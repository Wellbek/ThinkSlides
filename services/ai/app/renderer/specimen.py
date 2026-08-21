"""
What a design system looks like, before there is a deck to look at.

Preview PNGs exist as a by-product of a finished deck: render the PPTX,
convert it through LibreOffice, rasterise the pages. To show a user three
candidate design systems *before* anything is generated, there has to be a way
to preview a design system that has no deck behind it.

So: a specimen deck. Three slides that exercise the parts of a design system a
user needs to see -

1. the **cover**, for the display face and how the brand tone is deployed;
2. the **argument**, three parallel points - the slide that actually
   distinguishes directions;
3. the **evidence**, for the data-viz tokens: series colours, axis treatment,
   labels, table banding.

**The copy is the same in every direction and the shape is not.** Holding the
content constant is what makes three previews a comparison of design rather
than a comparison of writing - but "design" was being read as colour alone,
and three directions rendering the identical bulleted list in three palettes
is a comparison of hue. So the same three points come out as bullets in one
direction, as a row of figures in another, as ruled bands in a third. Same
words, same numbers, different shape (see ``ARGUMENT_BUILDERS``).

The copy is written for the actual brief rather than fixed lorem ipsum, so a
preview changes when the topic does. Its figures are illustrative, and a slide
about the user's own subject carrying numbers nobody researched has to say
what it is: every specimen content slide is sectioned ``Sample layout``, and
the card that offers the directions says the same thing in words above them.

And they go through the real ``PPTXRenderer``, not an approximation of it, so
a design system that renders badly previews badly. A preview that flatters a
direction the deck will not honour is worse than no preview.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app.agent.tools._today import recent_years
from app.observability.logging import get_logger
from app.renderer.preview_generator import PreviewGenerator

logger = get_logger(__name__)

# Where a direction's specimens live under the previews root. Throwaway by
# design: a run that never chooses a direction must not leave nine PNGs
# behind, so the whole directory goes when the session ends or a direction is
# chosen.
DIRECTION_DIR = "direction"
SPECIMEN_ZOOM = 2.0

# The section every specimen content slide is in. The copy is about the user's
# real topic and the figures in it were invented to fill a layout, so it says
# so wherever the direction's own chrome shows a section - and the card that
# offers the directions says it in words either way.
SECTION_LABEL = "Sample layout"

# How many of each the specimen deck can carry. More points than this stop
# fitting the shapes that show them as one row; more categories make the
# evidence table too narrow to read at thumbnail size.
MAX_POINTS = 3
MAX_CATEGORIES = 5
MAX_SERIES = 2


@dataclass(frozen=True)
class SpecimenPoint:
    """One of the three parallel points every specimen argument is built on."""
    label: str
    figure: str
    prose: str
    icon: str = "insight"


@dataclass(frozen=True)
class SpecimenContent:
    """
    The words and numbers a specimen deck is made of, for one brief.

    One set per run, shared by all three directions. That sharing is the
    whole point: what the user is comparing is what each direction *does*
    with the same material.
    """
    title: str
    subtitle: str
    headline: str
    points: tuple[SpecimenPoint, ...]
    evidence_headline: str
    takeaway: str
    chart_title: str
    categories: tuple[str, ...]
    series: tuple[tuple[str, tuple[float, ...]], ...]

    @classmethod
    def from_dict(cls, data: Any) -> "SpecimenContent":
        """
        A model-authored content set, held to the shape a specimen needs.

        Anything missing or unusable falls back to the corresponding default
        field rather than failing: a preview is worth showing with borrowed
        copy, and a run that cannot preview its directions cannot offer them.
        """
        fallback = default_content()
        if not isinstance(data, dict):
            return fallback

        points = tuple(
            SpecimenPoint(
                label=_text(entry.get("label")) or fallback.points[index].label,
                figure=_text(entry.get("figure")) or fallback.points[index].figure,
                prose=_text(entry.get("prose")) or fallback.points[index].prose,
                icon=_text(entry.get("icon")) or fallback.points[index].icon,
            )
            for index, entry in enumerate(
                [e for e in (data.get("points") or []) if isinstance(e, dict)][:MAX_POINTS]
            )
        )
        chart = data.get("chart") if isinstance(data.get("chart"), dict) else {}
        categories = tuple(
            _text(c) for c in (chart.get("categories") or []) if _text(c)
        )[:MAX_CATEGORIES]
        series = _series(chart.get("series"), len(categories))

        return cls(
            title=_text(data.get("title")) or fallback.title,
            subtitle=_text(data.get("subtitle")) or fallback.subtitle,
            headline=_text(data.get("headline")) or fallback.headline,
            # Three parallel points is the shape every structure is built for;
            # two would leave a card row with a hole in it.
            points=points if len(points) == MAX_POINTS else fallback.points,
            evidence_headline=(_text(data.get("evidenceHeadline"))
                               or fallback.evidence_headline),
            takeaway=_text(data.get("takeaway")) or fallback.takeaway,
            chart_title=_text(chart.get("title")) or fallback.chart_title,
            categories=categories if series else fallback.categories,
            series=series or fallback.series,
        )


def default_content() -> SpecimenContent:
    """
    The specimen copy when the brief could not be turned into any.

    Deliberately ordinary rather than lorem ipsum: a user comparing three
    designs is reading the shapes, and a page of Latin makes every shape look
    like the same grey block. The ``Sample layout`` eyebrow is what keeps it
    honest, not the unreadability of the words.
    """
    return SpecimenContent(
        title="The year ahead",
        subtitle="Where the next phase of growth comes from",
        headline="Three shifts decide the next twelve months",
        points=(
            SpecimenPoint(
                label="Adoption", figure="24%",
                prose="of the segment now runs it in production",
                icon="adoption",
            ),
            SpecimenPoint(
                label="Cost", figure="$1.8m",
                prose="of annual run-rate released by the second phase",
                icon="cost",
            ),
            SpecimenPoint(
                label="Speed", figure="6 weeks",
                prose="from decision to a working deployment",
                icon="speed",
            ),
        ),
        evidence_headline="Growth held in every one of the five measured years",
        takeaway="Both lines have compounded since the programme began",
        chart_title="Measured segments, percent",
        # Dated from today rather than written down: these specimen slides are
        # shown to the user when they pick a direction, and a sample chart
        # whose axis stops years ago reads as a stale product.
        categories=tuple(recent_years(5)),
        series=(
            ("Core", (18.0, 27.0, 36.0, 45.0, 54.0)),
            ("Adjacent", (12.0, 15.0, 21.0, 26.0, 33.0)),
        ),
    )


def specimen_slides(structure: str = "list",
                    content: Optional[SpecimenContent] = None) -> list[dict]:
    """
    The three specimen slides, as slide JSON the renderer already understands.

    ``structure`` is the direction's own answer to "what shape does a set of
    parallel points take here" - the axis that makes three previews three
    different slides rather than three palettes of one. An unrecognised value
    falls back to the plain list rather than raising: a direction that names a
    shape we do not have still deserves a preview.

    Built without a region tree, so a specimen renders under *any* design
    system including one whose grammar declares none. Blocks that name no
    region are allocated a band of the content area, which is exactly what a
    specimen wants.
    """
    content = content or default_content()
    argument, evidence = STRUCTURES_BY_NAME.get(
        str(structure or "").strip().lower(), STRUCTURES_BY_NAME["list"]
    )
    return [
        _cover(content),
        {
            "slideIndex": 1,
            "layoutId": "content",
            "layoutName": "Content",
            "section": SECTION_LABEL,
            "titleRegister": "claim",
            "contents": [_title(content.headline)] + argument(content),
        },
        {
            "slideIndex": 2,
            "layoutId": "chart",
            "layoutName": "Chart",
            "section": SECTION_LABEL,
            "titleRegister": "claim",
            "contents": [_title(content.evidence_headline)] + evidence(content),
        },
    ]


# ── The three slides ──────────────────────────────────────────────────

def _cover(content: SpecimenContent) -> dict:
    return {
        "slideIndex": 0,
        "layoutId": "title",
        "layoutName": "Title Slide",
        "titleRegister": "label",
        "contents": [
            {"type": "text", "placeholderIdx": 0,
             "runs": [{"text": content.title, "bold": True}]},
            {"type": "text", "placeholderIdx": 1,
             "runs": [{"text": content.subtitle, "bold": False}]},
        ],
    }


def _title(text: str) -> dict:
    return {"type": "text", "placeholderIdx": 0,
            "runs": [{"text": text, "bold": True}]}


# ── The argument, four ways ───────────────────────────────────────────
#
# Every one of these carries all three points, every point's label, its figure
# and its prose. What differs is the shape they are poured into - which is the
# thing three previews exist to let a user choose between.

def _argument_list(content: SpecimenContent) -> list[dict]:
    """Bullets. The plain shape, and the one a written report wants."""
    return [{"type": "text", "placeholderIdx": 1, "runs": [
        {"text": f"{point.label} - {point.figure} {point.prose}",
         "bullet": True, "level": 0}
        for point in content.points
    ]}]


def _argument_stats(content: SpecimenContent) -> list[dict]:
    """A row of oversized figures. The shape an executive summary wants."""
    return [{"type": "statCallout", "statCallouts": [
        {"value": point.figure, "caption": f"{point.label} - {point.prose}"}
        for point in content.points
    ]}]


def _argument_cards(content: SpecimenContent) -> list[dict]:
    """Accent-barred cards in a row. The shape a set of parallel themes wants."""
    return [{"type": "accentCard", "columns": len(content.points),
             "accentCards": [
                 {"heading": point.label, "tone": "benefit",
                  "body": [f"{point.figure} {point.prose}"]}
                 for point in content.points
             ]}]


def _argument_bands(content: SpecimenContent) -> list[dict]:
    """Ruled rows of icon, label and prose. The shape a dense analyst deck wants."""
    return [{"type": "rowBand", "rowBands": [
        {"icon": point.icon, "label": point.label,
         "prose": f"{point.figure} {point.prose}"}
        for point in content.points
    ]}]


# ── The evidence, four ways ───────────────────────────────────────────
#
# The same figures every time - the chart is what shows a direction's data-viz
# tokens, so it never goes away. What changes is what stands beside it.

def _chart(content: SpecimenContent) -> dict:
    return {"type": "chart", "chart": {
        "chartType": "bar",
        "title": content.chart_title,
        "categories": list(content.categories),
        "series": [{"name": name, "values": list(values)}
                   for name, values in content.series],
    }}


def _evidence_list(content: SpecimenContent) -> list[dict]:
    """The chart on its own."""
    return [_chart(content)]


def _evidence_stats(content: SpecimenContent) -> list[dict]:
    """What the chart's own numbers come to, called out above it."""
    name, values = content.series[0]
    first, last = content.categories[0], content.categories[-1]
    return [
        {"type": "statCallout", "statCallouts": [
            {"value": _change(values[0], values[-1]),
             "caption": f"{name}, {first} to {last}"},
            {"value": _figure(values[-1]), "caption": f"{name} in {last}"},
        ]},
        _chart(content),
    ]


