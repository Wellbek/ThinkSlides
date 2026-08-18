"""
An exhibit reaches its slide, and a second exhibit does not evict the first.

A builder returns content; the loop attaches it. Prepared visuals used to be
held one per (type, region), which is enough while a slide carries one chart
and wrong the moment it carries two: on a slide with no region tree both bind
to the same nothing, so the second silently replaced the first. The build
succeeded, the tool reported success, and the exhibit never reached the page -
a model had no way to detect it and no reason to retry.

These tests store blocks the way the loop stores them, through the same
``slot_key``, so a change to that key cannot leave them asserting behaviour
the loop no longer has.
"""

from app.agent.loop import WorkingState
from app.agent.tools._binding import slot_key


def _store(state, block, slide_index=0):
    """Exactly what the loop does with a build_* result."""
    slots = state.prepared_visuals.setdefault(slide_index, [])
    key = slot_key(block)
    slots[:] = [s for s in slots if slot_key(s) != key]
    slots.append(block)


def _chart(**kwargs):
    return {"type": "chart", "chart": {"chartType": "bar"}, **kwargs}


class TestSeveralExhibitsOnOnePage:
    def test_two_charts_on_one_unbound_slide_both_survive(self):
        state = WorkingState()
        for name in ("bridge", "mix"):
            _store(state, _chart(slot=name))

        state.resolve_and_add_slide(
            {"slideIndex": 0, "layoutId": "content", "contents": []}
        )

        slots = {c.get("slot") for c in state.accumulated_slides[0]["contents"]}
        assert slots == {"bridge", "mix"}

    def test_two_charts_in_two_regions_both_survive(self):
        state = WorkingState()
        for region in ("exhibitMain", "exhibitTopRight"):
            _store(state, _chart(regionId=region))

        state.resolve_and_add_slide(
            {"slideIndex": 0, "layoutId": "content", "contents": []}
        )

        regions = {c.get("regionId")
                   for c in state.accumulated_slides[0]["contents"]}
        assert regions == {"exhibitMain", "exhibitTopRight"}

    def test_a_full_dashboard_page_keeps_all_four_exhibits(self):
        state = WorkingState()
        _store(state, {"type": "metrics", "regionId": "strip",
                       "metrics": {"items": []}})
        _store(state, _chart(regionId="exhibitMain", slot="bridge"))
        _store(state, _chart(regionId="exhibitTopRight", slot="mix"))
        _store(state, {"type": "table", "regionId": "exhibitBottomRight",
                       "table": {"rows": []}})

        state.resolve_and_add_slide(
            {"slideIndex": 0, "layoutId": "content", "contents": []}
        )

        assert len(state.accumulated_slides[0]["contents"]) == 4


class TestRebuildingOneExhibit:
    def test_rebuilding_one_slot_replaces_only_that_slot(self):
        """The revision loop depends on this: one flagged exhibit, one redraw."""
        state = WorkingState()
        _store(state, {"type": "chart", "slot": "bridge", "chart": {"v": "old"}})
        _store(state, {"type": "chart", "slot": "mix", "chart": {"v": "keep"}})

        _store(state, {"type": "chart", "slot": "bridge", "chart": {"v": "new"}})

        values = {c["slot"]: c["chart"]["v"] for c in state.prepared_visuals[0]}
        assert values == {"bridge": "new", "mix": "keep"}

    def test_an_inline_chart_is_still_dropped_for_the_validated_one(self):
        """
        A slide cannot carry the same figures twice.

        design_slide_content may still write a chart inline; the validated
        block for the same slot replaces it rather than sitting beside it.
        """
        state = WorkingState()
        _store(state, {"type": "chart", "slot": "bridge",
                       "chart": {"source": "validated"}})

        state.resolve_and_add_slide({
            "slideIndex": 0, "layoutId": "content",
            "contents": [{"type": "chart", "slot": "bridge",
                          "chart": {"source": "inline"}}],
        })

        contents = state.accumulated_slides[0]["contents"]
        assert len(contents) == 1
        assert contents[0]["chart"]["source"] == "validated"

    def test_an_unslotted_chart_still_replaces_an_unslotted_one(self):
        """
        The single-exhibit path is unchanged.

        A run that never names a slot behaves exactly as it did: rebuilding
        the slide's one chart replaces it rather than accumulating copies.
        """
        state = WorkingState()
        _store(state, {"type": "chart", "chart": {"v": "first"}})
        _store(state, {"type": "chart", "chart": {"v": "second"}})

        assert len(state.prepared_visuals[0]) == 1
        assert state.prepared_visuals[0][0]["chart"]["v"] == "second"
