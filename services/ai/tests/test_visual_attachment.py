"""
Every builder's block reaches the slide it names.

The tools in ``BUILD_VISUAL_TOOLS`` and ``BLOCK_BUILDER_TOOLS`` each return a
block, and the loop attaches it in ``AgentLoop._attach_visual``. Two of them
returned that block under ``"blocks"`` rather than ``"content"`` for their
whole lives: ``draw_svg`` and ``build_mermaid``. Both reported success, both
logged a figure built, and neither figure ever reached a page - because the
attachment reads one key and they wrote another.

Every test they had asserted the tool's own return value, which is exactly
what a mismatch like that survives. So this drives each builder through the
real attachment and asserts the block lands in ``prepared_visuals``. A new
builder is added to ``CALLS`` or the test fails naming it, which is the point:
the next tool cannot repeat this quietly.
"""

import asyncio

import pytest

from app.agent.loop import AgentLoop
from app.agent.tools.build_blocks import BLOCK_BUILDER_TOOLS
from app.agent.tools.build_visuals import BUILD_VISUAL_TOOLS
from app.agent.registry import ToolRegistry, _register_default_tools

# One minimal, valid call per builder. Written out rather than synthesised
# from the schemas: a synthesised call that a tool rejects would make this
# test pass by never reaching the attachment at all.
CALLS: dict[str, dict] = {
    "build_chart": {
        "slide_index": 0, "chart_type": "bar",
        "categories": ["A", "B"],
        "series": [{"name": "Revenue (US$m)", "values": [1.0, 2.0]}],
    },
    "build_chart_grid": {
        "slide_index": 0, "panel_type": "line",
        "categories": ["2023", "2024"],
        "panels": [
            {"name": "North", "values": [1.0, 2.0]},
            {"name": "South", "values": [2.0, 3.0]},
        ],
    },
    "build_table": {
        "slide_index": 0, "columns": ["Segment", "Units"],
        "rows": [["North", "12"], ["South", "9"]],
    },
    "build_key_metrics": {
        "slide_index": 0,
        "metrics": [
            {"value": "12%", "label": "Growth"},
            {"value": "9x", "label": "Multiple"},
        ],
    },
    "build_diagram": {
        "slide_index": 0, "kind": "process",
        "items": [{"label": "Plan"}, {"label": "Build"}, {"label": "Ship"}],
    },
    "draw_svg": {
        "slide_index": 0,
        "svg": '<svg viewBox="0 0 100 60"><rect x="1" y="1" width="40" '
               'height="20" fill="#3366cc"/></svg>',
    },
    "build_mermaid": {
        "slide_index": 0,
        "source": "flowchart TD\n  A[Plan] --> B[Ship]",
    },
    "build_stat_callouts": {
        "slide_index": 0,
        "callouts": [{"value": "12%", "caption": "Growth year on year"},
                     {"value": "9x", "caption": "Revenue multiple"}],
    },
    "build_icon_cards": {
        "slide_index": 0,
        "cards": [{"icon": "check", "heading": "Scale", "body": ["First point."]},
                  {"icon": "star", "heading": "Margin", "body": ["Second point."]}],
    },
    "build_accent_cards": {
        "slide_index": 0,
        "cards": [{"heading": "Scale", "body": ["First point."], "tone": "positive"},
                  {"heading": "Margin", "body": ["Second point."], "tone": "negative"}],
    },
    "build_row_bands": {
        "slide_index": 0,
        "rows": [{"label": "Scale", "prose": "Volume carries the margin."},
                 {"label": "Mix", "prose": "Premium lines grew fastest."},
                 {"label": "Cost", "prose": "Freight fell back to trend."}],
    },
    "build_banner": {"slide_index": 0, "text": "One line that carries the page."},
    "build_legend": {
        "slide_index": 0,
        "items": [{"label": "North", "tone": "positive"},
                  {"label": "South", "tone": "negative"}],
    },
    "build_image_card_row": {
        "slide_index": 0,
        "cards": [{"imageUrl": "https://example.invalid/a.png", "title": "North"},
                  {"imageUrl": "https://example.invalid/b.png", "title": "South"}],
    },
    "compute_visual_with_code": {
        "slide_index": 0,
        "code": ("from PIL import Image\n"
                 "Image.new('RGB', (10, 10), (200, 0, 0)).save('figure.png')\n"),
        "intent": "a solid red square",
        "inputs": "none",
    },
    # The one exception, and it is a real one: primitives are marks rather
    # than a content block, and they attach to `prepared_primitives` through
    # their own branch. Listed here so the coverage check below still sees it.
    "draw_primitives": None,
}

ALL_BUILDERS = tuple(BUILD_VISUAL_TOOLS) + tuple(BLOCK_BUILDER_TOOLS)


def test_every_builder_is_covered():
    """A new builder joins CALLS or this names it."""
    assert set(ALL_BUILDERS) <= set(CALLS), set(ALL_BUILDERS) - set(CALLS)