def _evidence_cards(content: SpecimenContent) -> list[dict]:
    """A claim strip over the chart."""
    return [{"type": "banner", "banner": {"text": content.takeaway}},
            _chart(content)]


def _evidence_bands(content: SpecimenContent) -> list[dict]:
    """The chart, and the same numbers again as a table."""
    return [
        _chart(content),
        {"type": "table", "table": {"headerRow": True, "rows": [
            ["", *content.categories],
            *[[name, *[_figure(value) for value in values]]
              for name, values in content.series],
        ]}},
    ]


# What each structure does with the argument and with the evidence. The keys
# are ``directions.STRUCTURES``, and a test holds the two to each other - a
# direction naming a shape nothing here builds would silently preview as a
# bulleted list, which is the failure this module exists to end.
ARGUMENT_BUILDERS = {
    "list": _argument_list,
    "stats": _argument_stats,
    "cards": _argument_cards,
    "bands": _argument_bands,
}
EVIDENCE_BUILDERS = {
    "list": _evidence_list,
    "stats": _evidence_stats,
    "cards": _evidence_cards,
    "bands": _evidence_bands,
}
STRUCTURES_BY_NAME = {
    name: (builder, EVIDENCE_BUILDERS[name])
    for name, builder in ARGUMENT_BUILDERS.items()
}


