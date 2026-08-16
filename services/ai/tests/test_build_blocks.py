"""
The block builder tools.

Following what ``build_visuals`` established: the model states a block through
a function signature, and every failure comes back naming the specific problem
rather than rendering something wrong. The recurring judgement these tools
encode is that a block is not a bullet list with decoration - a callout whose
figure is a sentence, or accent cards that are all the same colour, is content
that would have read better as text, and the tool says so.
"""

import pytest

from app.agent.registry import ToolRegistry
from app.agent.tools.build_blocks import (
    BuildAccentCardsTool, BuildBannerTool, BuildIconCardsTool,
    BuildImageCardRowTool, BuildLegendTool, BuildRowBandsTool,
    BuildStatCalloutsTool, DrawPrimitivesTool, MAX_BAND_PROSE_CHARS,
)
from app.renderer.blocks import BLOCK_TYPES


def _bands(count=3):
    return [{"icon": "growth", "label": f"Row {n}", "prose": "An implication."}
            for n in range(count)]


class TestStatCallouts:
    @pytest.mark.asyncio
    async def test_a_figure_and_its_caption_become_a_block(self):
        result = await BuildStatCalloutsTool.run(0, [
            {"value": "56%", "caption": "Respondents adopting AI"},
            {"value": "$93.5 billion", "caption": "Private investment in 2021"},
        ])

        assert result.success
        assert result.data["content"]["type"] == "statCallout"
        assert len(result.data["content"]["statCallouts"]) == 2

    @pytest.mark.asyncio
    async def test_a_sentence_set_large_is_refused(self):
        result = await BuildStatCalloutsTool.run(0, [
            {"value": "Adoption rose to 56% of respondents",
             "caption": "AI adoption"},
        ])

        assert not result.success
        assert "figure, not a sentence" in result.error

    @pytest.mark.asyncio
    async def test_a_figure_with_no_caption_is_refused(self):
        result = await BuildStatCalloutsTool.run(0, [{"value": "56%"}])

        assert not result.success
        assert "not information" in result.error

    @pytest.mark.asyncio
    async def test_more_than_four_figures_is_refused(self):
        result = await BuildStatCalloutsTool.run(
            0, [{"value": f"{n}%", "caption": "x"} for n in range(5)]
        )

        assert not result.success

    @pytest.mark.asyncio
    async def test_a_region_binding_is_carried_through(self):
        result = await BuildStatCalloutsTool.run(
            0, [{"value": "56%", "caption": "x"}], region_id="right"
        )

        assert result.data["content"]["regionId"] == "right"


class TestIconCards:
    @pytest.mark.asyncio
    async def test_a_row_of_cards_becomes_a_block(self):
        result = await BuildIconCardsTool.run(0, [
            {"icon": "security", "heading": "Zero trust", "body": ["Prove trust."]},
            {"icon": "identity", "heading": "Identity", "body": ["Characterise."]},
        ])

        assert result.success
        assert result.data["content"]["type"] == "iconCard"

    @pytest.mark.asyncio
    async def test_one_card_is_not_a_row(self):
        result = await BuildIconCardsTool.run(0, [{"icon": "x", "heading": "One"}])

        assert not result.success
        assert "at least two" in result.error

    @pytest.mark.asyncio
    async def test_a_heading_that_is_a_sentence_is_refused(self):
        result = await BuildIconCardsTool.run(0, [
            {"icon": "a", "heading": "This heading is a whole sentence about the "
                                     "thing it is nominally labelling"},
            {"icon": "b", "heading": "Fine"},
        ])

        assert not result.success
        assert "is a label" in result.error

    @pytest.mark.asyncio
    async def test_the_icon_defaults_to_the_heading(self):
        result = await BuildIconCardsTool.run(0, [
            {"heading": "Financial services"}, {"heading": "Retail"},
        ])

        assert result.data["content"]["iconCards"][0]["icon"] == "Financial services"


class TestAccentCards:
    @pytest.mark.asyncio
    async def test_cards_of_two_tones_become_a_block(self):
        result = await BuildAccentCardsTool.run(0, [
            {"heading": "A benefit", "tone": "benefit"},
            {"heading": "A risk", "tone": "risk"},
        ])

        assert result.success
        assert result.data["content"]["type"] == "accentCard"

    @pytest.mark.asyncio
    async def test_cards_that_are_all_one_tone_are_refused(self):
        """A colour code that distinguishes nothing is decoration."""
        result = await BuildAccentCardsTool.run(0, [
            {"heading": "One", "tone": "benefit"},
            {"heading": "Two", "tone": "benefit"},
        ])

        assert not result.success
        assert "distinguish" in result.error

    @pytest.mark.asyncio
    async def test_columns_are_clamped_to_something_readable(self):
        result = await BuildAccentCardsTool.run(0, [
            {"heading": "A", "tone": "benefit"}, {"heading": "B", "tone": "risk"},
        ], columns=9)

        assert result.data["content"]["columns"] == 3


