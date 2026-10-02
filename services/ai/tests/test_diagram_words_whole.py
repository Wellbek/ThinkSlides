"""
A diagram's words are set whole, at one size, inside their boxes (#363).

Drawn once at the deck's body size, a four-step flow in a half-width slot
broke "ReplicaSet" into "ReplicaSe / t" and "maxUnavailable" into
"maxUnavai / lable", ran a detail out below its border, and was then shrunk
box by box by the renderer's autofit, so no two steps were the same size.
"""
import math

import pytest

from app.renderer import diagrams
from app.renderer.layout import PositionConfig
from app.renderer.typeset.fonts import width_pt

FLOW = {"diagram": {"kind": "flow", "title": "Incremental replacement", "items": [
    {"label": "Old ReplicaSet", "detail": "Service routes to current pods"},
    {"label": "Surge + readiness",
     "detail": "maxSurge / maxUnavailable govern replacement"},
    {"label": "New ReplicaSet", "detail": "Ready pods enter service incrementally"},
    {"label": "Rollback revision", "detail": "Reapply the prior Deployment revision"},
]}}

PALETTE = diagrams.DiagramPalette(
    ground="#FFFFFF", ink="#1A1A1A", heading="#1A1A1A", accent="#B3261E",
    rule="#808080", sequence=["#B3261E", "#2F6F6A", "#2F6F6A", "#3A2E2A"],
    font="Arial")


def _draw(width_in, height_in=3.0, label_pt=14.0, detail_pt=12.0):
    rect = PositionConfig(left=0.5, top=0.5, width=width_in, height=height_in)
    return diagrams.compile_diagram(FLOW, rect, palette=PALETTE,
                                    label_pt=label_pt, detail_pt=detail_pt)


def _texts(marks, part):
    return [m for m in marks if m.kind == "text" and m.id.endswith(part)]


@pytest.mark.parametrize("width_in", [4.7, 6.0, 11.7])
def test_no_word_is_broken(width_in):
    for mark in _draw(width_in):
        if mark.kind != "text":
            continue
        text = "".join(r.text for r in mark.runs)
        for word in text.replace("-", "- ").split():
            assert width_pt(word, "Arial", mark.size_pt,
                            bold=mark.bold) <= mark.box.width * 72.0 + 0.01, (
                f"{word!r} at {mark.size_pt}pt in {mark.box.width:.2f}in")


def test_every_step_is_set_at_one_size():
    marks = _draw(4.7)
    assert len({m.size_pt for m in _texts(marks, "-label")}) == 1
    assert len({m.size_pt for m in _texts(marks, "-detail")}) == 1


def test_the_renderer_does_not_resize_a_box_on_its_own():
    assert all(not m.autofit for m in _draw(4.7) if m.kind == "text")


def test_text_stays_inside_its_node():
    """`_reads` is the contract the fitting holds; it must hold at the end."""
    assert diagrams._reads(_draw(4.7))


def test_labels_of_a_row_start_together():
    labels = _texts(_draw(4.7), "-label")
    assert len({round(m.box.top, 4) for m in labels}) == 1


def test_a_wide_slot_keeps_the_decks_own_size():
    """Nothing gives way that does not have to."""
    labels = _texts(_draw(11.7), "-label")
    assert {m.size_pt for m in labels} == {14.0}


def test_a_hyphenated_compound_may_break_at_its_hyphen():
    """'stale-while-revalidate' breaking after a hyphen is ordinary setting,
    and must not drive the whole diagram down to the floor."""
    item = {"diagram": {"kind": "flow", "items": [
        {"label": "Stale, refetching", "detail": "stale-while-revalidate window"},
        {"label": "Fresh", "detail": "within max-age"},
        {"label": "Stale, origin down", "detail": "stale-if-error window"},
        {"label": "Must refetch", "detail": "after the windows"}]}}
    rect = PositionConfig(left=0.0, top=0.0, width=4.7, height=3.0)
    marks = diagrams.compile_diagram(item, rect, palette=PALETTE,
                                     label_pt=12.0, detail_pt=11.0)
    assert min(m.size_pt for m in _texts(marks, "-detail")) >= 10.0


class TestTheSelectorIsToldWhereItCanBeRead:
    def test_a_narrow_slot_cannot_hold_it_at_any_height(self):
        assert math.isinf(diagrams.needed_height_in(FLOW, 2.0, "Arial"))

    def test_a_wide_slot_needs_a_finite_height(self):
        needed = diagrams.needed_height_in(FLOW, 11.7, "Arial")
        assert 0.5 < needed < 4.0

    def test_unreadable_is_a_full_shortfall_not_a_perfect_fit(self):
        """inf / inf is NaN, and `max(0.0, nan)` said 0.0: a perfect fit."""
        from app.design import run as design_run
        from app.design.material import MaterialItem
        from app.design.select import legibility_shortfall

        pack = design_run.load("ey-iif-risk")
        item = MaterialItem(id="d", kind="diagram", payload=FLOW)
        assert legibility_shortfall(item, pack, (2.0, 6.0)) == 1.0
        assert legibility_shortfall(item, pack, (11.7, 6.0)) == 0.0


def test_a_typed_diagram_is_held_to_its_measured_floor_exactly():
    """Its floor is the diagram laid out at the size it is read at, like a
    table's - not an estimate that may be fallen short of by a quarter."""
    from app.design.material import MaterialItem
    from app.design.select import LEGIBLE_TOLERANCE, legible_tolerance

    typed = MaterialItem(id="d", kind="diagram", payload=FLOW)
    drawn = MaterialItem(id="s", kind="diagram",
                         payload={"diagram": {"format": "svg", "svg": "<svg/>"}})
    assert legible_tolerance(typed) == 0.0
    assert legible_tolerance(drawn) == LEGIBLE_TOLERANCE