# ── Rendering ─────────────────────────────────────────────────────────

def render_specimens(
    design_system: dict,
    session_id: str,
    direction_id: str,
    output_dir: Optional[str] = None,
    content: Optional[SpecimenContent] = None,
) -> list[str]:
    """
    Render the specimen deck under ``design_system``, returning preview URLs.

    The structure comes off the design system itself rather than being passed
    separately, so a direction cannot be rendered in one shape and built in
    another.

    One LibreOffice conversion for the whole three-slide deck rather than one
    per slide: conversion is the slow step by an order of magnitude, and the
    difference between three conversions and nine decides whether this fits
    inside a chat pause.
    """
    from app.renderer.pptx_renderer import PPTXRenderer

    generator = PreviewGenerator(output_dir)
    target = _direction_dir(generator.output_dir, session_id, direction_id)
    os.makedirs(target, exist_ok=True)

    work_dir = tempfile.mkdtemp(prefix=f"ts_specimen_{direction_id}_")
    try:
        pptx_path = os.path.join(work_dir, "specimen.pptx")
        PPTXRenderer(
            template_path=None,
            color_theme=str((design_system or {}).get("colorTheme") or "") or None,
            design_system=design_system,
        ).render(
            slides=specimen_slides(
                str((design_system or {}).get("structure") or "list"), content
            ),
            metadata={"title": str((design_system or {}).get("name")
                                   or "Design direction")},
            output_path=pptx_path,
        )
        pdf_path = generator.convert_to_pdf(pptx_path, work_dir)
        if not pdf_path:
            logger.error("specimen_conversion_failed", direction_id=direction_id)
            return []
        urls = _rasterise(pdf_path, target, session_id, direction_id)
        logger.info("specimens_rendered", direction_id=direction_id,
                    session_id=session_id, count=len(urls))
        return urls
    except Exception as e:  # pragma: no cover - a direction that will not render
        logger.error("specimen_failed", direction_id=direction_id, error=str(e))
        return []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def render_directions(
    directions: Sequence[dict],
    session_id: str,
    output_dir: Optional[str] = None,
    content: Optional[SpecimenContent] = None,
) -> dict[str, list[str]]:
    """
    Render every direction's specimens at once, keyed by direction id.

    One content set for all of them, so what the user compares is the design.

    Concurrently, because this sits inside a chat pause and the user is
    watching: three conversions in sequence is three times the wait for no
    reason. Each LibreOffice runs against its own ``UserInstallation`` profile
    - the isolation that already exists in ``convert_to_pdf`` is exactly what
    makes concurrent instances safe.

    One direction failing to render does not take the others with it. A user
    offered two of three directions has still been offered a choice; a user
    offered an error has not.
    """
    async def one(direction: dict) -> tuple[str, list[str]]:
        direction_id = str(direction.get("id") or "")
        urls = await asyncio.to_thread(
            render_specimens, direction.get("designSystem") or {},
            session_id, direction_id, output_dir, content,
        )
        return direction_id, urls

    results = await asyncio.gather(
        *(one(direction) for direction in directions if direction.get("id")),
        return_exceptions=True,
    )
    rendered: dict[str, list[str]] = {}
    for result in results:
        if isinstance(result, BaseException):
            logger.error("specimen_direction_failed", error=str(result))
            continue
        direction_id, urls = result
        rendered[direction_id] = urls
    return rendered


