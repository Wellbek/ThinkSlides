"""
The region tree: a slide's own composition, independent of any placeholder.

A slide's content has always bound to a placeholder index, and a placeholder
is a box in one of eight fixed layouts. That is enough for "a title and some
bullets" and it cannot express any of the shapes a consultancy deck is built
from - a dark rail one third wide holding the title and a takeaway, a canvas
split white/navy with content bound to a half, seven ruled row bands each
subdivided into icon, label and prose.

So a slide may now declare *regions*, and a content block may bind to one by
id. Geometry is fractional against the parent rather than absolute, so a
region tree written against one canvas survives a change of canvas - the
reference deck happens to share ours exactly, and relying on that would be
relying on luck.

Nothing here is required. A slide with no region tree resolves to no tree at
all, every caller takes the path it took before, and the placeholder binding
is untouched. That is the point: this lands additively or it does not land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from app.renderer.layout import PositionConfig

# The vocabulary a region can declare itself as. Kinds carry no geometry of
# their own - they say what a region *is*, which is what lets the extractor
# (#118) and the block renderers reason about it without re-deriving intent
# from coordinates.
REGION_KINDS = frozenset({
    "root",      # the whole canvas, when a tree wants an explicit parent
    "rail",      # a narrow full-height band with its own ground
    "panel",     # a major area of the canvas, usually beside a rail or split
    "column",    # one column of a grid
    "bandRow",   # the container of a set of row bands
    "band",      # one row band
    "banner",    # a full-width strip, usually ruled above and below
    "grid",      # the container of a set of columns or cards
})

# Siblings overlapping by less than this fraction of the smaller one are
# touching, not overlapping - fractional geometry rounds, and a hairline of
# shared edge between two halves of a split is not a defect.
OVERLAP_TOLERANCE = 0.02
# ...and the same slack applies to a child sitting inside its parent.
CONTAINMENT_TOLERANCE = 0.005


@dataclass(frozen=True)
class Region:
    """One declared area of a slide, in fractions of its parent."""
    id: str
    kind: str
    x: float
    y: float
    w: float
    h: float
    ground: Optional[str] = None
    children: tuple["Region", ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "Region":
        children = tuple(
            cls.from_dict(child)
            for child in (data.get("children") or [])
            if isinstance(child, dict)
        )
        return cls(
            id=str(data.get("id") or "").strip(),
            kind=str(data.get("kind") or "panel").strip() or "panel",
            x=_as_fraction(data.get("x"), 0.0),
            y=_as_fraction(data.get("y"), 0.0),
            w=_as_fraction(data.get("w"), 1.0),
            h=_as_fraction(data.get("h"), 1.0),
            ground=_as_hex(data.get("ground")),
            children=children,
        )


@dataclass(frozen=True)
class ResolvedRegion:
    """A region with its absolute rectangle and its inherited ground."""
    region: Region
    rect: PositionConfig
    ground: Optional[str]
    depth: int
    parent_id: Optional[str] = None

    @property
    def id(self) -> str:
        return self.region.id

    @property
    def kind(self) -> str:
        return self.region.kind


@dataclass
class RegionTree:
    """A slide's regions, resolved onto the canvas once."""
    canvas: PositionConfig
    resolved: dict[str, ResolvedRegion] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    @classmethod
    def from_slide(
        cls,
        slide_data: dict,
        canvas_width_in: float,
        canvas_height_in: float,
    ) -> Optional["RegionTree"]:
        """
        Build the tree for a slide, or None when the slide declares none.

        Returning None rather than an empty tree is deliberate: every caller
        branches on "is there a tree", and an empty-but-present tree would
        make the region-free path look like a special case of the region path
        when it is meant to be the same code it always was.
        """
        raw = slide_data.get("regions")
        if not isinstance(raw, list) or not raw:
            return None

        canvas = PositionConfig(left=0.0, top=0.0,
                                width=canvas_width_in, height=canvas_height_in)
        tree = cls(canvas=canvas)
        tree._absorb(
            [Region.from_dict(r) for r in raw if isinstance(r, dict)],
            canvas, None, 0, None,
        )
        return tree if tree.resolved else None

    def _absorb(self, regions: Sequence[Region], parent_rect: PositionConfig,
                parent_ground: Optional[str], depth: int,
                parent_id: Optional[str]) -> None:
        for region in regions:
            if not region.id or region.id in self.resolved:
                # An unnamed or duplicated region cannot be bound to, so it
                # would only ever be dead geometry. Validation reports it;
                # rendering skips it rather than drawing something no block
                # can reach.
                continue
            rect = PositionConfig(
                left=parent_rect.left + region.x * parent_rect.width,
                top=parent_rect.top + region.y * parent_rect.height,
                width=region.w * parent_rect.width,
                height=region.h * parent_rect.height,
            )
            ground = region.ground or parent_ground
            self.resolved[region.id] = ResolvedRegion(
                region=region, rect=rect, ground=ground, depth=depth,
                parent_id=parent_id,
            )
            self.order.append(region.id)
            if region.children:
                self._absorb(region.children, rect, ground, depth + 1, region.id)

    def __bool__(self) -> bool:
        return bool(self.resolved)

    def __contains__(self, region_id: object) -> bool:
        return region_id in self.resolved

    def get(self, region_id: Optional[str]) -> Optional[ResolvedRegion]:
        if not region_id:
            return None
        return self.resolved.get(str(region_id))

    def rect(self, region_id: Optional[str]) -> Optional[PositionConfig]:
        resolved = self.get(region_id)
        return resolved.rect if resolved else None

    def ground(self, region_id: Optional[str]) -> Optional[str]:
        resolved = self.get(region_id)
        return resolved.ground if resolved else None

    def ids(self) -> list[str]:
        return list(self.order)

    def grounded(self) -> list[ResolvedRegion]:
        """
        Regions declaring a ground of their own, outermost first.

        Painting order matters and is the caller's problem to get right, so
        it is fixed here rather than left to dict ordering: a rail inside a
        panel has to be painted after the panel or it disappears under it.
        """
        return sorted(
            (r for r in self.resolved.values() if r.region.ground),
            key=lambda r: (r.depth, self.order.index(r.id)),
        )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_regions(
    raw_regions: object,
    bound_region_ids: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """
    Check a slide's region tree, returning ``(errors, warnings)``.

    Three failures have to be caught before render rather than discovered in
    the PPTX, because all three produce a file that opens and is wrong:
    siblings that overlap (content drawn over content), a child escaping its
    parent (content outside the area it was composed into), and a block bound
    to a region that does not exist (content silently dropped).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if raw_regions is None:
        return errors, warnings
    if not isinstance(raw_regions, list):
        return [f"'regions' must be an array, got {type(raw_regions).__name__}"], warnings

    seen: set[str] = set()
    _validate_level(raw_regions, "", seen, errors, warnings)

    missing = sorted({str(rid) for rid in bound_region_ids if rid} - seen)
    for region_id in missing:
        errors.append(f"content is bound to region {region_id!r}, which the slide does not declare")
    return errors, warnings


def _validate_level(raw: Sequence[object], path: str, seen: set[str],
                    errors: list[str], warnings: list[str]) -> None:
    boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    for index, data in enumerate(raw):
        where = f"{path}[{index}]" if path else f"region {index}"
        if not isinstance(data, dict):
            errors.append(f"{where}: must be an object, got {type(data).__name__}")
            continue

        region_id = str(data.get("id") or "").strip()
        if not region_id:
            errors.append(f"{where}: missing 'id'")
        elif region_id in seen:
            errors.append(f"{where}: duplicate region id {region_id!r}")
        else:
            seen.add(region_id)

        kind = str(data.get("kind") or "").strip()
        if kind and kind not in REGION_KINDS:
            warnings.append(
                f"{where}: unknown region kind {kind!r} - it will render as a plain area"
            )

        box = tuple(_as_fraction(data.get(key), default)
                    for key, default in (("x", 0.0), ("y", 0.0), ("w", 1.0), ("h", 1.0)))
        x, y, w, h = box
        if w <= 0 or h <= 0:
            errors.append(f"{where}: region has no area (w={w}, h={h})")
        if (x < -CONTAINMENT_TOLERANCE or y < -CONTAINMENT_TOLERANCE
                or x + w > 1 + CONTAINMENT_TOLERANCE
                or y + h > 1 + CONTAINMENT_TOLERANCE):
            errors.append(
                f"{where}: region escapes its parent "
                f"(x={x}, y={y}, w={w}, h={h}; must fit within 0..1)"
            )
        boxes.append((region_id or where, box))

        children = data.get("children")
        if children:
            if not isinstance(children, list):
                errors.append(f"{where}: 'children' must be an array")
            else:
                _validate_level(children, where, seen, errors, warnings)

    for i in range(len(boxes)):
        name_i, box_i = boxes[i]
        for j in range(i + 1, len(boxes)):
            name_j, box_j = boxes[j]
            overlap = _overlap_fraction(box_i, box_j)
            if overlap > OVERLAP_TOLERANCE:
                errors.append(
                    f"regions {name_i!r} and {name_j!r} overlap by "
                    f"{overlap:.0%} of the smaller one"
                )


def content_region_ids(contents: object) -> list[str]:
    """Every ``regionId`` a slide's content blocks bind to."""
    if not isinstance(contents, list):
        return []
    out: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        region_id = item.get("regionId")
        if region_id:
            out.append(str(region_id))
    return out


def _overlap_fraction(a: tuple[float, float, float, float],
                      b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min(aw * ah, bw * bh)
    return intersection / smaller if smaller > 0 else 0.0


def _as_fraction(value: object, default: float) -> float:
    try:
        number = float(value)                        # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _as_hex(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    return text.upper() if len(text) == 7 else None
