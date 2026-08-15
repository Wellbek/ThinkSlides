"""
Reading a template's layouts in the language the renderer composes in.

The vision prompt asked a model to describe every layout as a list of
placeholder boxes typed title / body / subtitle / footer. That vocabulary has
no words for a split canvas, a rail, seven ruled row bands or a four-column
card row, so the extractor returned "some body boxes" and everything that made
the template look like itself was discarded at that step.

Two things are under test. The **rules** - what makes a rail a rail rather than
half of a split, what makes four rules a band row rather than a line over
something and a line under it - are checked against synthetic pages drawn at
known coordinates. The **acceptance criterion** is checked against the
reference's own design pages, from the grammar committed to
``reference_profile.json``, so the third-party PDF is never needed.

Nothing here calls a model. The issue asked for region extraction to be split
into its own vision call so that one malformed response could not lose the
whole bundle; measuring the grammar gives that isolation and determinism too,
and a page whose grammar cannot be read leaves the rest of the design system
exactly as it was.
"""

import os
import shutil
import tempfile

import fitz
import pytest

from app.eval import reference
from app.measure import grammar

WIDTH, HEIGHT = 960.0, 540.0
LEFT, RIGHT = WIDTH * 0.05, WIDTH * 0.95
NAVY = (0.02, 0.11, 0.17)


def _page(doc=None):
    doc = doc or fitz.open()
    return doc, doc.new_page(width=WIDTH, height=HEIGHT)


def _text(page, body, x, y, size=12.0):
    page.insert_text((x, y), body, fontsize=size, fontname="helv")


def _rule(page, y, x0=LEFT, x1=RIGHT):
    page.draw_rect(fitz.Rect(x0, y, x1, y + 0.7), color=None,
                   fill=(0.75, 0.75, 0.75))


def _chrome(page):
    """The rules every page of a deck carries, top and bottom."""
    _rule(page, HEIGHT * 0.17)
    _rule(page, HEIGHT * 0.94)
    _text(page, "Section eyebrow", LEFT, HEIGHT * 0.13, size=9.0)
    _text(page, "12", RIGHT - 20, HEIGHT * 0.97, size=9.0)


# ──────────────────────────────────────────────────────────────────────
# Grounds: rails and splits
# ──────────────────────────────────────────────────────────────────────

class TestAGroundThatChangesAcrossThePage:
    def _read(self, dark_from, dark_to):
        doc, page = _page()
        page.draw_rect(fitz.Rect(WIDTH * dark_from, 0, WIDTH * dark_to, HEIGHT),
                       color=None, fill=NAVY)
        _chrome(page)
        for line in range(6):
            _text(page, "A sentence of the argument set on this half of the page",
                  LEFT, 200 + line * 22)
        found = grammar.read_page(page, 1)
        doc.close()
        return found

    def test_a_narrow_full_height_band_is_a_rail(self):
        found = self._read(0.0, 0.33)

        assert found.has(grammar.RAIL)
        rail = next(r for r in found.regions if r["kind"] == "rail")
        assert rail["x"] == pytest.approx(0.0, abs=0.05)
        assert rail["w"] == pytest.approx(0.33, abs=0.06)
        assert rail["h"] == 1.0

    def test_a_rail_on_the_right_is_still_a_rail(self):
        found = self._read(0.70, 1.0)

        rail = next(r for r in found.regions if r["kind"] == "rail")

        assert rail["x"] == pytest.approx(0.70, abs=0.06)

    def test_half_a_canvas_is_a_split_not_a_rail(self):
        found = self._read(0.5, 1.0)

        assert found.has(grammar.GROUND_SPLIT)
        assert not found.has(grammar.RAIL)
        assert [r["kind"] for r in found.regions if r.get("ground")] == \
            ["panel", "panel"]

    def test_a_rail_is_not_also_reported_as_a_split(self):
        """A rail is a kind of ground split, so saying both says nothing."""
        found = self._read(0.0, 0.30)

        assert grammar.GROUND_SPLIT not in found.features

    def test_each_panel_carries_the_colour_it_is_painted(self):
        found = self._read(0.5, 1.0)

        grounds = [r["ground"] for r in found.regions if r.get("ground")]

        assert len(grounds) == 2
        assert len(set(grounds)) == 2

    def test_one_ground_across_the_whole_page_is_no_split(self):
        doc, page = _page()
        _chrome(page)
        for line in range(6):
            _text(page, "A sentence on a page of one colour", LEFT, 200 + line * 22)

        found = grammar.read_page(page, 1)
        doc.close()

        assert not found.has(grammar.GROUND_SPLIT)
        assert not found.has(grammar.RAIL)

    def test_a_hairline_edge_is_not_a_region(self):
        """A 2%-wide strip of another colour is a rule, not a panel."""
        found = self._read(0.0, 0.02)

        assert not found.has(grammar.RAIL)
        assert not found.has(grammar.GROUND_SPLIT)