def cleanup_directions(session_id: str, output_dir: Optional[str] = None,
                       keep: Optional[str] = None) -> None:
    """
    Remove a session's direction specimens.

    Called when a direction is chosen and again when the session ends. They
    are throwaway by design, and an abandoned run that leaves nine PNGs behind
    is nine per abandoned run for ever.
    """
    root = os.path.join(
        (PreviewGenerator(output_dir).output_dir), session_id, DIRECTION_DIR
    )
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        if keep and name == keep:
            continue
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    if not os.listdir(root):
        shutil.rmtree(root, ignore_errors=True)
    logger.info("direction_previews_cleaned", session_id=session_id, kept=keep)


def specimen_urls(session_id: str, direction_id: str,
                  output_dir: Optional[str] = None) -> list[str]:
    """The specimen URLs already on disk for a direction, in order."""
    target = _direction_dir(PreviewGenerator(output_dir).output_dir,
                            session_id, direction_id)
    if not os.path.isdir(target):
        return []
    names = sorted(
        (n for n in os.listdir(target) if n.endswith(".png")),
        key=lambda n: int(os.path.splitext(n)[0]) if n[:-4].isdigit() else 0,
    )
    return [f"/previews/{session_id}/{DIRECTION_DIR}/{direction_id}/{n}"
            for n in names]


