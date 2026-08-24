"""
Which type role a piece of material takes in a given frame.

One function, imported by both the binder and the fit cost, because they
have to agree. When they did not, the cost was measured with the body role
while the binder set the frame's own role - so a paragraph bound into a
frame the reference used for a 30pt standfirst was *measured* at 16pt and
*drawn* at 30pt. The fit reported no overflow and the page ran off the
bottom, which is the worst kind of disagreement: one that makes the metric
confidently wrong rather than merely imprecise.
"""
from __future__ import annotations

# Frame role -> the type role its text is set in.
FRAME_TYPE_ROLE = {
    "headline": "headline",
    "standfirst": "standfirst",
    "pullquote": "pullquote",
    "eyebrow": "eyebrow",
    "folio": "folio",
    "caption": "caption",
    "footnotes": "footnote",
    "sourceLine": "caption",
}


def type_role_for(frame_role: str, item_kind: str, pack) -> str:
    """The pack type role this item takes in a frame of this role.

    A quote is always set as a pull-quote where the pack has one; otherwise
    the frame decides, falling back to body when the pack does not define
    the role the frame asks for.
    """
    if item_kind == "quote" and "pullquote" in pack.type_roles:
        return "pullquote"
    wanted = FRAME_TYPE_ROLE.get(frame_role, "body")
    if wanted in pack.type_roles:
        return wanted
    return "body" if "body" in pack.type_roles else next(iter(pack.type_roles), "body")