# ──────────────────────────────────────────────────────────────────────
# Band rows
# ──────────────────────────────────────────────────────────────────────

class TestRuledRowBands:
    def _bands(self, count, spacing=None):
        doc, page = _page()
        _chrome(page)
        _text(page, "A headline that states a finding", LEFT, 100, size=24.0)
        top = 150.0
        spacing = spacing or [40.0] * count
        y = top
        for index in range(count + 1):
            _rule(page, y)
            if index < count:
                _text(page, f"Sector {index}", LEFT, y + 20)
                _text(page, "A full sentence of prose carrying this band's point",
                      LEFT + 180, y + 20)
                y += spacing[index]
        found = grammar.read_page(page, 1)
        doc.close()
        return found

    def test_seven_rules_of_bands_read_as_a_band_row(self):
        found = self._bands(7)

        assert found.has(grammar.BAND_ROW)
        assert found.bands == 7

    def test_the_band_row_carries_a_child_per_band(self):
        found = self._bands(5)

        row = next(r for r in found.regions if r["kind"] == "bandRow")

        assert len(row["children"]) == 5
        assert all(child["kind"] == "band" for child in row["children"])

    def test_the_bands_tile_their_row_without_gaps(self):
        found = self._bands(5)
        row = next(r for r in found.regions if r["kind"] == "bandRow")

        tops = [child["y"] for child in row["children"]]
        assert tops == sorted(tops)
        assert tops[0] == pytest.approx(0.0, abs=0.01)
        last = row["children"][-1]
        assert last["y"] + last["h"] == pytest.approx(1.0, abs=0.02)

    def test_geometry_is_fractional(self):
        found = self._bands(6)
        row = next(r for r in found.regions if r["kind"] == "bandRow")

        for key in ("x", "y", "w", "h"):
            assert 0.0 <= row[key] <= 1.0

    def test_a_rule_over_something_and_one_under_it_is_not_a_band_row(self):
        found = self._bands(2)

        assert not found.has(grammar.BAND_ROW)

    def test_wildly_uneven_rules_are_not_a_band_row(self):
        """Evenness is what makes a set of rules a set."""
        found = self._bands(4, spacing=[20.0, 120.0, 22.0, 110.0])

        assert not found.has(grammar.BAND_ROW)

    def test_the_decks_own_chrome_rules_are_not_bands(self):
        """The rule under the eyebrow and the one over the footer are on
        every page; a band row made of those two spans the whole deck."""
        doc, page = _page()
        _chrome(page)
        _text(page, "A headline that states a finding", LEFT, 100, size=24.0)
        for line in range(5):
            _text(page, "A sentence of body copy", LEFT, 220 + line * 22)

        found = grammar.read_page(page, 1)
        doc.close()

        assert not found.has(grammar.BAND_ROW)


# ──────────────────────────────────────────────────────────────────────
# Column grids and banners
# ──────────────────────────────────────────────────────────────────────

class TestCardRowsAndBanners:
    def _cards(self, count, widths=None):
        doc, page = _page()
        _chrome(page)
        _text(page, "A headline that states a finding", LEFT, 100, size=24.0)
        span = RIGHT - LEFT
        widths = widths or [1.0 / count] * count
        x = LEFT
        for index, share in enumerate(widths):
            width = span * share
            _text(page, f"Card {index}", x, 200, size=16.0)
            # Filled to its own column's width, and no further: insert_text
            # does not wrap, and a line running into the next column makes the
            # two of them one cell, which is exactly what a card row is not.
            words = max(1, int((width - 24) / 34))
            for line in range(4):
                _text(page, " ".join(["prose"] * words), x, 224 + line * 16)
            x += width
        return doc, page

    def test_four_equal_columns_are_a_card_row(self):
        doc, page = self._cards(4)
        found = grammar.read_page(page, 1)
        doc.close()

        assert found.has(grammar.CARD_ROW)
        assert found.columns == 4

    def test_the_grid_carries_a_column_per_card(self):
        doc, page = self._cards(3)
        found = grammar.read_page(page, 1)
        doc.close()

        grid = next(r for r in found.regions if r["kind"] == "grid")

        assert [child["kind"] for child in grid["children"]] == ["column"] * 3
        assert grid["children"][0]["x"] == pytest.approx(0.0, abs=0.02)

    def test_two_columns_are_not_a_card_row(self):
        """Two is a label beside a paragraph, which the band rules cover."""
        doc, page = self._cards(2)
        found = grammar.read_page(page, 1)
        doc.close()

        assert not found.has(grammar.CARD_ROW)

    def test_columns_of_very_different_widths_are_not_a_grid(self):
        doc, page = self._cards(3, widths=[0.62, 0.19, 0.19])
        found = grammar.read_page(page, 1)
        doc.close()

        assert not found.has(grammar.CARD_ROW)

    def test_a_ruled_strip_at_the_foot_is_a_banner(self):
        doc, page = self._cards(4)
        _rule(page, HEIGHT * 0.80)
        _text(page, "A closing sentence that sums the four cards up",
              LEFT, HEIGHT * 0.86)

        found = grammar.read_page(page, 1)
        doc.close()

        assert found.has(grammar.BANNER)
        banner = next(r for r in found.regions if r["kind"] == "banner")
        assert banner["y"] == pytest.approx(0.80, abs=0.02)

    def test_a_rule_with_nothing_under_it_is_not_a_banner(self):
        doc, page = self._cards(4)
        _rule(page, HEIGHT * 0.80)

        found = grammar.read_page(page, 1)
        doc.close()

        assert not found.has(grammar.BANNER)


