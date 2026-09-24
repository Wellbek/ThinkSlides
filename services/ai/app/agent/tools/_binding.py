"""
Where a built block goes on its slide, and which of several it is.

Two arguments answer that, and every builder that produces a content block
takes them. They live here rather than in one builder because a model that
has learned an argument on ``build_stat_callouts`` should not have to relearn
it on ``build_chart``: one description, stated once, used everywhere.

``region_id`` is placement. A slide that declares a region tree binds each
block to an area of it; a slide that does not renders in its placeholders as
it always has, and omitting the argument is how that is said.

``slot`` is identity. Blocks are held one per slot while a slide is being
built, so that rebuilding one replaces exactly that one - which is what the
revision loop depends on when validation sends a single exhibit back. Region
alone was enough while a slide carried one chart. A dashboard page carries a
bridge and a mix chart, and on a slide with no region tree both bind to the
same nothing; without a slot to tell them apart the second silently replaces
the first, the tool still reports success, and the exhibit never reaches the
page.
"""

from __future__ import annotations

import re

from app.agent.registry import ToolParameter


def region_param() -> ToolParameter:
    return ToolParameter(
        name="region_id", type="string", required=False,
        description=(
            "The id of the slide region this block is bound to, when the slide "
            "declares a region tree. Omit on a slide that does not."
        ),
    )


def slot_param(examples: str) -> ToolParameter:
    """
    The slot argument, illustrated with names from the calling tool's domain.

    The examples are the parameter's whole teaching value: "a short id" is
    abstract enough that a model reads it as optional bookkeeping, whereas
    "bridge" and "mix" state what it is for in the terms of the thing being
    built.
    """
    return ToolParameter(
        name="slot", type="string", required=False,
        description=(
            "A short id distinguishing this from another of the same type on "
            f"the same slide, e.g. {examples}. Rebuilding the same slot "
            "replaces that one; a different slot adds one while a slide is "
            "first drafted. When revising a slide, a block of a type it "
            "already carries replaces the earlier one unless its slot names "
            "another. Omit when the slide carries only one."
        ),
    )


def bind(content: dict, region_id: str = "", slot: str = "") -> dict:
    """Stamp placement and identity onto a block, omitting what was not given."""
    if (region_id or "").strip():
        content["regionId"] = region_id.strip()
    if (slot or "").strip():
        content["slot"] = slot.strip()
    return content


def slot_key(block: dict) -> tuple:
    """
    The identity a prepared block is held under.

    One function rather than the tuple written out at each site: the loop
    stores blocks under this key and ``resolve_and_add_slide`` uses the same
    key to drop the inline content a model wrote in favour of the validated
    block. The two disagreeing is how a slide ends up carrying an exhibit
    twice, so they read it from here.

    The slot is compared loosely - case and separators aside - because a model
    restating a name turns after it first wrote it does not restate it
    exactly: a revision rebuilt "decision-matrix" as "decision_matrix" and the
    page shipped both tables.
    """
    raw = str(block.get("slot") or "").strip()
    # Separators only: a slot named in Chinese or Cyrillic is still a name.
    slot = re.sub(r"[\W_]+", "", raw.casefold()) or raw
    return (block.get("type"), block.get("regionId"), slot or None)