# ── Internals ─────────────────────────────────────────────────────────

def _direction_dir(root: str, session_id: str, direction_id: str) -> str:
    return os.path.join(root, session_id, DIRECTION_DIR, direction_id)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _figure(value: float) -> str:
    """A chart value as it reads in a callout or a table cell."""
    return f"{value:g}"


def _change(first: float, last: float) -> str:
    """
    What a series came to, as a figure that needs no unit beside it.

    A multiple where there is one to state, and a signed difference where
    there is not - the callout sits above the chart it was derived from, and
    a bare restated value would only be one of its bars said twice.
    """
    if first > 0 and last >= first:
        return f"{last / first:.1f}x".replace(".0x", "x")
    return f"{last - first:+g}"


def _series(raw: Any, category_count: int) -> tuple[tuple[str, tuple[float, ...]], ...]:
    """
    The chart's series, dropping any that does not line up with the categories.

    A series shorter than its categories renders as a chart with holes in it,
    and a specimen showing a broken chart says the design system is broken
    rather than that the model miscounted.
    """
    if not category_count:
        return ()
    out: list[tuple[str, tuple[float, ...]]] = []
    for entry in (raw or []):
        if not isinstance(entry, dict):
            continue
        try:
            values = tuple(float(v) for v in (entry.get("values") or []))
        except (TypeError, ValueError):
            continue
        name = _text(entry.get("name"))
        if name and len(values) == category_count:
            out.append((name, values))
    return tuple(out[:MAX_SERIES])


def _rasterise(pdf_path: str, target: str, session_id: str,
               direction_id: str) -> list[str]:
    import fitz

    urls: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        matrix = fitz.Matrix(SPECIMEN_ZOOM, SPECIMEN_ZOOM)
        for index in range(len(doc)):
            name = f"{index + 1}.png"
            doc[index].get_pixmap(matrix=matrix, alpha=False).save(
                os.path.join(target, name)
            )
            urls.append(
                f"/previews/{session_id}/{DIRECTION_DIR}/{direction_id}/{name}"
            )
    finally:
        doc.close()
    return urls


def describe(design_system: dict) -> dict[str, Any]:
    """
    The few facts about a design system a preview cannot show at thumbnail
    size, for logging and for the distinctness check in #113.
    """
    typography = (design_system or {}).get("typography") or {}
    density = (design_system or {}).get("density") or {}
    return {
        "displayFace": (typography.get("titleFont") or {}).get("typeface"),
        "bodyFace": (typography.get("bodyFont") or {}).get("typeface"),
        "displayIsSerif": bool(typography.get("displayIsSerif")),
        "structure": (design_system or {}).get("structure"),
        "wordsMedian": (density.get("wordsPerSlide") or {}).get("median"),
        "blocksMedian": (density.get("blocksPerSlide") or {}).get("median"),
    }