# ──────────────────────────────────────────────────────────────────────
# Degenerate templates
# ──────────────────────────────────────────────────────────────────────

class TestATemplateWithNoneOfThis:
    def test_a_plain_page_yields_an_empty_grammar_not_an_error(self):
        """#118's last acceptance criterion."""
        doc, page = _page()
        _text(page, "A headline", LEFT, 100, size=24.0)
        _text(page, "One line of body copy", LEFT, 200)

        found = grammar.read_page(page, 1)
        doc.close()

        assert found.regions == ()
        assert found.features == ()

    def test_an_empty_page_yields_an_empty_grammar(self):
        doc, page = _page()

        found = grammar.read_page(page, 1)
        doc.close()

        assert found.regions == ()

    def test_an_empty_grammar_round_trips(self):
        empty = grammar.TemplateGrammar()

        assert grammar.TemplateGrammar.from_dict(empty.to_dict()).layouts == []
        assert grammar.TemplateGrammar.from_dict(None).layouts == []
        assert grammar.TemplateGrammar.from_dict({}).features == set()

    def test_a_layout_round_trips(self):
        doc, page = _page()
        page.draw_rect(fitz.Rect(0, 0, WIDTH * 0.33, HEIGHT), color=None, fill=NAVY)
        _chrome(page)
        for line in range(6):
            _text(page, "A sentence of the argument", LEFT + 340, 200 + line * 22)
        found = grammar.read_page(page, 7, label="split canvas")
        doc.close()

        again = grammar.LayoutGrammar.from_dict(found.to_dict())

        assert again == found


class TestTheRegionsAreReadyToUse:
    """Whatever is emitted has to be something a slide can carry, or the
    grammar is a report rather than a composition."""

    @pytest.fixture
    def layout(self):
        doc, page = _page()
        page.draw_rect(fitz.Rect(0, 0, WIDTH * 0.33, HEIGHT), color=None, fill=NAVY)
        _chrome(page)
        _text(page, "A headline that states a finding", LEFT + 340, 100, size=24.0)
        top = 180.0
        for index in range(6):
            _rule(page, top + index * 45, x0=LEFT + 340)
            _text(page, f"Sector {index}", LEFT + 350, top + index * 45 + 20)
            _text(page, "A full sentence of prose for this band",
                  LEFT + 500, top + index * 45 + 20)
        _rule(page, top + 5 * 45, x0=LEFT + 340)
        found = grammar.read_page(page, 1)
        doc.close()
        return found

    def test_the_region_model_accepts_them(self, layout):
        from app.renderer.regions import RegionTree

        tree = RegionTree.from_slide({"regions": list(layout.regions)},
                                     13.333, 7.5)

        assert tree is not None
        assert len(tree.resolved) >= len(layout.regions)

    def test_the_validator_accepts_them(self, layout):
        from app.renderer import regions as region_model

        errors, _ = region_model.validate_regions(list(layout.regions), set())

        assert errors == []

    def test_every_region_has_an_id_and_a_known_kind(self, layout):
        from app.renderer.regions import REGION_KINDS

        def check(region):
            assert region["id"]
            assert region["kind"] in REGION_KINDS
            for child in region.get("children") or []:
                check(child)

        for region in layout.regions:
            check(region)


# ──────────────────────────────────────────────────────────────────────
# The reference deck
# ──────────────────────────────────────────────────────────────────────

