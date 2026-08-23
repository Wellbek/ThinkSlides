"""
What counts as legible, per design language rather than globally.

``typography.py`` enforced ``MIN_BODY_PT = 16`` on every deck, commented "a
template that specifies 11pt body copy is describing a document, not a
slide". Measured as a share of canvas height, the four reference
publications set body copy at 2.6%, 2.2%, 1.5% and 1.3% - and that floor is
3.0%. It was above every one of them. It did not enforce legibility; it
forbade the registers the system is trying to reach.

The floor belongs to the pack, because legibility is not a property of a
point size. Ten point on a 792-point page is smaller type than twelve point
on a 540-point one, and both are correct for the publication that chose
them. Each pack carries the ratios its own body copy implies, and this is
where a size is checked against them.
"""
from __future__ import annotations

from typing import Optional

# What a role is measured against. Anything not named here is held to the
# caption floor, which is the most permissive of the three.
ROLE_KIND = {
    "body": "body", "column": "body", "standfirst": "body",
    "headline": "display", "pullquote": "display", "folio": "display",
    "caption": "caption", "footnote": "caption", "eyebrow": "caption",
    "sourceLine": "caption",
}


def floor_for(pack, role_name: str) -> float:
    """The smallest size this pack considers readable for this role."""
    kind = ROLE_KIND.get(role_name, "caption")
    return pack.policy.floor_pt(kind, pack.canvas)


def check(pack, role_name: str, size_pt: float) -> Optional[str]:
    """Why this size is not legible in this pack, or None if it is.

    Returns a sentence rather than a boolean because the caller's job when
    this fails is to explain itself: a page that cannot fit its copy at a
    legible size has a composition problem, and shrinking the type past the
    floor hides it rather than solving it.
    """
    floor = floor_for(pack, role_name)
    if size_pt >= floor:
        return None
    page_pt = pack.canvas.h_in * 72.0
    return (
        f"{role_name} at {size_pt:.1f}pt is below this pack's floor of "
        f"{floor:.1f}pt ({size_pt / page_pt:.2%} of the page against a "
        f"minimum of {floor / page_pt:.2%}); the copy does not fit the "
        f"composition rather than the type being too large"
    )


def largest_legible(pack, role_name: str, candidates) -> Optional[float]:
    """The largest candidate size that this pack still considers readable."""
    floor = floor_for(pack, role_name)
    legible = [size for size in candidates if size >= floor]
    return max(legible) if legible else None