class TestRowBands:
    @pytest.mark.asyncio
    async def test_bands_become_a_block(self):
        result = await BuildRowBandsTool.run(0, _bands(5))

        assert result.success
        assert len(result.data["content"]["rowBands"]) == 5

    @pytest.mark.asyncio
    async def test_two_bands_do_not_read_as_a_set(self):
        result = await BuildRowBandsTool.run(0, _bands(2))

        assert not result.success

    @pytest.mark.asyncio
    async def test_a_band_without_its_implication_is_a_bullet(self):
        rows = _bands(3)
        rows[1]["prose"] = ""

        result = await BuildRowBandsTool.run(0, rows)

        assert not result.success
        assert "is a bullet" in result.error

    @pytest.mark.asyncio
    async def test_a_paragraph_in_a_band_is_refused(self):
        rows = _bands(3)
        rows[0]["prose"] = "x" * (MAX_BAND_PROSE_CHARS + 1)

        result = await BuildRowBandsTool.run(0, rows)

        assert not result.success
        assert "one implication" in result.error


class TestBannerAndLegend:
    @pytest.mark.asyncio
    async def test_a_banner_becomes_a_block(self):
        result = await BuildBannerTool.run(0, "Digital trust addresses risk.")

        assert result.success
        assert result.data["content"]["banner"]["text"].endswith("risk.")

    @pytest.mark.asyncio
    async def test_a_paragraph_is_not_a_banner(self):
        result = await BuildBannerTool.run(0, "word " * 60)

        assert not result.success

    @pytest.mark.asyncio
    async def test_a_legend_becomes_a_block(self):
        result = await BuildLegendTool.run(0, [
            {"label": "Benefits", "tone": "benefit"},
            {"label": "Risks", "tone": "risk"},
        ])

        assert result.success
        assert len(result.data["content"]["legend"]["items"]) == 2

    @pytest.mark.asyncio
    async def test_a_legend_of_one_explains_nothing(self):
        result = await BuildLegendTool.run(0, [{"label": "Benefits", "tone": "benefit"}])

        assert not result.success


class TestImageCardRow:
    @pytest.mark.asyncio
    async def test_a_row_of_pictures_becomes_a_block(self):
        result = await BuildImageCardRowTool.run(0, [
            {"imageUrl": "https://example.test/a.png", "title": "Mining"},
            {"imageUrl": "https://example.test/b.png", "title": "Tourism"},
        ])

        assert result.success
        assert len(result.data["content"]["imageCardRow"]["cards"]) == 2

    @pytest.mark.asyncio
    async def test_an_unlabelled_picture_is_refused(self):
        result = await BuildImageCardRowTool.run(0, [
            {"imageUrl": "https://example.test/a.png"},
            {"imageUrl": "https://example.test/b.png", "title": "Tourism"},
        ])

        assert not result.success
        assert "decoration" in result.error

    @pytest.mark.asyncio
    async def test_group_spans_must_account_for_every_card(self):
        result = await BuildImageCardRowTool.run(
            0,
            [{"imageUrl": "https://example.test/a.png", "title": "A"},
             {"imageUrl": "https://example.test/b.png", "title": "B"},
             {"imageUrl": "https://example.test/c.png", "title": "C"}],
            groups=[{"label": "Economy", "span": 2}],
        )

        assert not result.success
        assert "every card belongs" in result.error.lower()

    @pytest.mark.asyncio
    async def test_groups_may_span_unequal_subsets(self):
        result = await BuildImageCardRowTool.run(
            0,
            [{"imageUrl": "https://example.test/a.png", "title": "A"},
             {"imageUrl": "https://example.test/b.png", "title": "B"},
             {"imageUrl": "https://example.test/c.png", "title": "C"}],
            groups=[{"label": "Economy", "span": 2},
                    {"label": "Spaceflight", "span": 1}],
        )

        assert result.success
        assert [g["span"] for g in
                result.data["content"]["imageCardRow"]["groups"]] == [2, 1]