class TestTheReferenceGrammarIsCommitted:
    """#118's acceptance test: on reference pages 3, 22, 40 and 95,
    extraction identifies respectively a rail, a ground split, a band row and
    a card row. Checked from the committed fixture, so no PDF is needed."""

    @staticmethod
    def _layout(page: int):
        found = next(
            (layout for layout in reference.load_design_grammar().layouts
             if layout.page == page),
            None,
        )
        assert found is not None, f"page {page} has no committed grammar"
        return found

    def test_page_3_is_a_rail(self):
        layout = self._layout(3)

        assert layout.has(grammar.RAIL)
        rail = next(r for r in layout.regions if r["kind"] == "rail")
        assert rail["w"] < 0.45
        assert rail["h"] == 1.0

    def test_page_22_is_a_ground_split(self):
        layout = self._layout(22)

        assert layout.has(grammar.GROUND_SPLIT)
        panels = [r for r in layout.regions if r.get("ground")]
        assert len(panels) == 2
        assert panels[1]["x"] == pytest.approx(0.5, abs=0.05)

    def test_page_40_is_a_band_row_of_seven(self):
        layout = self._layout(40)

        assert layout.has(grammar.BAND_ROW)
        assert layout.bands == 7

    def test_page_95_is_a_card_row_of_four(self):
        layout = self._layout(95)

        assert layout.has(grammar.CARD_ROW)
        assert layout.columns == 4

    def test_page_95_also_carries_its_banner(self):
        assert self._layout(95).has(grammar.BANNER)

    def test_the_grammar_covers_the_vocabulary_the_epic_needs(self):
        found = reference.load_design_grammar().features

        assert {grammar.RAIL, grammar.GROUND_SPLIT, grammar.BAND_ROW,
                grammar.CARD_ROW, grammar.BANNER} <= found

    def test_every_committed_region_is_fractional(self):
        for layout in reference.load_design_grammar().layouts:
            for region in layout.regions:
                for key in ("x", "y", "w", "h"):
                    assert 0.0 <= region[key] <= 1.0, (
                        f"page {layout.page}: {region['id']}.{key} = {region[key]}"
                    )

    def test_a_page_with_no_composition_reports_none_rather_than_inventing(self):
        """Reference page 4 is prose. Reporting a grid there would be worse
        than reporting nothing."""
        assert self._layout(4).regions == ()


class TestTheGrammarReachesTheComposer:
    def test_the_designer_is_offered_the_templates_compositions(self):
        from app.agent.tools.design_slide_content import _grammar_guidance

        design_system = {"layoutGrammar":
                         reference.load_design_grammar().to_dict()}

        guidance = _grammar_guidance(design_system)

        assert "Compositions this template uses" in guidance
        assert '"kind":"rail"' in guidance or '"kind": "rail"' in guidance

    def test_a_template_with_no_grammar_offers_nothing(self):
        from app.agent.tools.design_slide_content import _grammar_guidance

        assert _grammar_guidance({}) == ""
        assert _grammar_guidance({"layoutGrammar": {"layouts": []}}) == ""

    def test_the_bundle_has_somewhere_to_keep_it(self):
        from app.db.models import DesignSystemBundle

        assert "layout_grammar" in DesignSystemBundle.__table__.columns


class TestReadingAWholeDocument:
    def test_each_page_is_read_as_its_own_layout(self):
        work_dir = tempfile.mkdtemp(prefix="ts_grammar_")
        try:
            path = os.path.join(work_dir, "deck.pdf")
            doc = fitz.open()
            for index in range(3):
                page = doc.new_page(width=WIDTH, height=HEIGHT)
                page.draw_rect(fitz.Rect(0, 0, WIDTH * 0.33, HEIGHT),
                               color=None, fill=NAVY)
                _chrome(page)
                _text(page, f"Page {index}", LEFT + 340, 200)
            doc.save(path)
            doc.close()

            read = grammar.read_pdf(path, [1, 2, 3], {1: "split canvas"})

            assert [layout.page for layout in read.layouts] == [1, 2, 3]
            assert read.layouts[0].label == "split canvas"
            assert grammar.RAIL in read.features
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_pages_outside_the_document_are_skipped(self):
        work_dir = tempfile.mkdtemp(prefix="ts_grammar_")
        try:
            path = os.path.join(work_dir, "one.pdf")
            doc = fitz.open()
            doc.new_page(width=WIDTH, height=HEIGHT)
            doc.save(path)
            doc.close()

            assert len(grammar.read_pdf(path, [1, 9, 40]).layouts) == 1
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