def _run(name: str, args: dict):
    registry = ToolRegistry()
    _register_default_tools(registry)
    entry = registry.get(name)
    assert entry is not None, f"{name} is not registered"
    _definition, fn = entry
    return asyncio.run(fn(**args))


@pytest.mark.parametrize(
    "name", [n for n in ALL_BUILDERS if CALLS.get(n) is not None]
)
def test_a_built_block_reaches_the_slide_it_names(name):
    result = _run(name, CALLS[name])
    assert result.success, result.error

    loop = AgentLoop(db=None)
    loop._attach_visual(name, result)

    attached = loop.working_state.prepared_visuals.get(0) or []
    assert attached, f"{name} built a block that reached no slide"


@pytest.mark.parametrize(
    "name", [n for n in ALL_BUILDERS if CALLS.get(n) is not None]
)
def test_rebuilding_the_same_slot_replaces_rather_than_duplicates(name):
    """The dedup key has to see the block, which it cannot if attachment did
    not happen - so this fails the same way for the same bug."""
    result = _run(name, CALLS[name])
    assert result.success, result.error

    loop = AgentLoop(db=None)
    loop._attach_visual(name, result)
    loop._attach_visual(name, result)

    assert len(loop.working_state.prepared_visuals.get(0) or []) == 1


# ── Revision rounds ─────────────────────────────────────────────────────────
#
# A revising model rebuilds a flagged page's exhibits and names their slots
# afresh. The Kubernetes run (`20260924-203834-live`) built slide 2's table
# three times - "strategy-comparison" on the first draft, no slot after the
# first validation, "strategy_comparison" after the second - and shipped all
# three stacked, with the text run off the page.


def _table(slot: str | None = None, title: str = "") -> dict:
    block = {"type": "table", "table": {"title": title}}
    if slot:
        block["slot"] = slot
    return block


def _after_validation(loop: AgentLoop, rounds: int = 1) -> None:
    loop.working_state.validation_attempts += rounds


def test_the_kubernetes_run_ships_one_table_not_three():
    loop = AgentLoop(db=None)
    ws = loop.working_state
    ws.prepare_visual(1, _table("strategy-comparison", "first draft"))
    _after_validation(loop)
    ws.prepare_visual(1, _table(None, "first revision"))
    _after_validation(loop)
    ws.prepare_visual(1, _table("strategy_comparison", "second revision"))

    assert [b["table"]["title"] for b in ws.prepared_visuals[1]] == ["second revision"]


def test_a_slot_restated_with_other_separators_is_the_same_slot():
    loop = AgentLoop(db=None)
    ws = loop.working_state
    ws.prepare_visual(5, _table("decision-matrix", "old"))
    ws.prepare_visual(5, _table("decision_matrix", "new"))

    assert [b["table"]["title"] for b in ws.prepared_visuals[5]] == ["new"]


def test_a_first_draft_keeps_two_exhibits_of_one_type():
    loop = AgentLoop(db=None)
    ws = loop.working_state
    ws.prepare_visual(0, _table("costs"))
    ws.prepare_visual(0, _table("risks"))

    assert len(ws.prepared_visuals[0]) == 2


def test_a_revision_keeps_both_exhibits_it_rebuilds():
    loop = AgentLoop(db=None)
    ws = loop.working_state
    ws.prepare_visual(0, _table("costs", "old"))
    ws.prepare_visual(0, _table("risks", "old"))
    _after_validation(loop)
    ws.prepare_visual(0, _table("cost_table", "new"))
    ws.prepare_visual(0, _table("risk_table", "new"))

    assert [b["table"]["title"] for b in ws.prepared_visuals[0]] == ["new", "new"]


def test_a_named_fix_to_one_exhibit_keeps_the_other():
    loop = AgentLoop(db=None)
    ws = loop.working_state
    ws.prepare_visual(0, _table("costs", "old costs"))
    ws.prepare_visual(0, _table("risks", "old risks"))
    _after_validation(loop)
    ws.prepare_visual(0, _table("costs", "new costs"))

    assert sorted(b["table"]["title"] for b in ws.prepared_visuals[0]) == [
        "new costs", "old risks"]


def test_a_revision_leaves_other_types_and_regions_alone():
    loop = AgentLoop(db=None)
    ws = loop.working_state
    ws.prepare_visual(0, {"type": "diagram", "slot": "flow"})
    ws.prepare_visual(0, {**_table("left"), "regionId": "left"})
    ws.prepare_visual(0, {**_table("right"), "regionId": "right"})
    _after_validation(loop)
    ws.prepare_visual(0, {**_table("left-rebuilt"), "regionId": "left"})

    kept = [(b["type"], b.get("regionId"), b.get("slot")) for b in ws.prepared_visuals[0]]
    assert kept == [("diagram", None, "flow"), ("table", "right", "right"),
                    ("table", "left", "left-rebuilt")]