class TestTheEscapeHatch:
    @pytest.mark.asyncio
    async def test_marks_come_back_as_authored_primitives(self):
        result = await DrawPrimitivesTool.run(0, [
            {"kind": "line", "id": "divider", "x1": 0.0, "y1": 0.5,
             "x2": 1.0, "y2": 0.5, "stroke": "#8C97A8", "strokeWeightPt": 0.75},
        ])

        assert result.success
        assert result.data["primitives"][0]["kind"] == "line"

    @pytest.mark.asyncio
    async def test_a_mark_outside_its_frame_is_refused(self):
        result = await DrawPrimitivesTool.run(0, [
            {"kind": "rect", "x": 0.8, "y": 0.0, "w": 0.5, "h": 1.0, "fill": "#EEE"},
        ])

        assert not result.success
        assert "escapes its frame" in result.error

    @pytest.mark.asyncio
    async def test_an_empty_text_mark_is_refused(self):
        result = await DrawPrimitivesTool.run(0, [
            {"kind": "text", "x": 0, "y": 0, "w": 0.5, "h": 0.1, "text": "   "},
        ])

        assert not result.success

    @pytest.mark.asyncio
    async def test_an_unknown_kind_is_refused(self):
        result = await DrawPrimitivesTool.run(0, [{"kind": "hexagon"}])

        assert not result.success
        assert "hexagon" in result.error

    @pytest.mark.asyncio
    async def test_a_slide_built_entirely_from_marks_is_refused(self):
        """
        The hatch is for what a block cannot express. Dozens of marks means a
        block was needed and not asked for.
        """
        result = await DrawPrimitivesTool.run(0, [
            {"kind": "rect", "x": 0.0, "y": n / 40, "w": 0.1, "h": 0.02,
             "fill": "#EEE"}
            for n in range(30)
        ])

        assert not result.success
        assert "it is a block" in result.error

    @pytest.mark.asyncio
    async def test_a_text_mark_becomes_runs(self):
        result = await DrawPrimitivesTool.run(0, [
            {"kind": "text", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.1,
             "text": "Exhibit 1", "sizePt": 11, "bold": True},
        ])

        mark = result.data["primitives"][0]
        assert mark["runs"] == [{"text": "Exhibit 1"}]
        assert mark["sizePt"] == 11
        assert mark["bold"] is True


class TestTheToolsAreRegistered:
    def test_every_block_type_has_a_builder(self):
        registry = ToolRegistry()
        from app.agent.registry import _register_default_tools

        _register_default_tools(registry)
        names = {tool.name for tool in registry.list_tools()}

        assert "draw_primitives" in names
        for expected in ("build_stat_callouts", "build_icon_cards",
                         "build_accent_cards", "build_row_bands",
                         "build_banner", "build_legend", "build_image_card_row"):
            assert expected in names, f"{expected} is not registered"

    def test_the_vocabulary_and_the_builders_line_up(self):
        assert len(BLOCK_TYPES) == 7


class TestBlocksReachTheSlideThroughTheLoop:
    """
    A block builder returns content; the loop attaches it. The thing worth
    pinning is that a composed slide carrying several blocks of the SAME type
    in different regions keeps all of them - keying the attachment on type
    alone silently drops every one but the last.
    """

    @staticmethod
    def _state():
        from app.agent.loop import WorkingState

        return WorkingState()

    def test_two_blocks_of_one_type_in_two_regions_both_survive(self):
        state = self._state()
        for region in ("left", "right"):
            state.prepared_visuals.setdefault(0, []).append(
                {"type": "statCallout", "regionId": region,
                 "statCallouts": [{"value": "1", "caption": region}]}
            )

        state.resolve_and_add_slide({"slideIndex": 0, "layoutId": "content",
                                     "contents": []})

        regions = {c.get("regionId")
                   for c in state.accumulated_slides[0]["contents"]}
        assert regions == {"left", "right"}

    def test_rebuilding_one_region_replaces_only_that_region(self):
        from app.agent.loop import WorkingState

        state = WorkingState()
        slots = state.prepared_visuals.setdefault(0, [])
        slots.append({"type": "statCallout", "regionId": "left",
                      "statCallouts": [{"value": "old", "caption": "x"}]})
        slots.append({"type": "statCallout", "regionId": "right",
                      "statCallouts": [{"value": "keep", "caption": "y"}]})

        # What the loop does when build_stat_callouts is called again for
        # the left region.
        replacement = {"type": "statCallout", "regionId": "left",
                       "statCallouts": [{"value": "new", "caption": "x"}]}
        key = (replacement["type"], replacement["regionId"])
        slots[:] = [s for s in slots
                    if (s.get("type"), s.get("regionId")) != key]
        slots.append(replacement)

        values = {c["regionId"]: c["statCallouts"][0]["value"] for c in slots}
        assert values == {"left": "new", "right": "keep"}

    def test_marks_attach_to_the_slides_primitives(self):
        state = self._state()
        state.prepared_primitives[0] = [
            {"kind": "line", "id": "divider", "x1": 0, "y1": 0.5, "x2": 1, "y2": 0.5}
        ]

        state.resolve_and_add_slide({"slideIndex": 0, "layoutId": "content",
                                     "contents": []})

        assert state.accumulated_slides[0]["primitives"][0]["id"] == "divider"
