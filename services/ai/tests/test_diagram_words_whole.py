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


@pytest.mark.parametrize("width_in", [4.7, 6.0])
def test_every_step_is_set_at_one_size(width_in):
    marks = _draw(width_in)
    assert len({m.size_pt for m in _texts(marks, "-label")}) == 1
    assert len({m.size_pt for m in _texts(marks, "-detail")}) <= 1


def test_details_are_kept_where_they_read():
    assert len(_texts(_draw(6.0), "-detail")) == 4


def test_details_go_before_labels_go_below_legibility():
    """The module's rule: in a slot too narrow for the details at 10pt, the
    details give way and the labels stay readable."""
    marks = _draw(4.7)
    assert not _texts(marks, "-detail")
    assert min(m.size_pt for m in _texts(marks, "-label")) >= diagrams.LEGIBLE_PT


def test_the_renderer_does_not_resize_a_box_on_its_own():
    """Node text is sized once for the whole diagram. Titles and other
    free-standing text keep autofit as their guard."""
    marks = _draw(6.0)
    assert all(not m.autofit for m in _texts(marks, "-label") + _texts(marks, "-detail"))


def test_the_floor_counts_the_details():
    """A slot that shows the labels only is not offered: what the diagram
    was drawn to say would be lost."""
    assert math.isinf(diagrams.needed_height_in(FLOW, 4.7, "Arial"))
    assert math.isfinite(diagrams.needed_height_in(FLOW, 6.0, "Arial"))


@pytest.mark.parametrize("width_in", [4.7, 6.0, 11.7])
def test_text_stays_inside_its_node(width_in):
    """`_reads` is the contract the fitting holds; it must hold at the end."""
    assert diagrams._reads(_draw(width_in))


def test_labels_of_a_row_start_together():
    labels = _texts(_draw(6.0), "-label")
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


def _node_boxes(marks):
    return {m.id: m.box for m in marks if m.kind == "rect"}


class TestTheSecondPassOfTheReview:
    def test_a_measuring_pass_gets_the_height_it_needs_not_the_box(self):
        """`BlockCompiler.measure` draws into a hundred inches. Growing to
        the whole rect reported a 6in funnel as 28in tall."""
        funnel = {"diagram": {"kind": "funnel", "items": [
            {"label": f"Stage {i}", "detail": "A detail long enough to wrap twice "
             "at this width"} for i in range(4)]}}
        rect = PositionConfig(left=0.0, top=0.0, width=6.0, height=100.0)
        marks = diagrams.compile_diagram(funnel, rect, palette=PALETTE,
                                         label_pt=12.0, detail_pt=10.0)
        box = diagrams.primitives.bounds(marks)
        assert box.height < 6.0 * diagrams.ASPECT["funnel"] * 2

    def test_a_label_without_its_detail_is_centred_in_its_box(self):
        flow = {"diagram": {"kind": "flow", "items": [
            {"label": "Plan", "detail": "One line"}] * 3}}
        rect = PositionConfig(left=0.0, top=0.0, width=6.0, height=0.5)
        marks = diagrams.compile_diagram(flow, rect, palette=PALETTE,
                                         label_pt=10.0, detail_pt=10.0)
        boxes = _node_boxes(marks)
        for label in _texts(marks, "-label"):
            box = boxes[label.id.rpartition("-")[0]]
            drawn = diagrams._text_h("Plan", label.size_pt, label.box.width,
                                     bold=True, family="Arial")
            assert (label.box.top + drawn / 2) == pytest.approx(
                box.top + box.height / 2, abs=0.03)

    def test_drawing_is_a_handful_of_draws_not_ninety(self):
        import time

        narrow = PositionConfig(left=0.0, top=0.0, width=3.0, height=3.0)
        diagrams.compile_diagram(FLOW, narrow, palette=PALETTE, label_pt=14.0,
                                 detail_pt=12.0)
        started = time.perf_counter()
        for _ in range(5):
            diagrams.compile_diagram(FLOW, narrow, palette=PALETTE,
                                     label_pt=14.5, detail_pt=12.5)
        assert (time.perf_counter() - started) / 5 < 0.05

    def test_the_last_resort_keeps_autofit_as_a_guard(self):
        """Drawn at the floor because nothing reads, its text is no longer
        sized to fit - so the renderer is left to keep it in its boxes."""
        tiny = PositionConfig(left=0.0, top=0.0, width=1.2, height=0.4)
        marks = diagrams.compile_diagram(FLOW, tiny, palette=PALETTE,
                                         label_pt=14.0, detail_pt=12.0)
        assert all(m.autofit for m in marks if m.kind == "text")

    def test_a_slot_of_the_measured_floor_is_drawn_at_a_legible_size(self):
        """The floor and the drawing agree: given exactly the height the
        selector was told, the labels are not set below `LEGIBLE_PT`."""
        width = 6.0
        needed = diagrams.needed_height_in(FLOW, width, "Arial", label_pt=14.0)
        assert math.isfinite(needed)
        rect = PositionConfig(left=0.0, top=0.0, width=width, height=needed + 0.01)
        marks = diagrams.compile_diagram(FLOW, rect, palette=PALETTE,
                                         label_pt=14.0, detail_pt=12.0)
        assert min(m.size_pt for m in _texts(marks, "-label")) >= diagrams.LEGIBLE_PT

    def test_a_timeline_date_is_measured_bold_as_it_is_drawn(self):
        timeline = {"diagram": {"kind": "timeline", "items": [
            {"date": "2026-Q3 rollout window", "label": "Pilot"},
            {"date": "2027", "label": "General availability"}]}}
        rect = PositionConfig(left=0.0, top=0.0, width=2.4, height=2.0)
        marks = diagrams.compile_diagram(timeline, rect, palette=PALETTE,
                                         label_pt=12.0, detail_pt=11.0)
        for date in (m for m in marks if m.kind == "text" and "-d" in m.id):
            text = "".join(r.text for r in date.runs)
            assert date.bold
            assert date.box.height >= diagrams._text_h(
                text, date.size_pt, date.box.width, bold=True,
                family="Arial") - 1e-6
