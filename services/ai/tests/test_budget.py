"""
The density budget: how much a slide should carry, and what happens when it
does not.

#119 measured what a template puts on a page and published the figure. A
target nothing checks is a suggestion, so this is the check - and the risk it
owns is stated plainly in the issue: telling a model to fill seven bands is a
reliable way to get seven bands of filler. Most of what is tested here is
therefore the *conditions* under which the budget does not fire.

The other property under test is that the planning-time count and the
rendered-page count agree. If they drifted, a deck would be written to one
number and scored on another, and the harness would report progress that a
reader would not see. ``TestTheTwoCountsAgree`` renders the fixture decks for
real and compares.
"""

import os
import shutil
import tempfile

import pytest

from app import budget
from app.agent import run_context
from app.design_system import get_default_design_system

DENSE = {
    "density": {
        "pagesMeasured": 12,
        "wordsPerSlide": {"median": 208.0, "p25": 178.0, "p75": 235.0},
        "blocksPerSlide": {"median": 10.0, "p25": 6.5, "p75": 13.0},
        "inkCoverageMedian": 0.5,
        "gridRowsMedian": 3.5,
        "gridColumnsMedian": 3.0,
        "wordsPerBlockMedian": 20.0,
    }
}


def _slide(index=1, points=4, words_each=8, **extra):
    runs = [{"text": "Word " * words_each, "bullet": True, "level": 0}
            for _ in range(points)]
    contents = [
        {"type": "text", "placeholderIdx": 0,
         "runs": [{"text": "A headline that states a finding of some kind"}]},
    ]
    if runs:
        contents.append({"type": "text", "placeholderIdx": 1, "runs": runs})
    slide = {"slideIndex": index, "layoutId": "content",
             "layoutName": "Content", "contents": contents}
    slide.update(extra)
    return slide


# ──────────────────────────────────────────────────────────────────────
# The budget itself
# ──────────────────────────────────────────────────────────────────────

class TestABudgetIsPerKindOfPage:
    def test_a_content_page_is_held_to_the_measured_figure(self):
        target = budget.budget_for(DENSE, budget.CONTENT).blocks_target

        assert 9 <= target <= 11

    @pytest.mark.parametrize("archetype", [budget.COVER, budget.DIVIDER])
    def test_a_cover_and_a_divider_are_not(self, archetype):
        """Holding every page to a content page's figure produces a deck of
        uniformly cluttered pages, which is a different failure."""
        content = budget.budget_for(DENSE, budget.CONTENT)
        sparse = budget.budget_for(DENSE, archetype)

        assert sparse.blocks_target < content.blocks_target / 3
        assert sparse.words_target < content.words_target / 5

    def test_an_exhibit_slide_sits_between_them(self):
        exhibit = budget.budget_for(DENSE, budget.EXHIBIT)
        content = budget.budget_for(DENSE, budget.CONTENT)
        cover = budget.budget_for(DENSE, budget.COVER)

        assert cover.blocks_target < exhibit.blocks_target < content.blocks_target

    def test_no_page_is_ever_held_to_more_than_a_readable_number(self):
        enormous = {"density": {"blocksPerSlide": {"median": 400},
                                "wordsPerSlide": {"median": 9000}}}

        assert budget.budget_for(enormous).blocks_target <= budget.MAX_BLOCKS
        assert budget.budget_for(enormous).blocks_max <= budget.MAX_BLOCKS

    def test_an_unmeasured_template_has_no_budget_to_state(self):
        assert not budget.density_brief_available({})
        assert not budget.density_brief_available({"density": {}})
        assert budget.density_brief_available(DENSE)


class TestTheArchetypeComesFromThePlan:
    @pytest.mark.parametrize("content_type,expected", [
        ("title", budget.COVER),
        ("section", budget.DIVIDER),
        ("chart", budget.EXHIBIT),
        ("table", budget.EXHIBIT),
        ("image", budget.EXHIBIT),
        ("conclusion", budget.CLOSING),
        ("content", budget.CONTENT),
        ("twoColumn", budget.CONTENT),
        ("", budget.CONTENT),
    ])
    def test_a_content_type_maps_to_a_kind_of_page(self, content_type, expected):
        assert budget.archetype_for({"contentType": content_type}) == expected

    def test_an_explicit_archetype_wins(self):
        assert budget.archetype_for(
            {"contentType": "content", "archetype": "divider"}
        ) == budget.DIVIDER

    def test_an_unknown_archetype_is_ignored_rather_than_trusted(self):
        assert budget.archetype_for({"archetype": "splash"}) == budget.CONTENT


# ──────────────────────────────────────────────────────────────────────
# The guard the issue exists to carry
# ──────────────────────────────────────────────────────────────────────

class TestTheBudgetScalesToTheMaterial:
    def test_a_slide_with_nothing_behind_it_is_asked_for_little(self):
        rich = budget.budget_for(DENSE, budget.CONTENT, material=1.0)
        thin = budget.budget_for(DENSE, budget.CONTENT, material=0.0)

        assert thin.blocks_target < rich.blocks_target / 2
        assert thin.words_target < rich.words_target / 2

    def test_the_floor_is_not_zero(self):
        thin = budget.budget_for(DENSE, budget.CONTENT, material=0.0)

        assert thin.blocks_target >= 1
        assert thin.words_target >= 6

    def test_material_reads_what_the_run_already_knows(self):
        """Never a model's own estimate of whether it has enough to say."""
        plenty = budget.material_score(
            ["a", "b", "c", "d", "e"], has_exhibit=True, figures=6,
            research_findings=4, source_words=900,
        )
        nothing = budget.material_score([])

        assert plenty == 1.0
        assert nothing == 0.0

    @pytest.mark.parametrize("kwargs", [
        {"key_points": ["one", "two"]},
        {"key_points": [], "has_exhibit": True},
        {"key_points": [], "figures": 2},
        {"key_points": [], "research_findings": 2},
    ])
    def test_every_signal_moves_it(self, kwargs):
        points = kwargs.pop("key_points")

        assert budget.material_score(points, **kwargs) > 0

    def test_blank_points_are_not_material(self):
        assert budget.material_score(["", "   ", None]) == 0.0


class TestASparseSlideIsSometimesRight:
    def test_a_thin_slide_with_thin_material_is_accepted(self):
        errors, warnings = budget.validate_density(
            _slide(points=2, words_each=5), DENSE, material=0.2,
        )

        assert errors == []
        assert warnings == []

    def test_the_same_slide_with_rich_material_is_rejected(self):
        errors, _ = budget.validate_density(
            _slide(points=2, words_each=5), DENSE, material=1.0,
        )

        assert len(errors) == 1
        assert "under its density budget" in errors[0]

    def test_the_rejection_says_what_is_missing_and_what_to_do(self):
        errors, _ = budget.validate_density(
            _slide(points=2, words_each=4), DENSE, material=1.0,
        )

        assert "blocks where this slide should carry at least" in errors[0]
        assert "Redesign it with more on it" in errors[0]
        # Never "make the existing points longer", which is the failure mode
        # this replaces.
        assert "rather than shortening" in errors[0]

    def test_a_cover_is_never_under_budget(self):
        errors, warnings = budget.validate_density(
            _slide(points=0, contentType="title"), DENSE, material=1.0,
        )

        assert (errors, warnings) == ([], [])

    def test_an_unmeasured_run_checks_nothing(self):
        errors, warnings = budget.validate_density(
            _slide(points=1, words_each=3), {}, material=1.0,
        )

        assert (errors, warnings) == ([], [])

    def test_a_slide_nobody_measured_the_material_for_checks_nothing(self):
        """None is not zero: it is the absence of the evidence the budget
        needs, and guessing would be the invention this prevents."""
        errors, warnings = budget.validate_density(
            _slide(points=1, words_each=3), DENSE, material=None,
        )

        assert (errors, warnings) == ([], [])

    def test_an_overfull_slide_is_a_warning_not_a_rejection(self):
        errors, warnings = budget.validate_density(
            _slide(points=30, words_each=20), DENSE, material=1.0,
        )

        assert errors == []
        assert len(warnings) == 1
        assert "past the" in warnings[0]

    def test_a_slide_on_budget_says_nothing(self):
        errors, warnings = budget.validate_density(
            _slide(points=10, words_each=20), DENSE, material=1.0,
        )

        assert (errors, warnings) == ([], [])


# ──────────────────────────────────────────────────────────────────────
# Counting what a slide carries
# ──────────────────────────────────────────────────────────────────────

class TestCountingASlide:
    def test_the_headline_is_not_a_block(self):
        """Every page has one, so counting it reports every page as carrying
        one more than it does - the same reason measure.structure drops it."""
        substance = budget.measure_slide(_slide(points=3))

        assert substance.blocks == 3

    def test_a_sub_bullet_belongs_to_the_point_above_it(self):
        slide = _slide(points=0)
        slide["contents"].append({"type": "text", "placeholderIdx": 1, "runs": [
            {"text": "A top level point", "level": 0},
            {"text": "Its supporting detail", "level": 1},
            {"text": "Another top level point", "level": 0},
        ]})

        assert budget.measure_slide(slide).blocks == 2

    def test_a_chart_is_one_block_and_its_words_are_counted(self):
        slide = _slide(points=0)
        slide["contents"].append({"type": "chart", "chart": {
            "chartType": "bar", "title": "Adoption by year",
            "categories": ["2024"], "series": [{"name": "A", "values": [1]}]}})

        substance = budget.measure_slide(slide)

        assert substance.blocks == 1
        assert substance.exhibits == 1

    def test_a_metric_row_is_one_block_per_figure(self):
        slide = _slide(points=0)
        slide["contents"].append({"type": "metrics", "metrics": {"items": [
            {"value": "36.2", "label": "Revenue"},
            {"value": "14.9%", "label": "Margin"},
        ]}})

        assert budget.measure_slide(slide).blocks == 2

    def test_a_typed_block_counts_its_entries(self):
        slide = _slide(points=0)
        slide["contents"].append({"type": "rowBand", "rowBands": [
            {"label": "One", "body": "The first band's sentence"},
            {"label": "Two", "body": "The second band's sentence"},
            {"label": "Three", "body": "The third band's sentence"},
        ]})

        substance = budget.measure_slide(slide)

        assert substance.blocks == 3
        assert substance.words > 9

    def test_a_filled_table_reads_as_one_object(self):
        """Its rendered rows sit inside a single ruled shape, and the page
        counter finds an exhibit rather than a grid."""
        slide = _slide(points=0)
        slide["contents"].append({"type": "table", "table": {
            "headerRow": True,
            "rows": [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"]]}})

        assert budget.measure_slide(slide).blocks == 1

    def test_a_hairline_table_reads_as_the_grid_it_is(self):
        """Reference page 12 is fifteen blocks because it is a hairline
        table; the same rows in filled bands are one."""
        slide = _slide(points=0)
        slide["contents"].append({"type": "table", "table": {
            "headerRow": True,
            "rows": [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"]]}})
        hairline = {"dataViz": {"tableTreatment": "hairline"}}

        assert budget.measure_slide(slide, hairline).blocks == 4

    def test_an_authored_primitive_counts_too(self):
        slide = _slide(points=1)
        slide["primitives"] = [
            {"kind": "text", "runs": [{"text": "A mark the slide drew itself"}]},
            {"kind": "line"},
        ]

        assert budget.measure_slide(slide).blocks == 2

    def test_an_empty_slide_carries_nothing(self):
        assert budget.measure_slide({}).blocks == 0
        assert budget.measure_slide(None).words == 0

    def test_junk_in_contents_does_not_crash_the_count(self):
        assert budget.measure_slide(
            {"contents": ["a string", None, 7, {"type": "text"}]}
        ).blocks == 0


class TestTheTwoCountsAgree:
    """
    The planning count has to predict the rendered one.

    If they disagreed, a slide could pass its budget and then be measured as
    sparse by the harness - the deck would be written to one number and scored
    on another, and the epic would report progress a reader cannot see.
    """

    TOLERANCE = 2

    @pytest.fixture(scope="class")
    @staticmethod
    def counted():
        import fitz

        from app.eval.fixture_decks import DECKS
        from app.renderer.pptx_renderer import PPTXRenderer
        from app.renderer.preview_generator import PreviewGenerator
        from app.measure.structure import count_blocks

        rows = []
        for name in sorted(DECKS):
            slides, theme = DECKS[name]
            work_dir = tempfile.mkdtemp(prefix=f"ts_budget_{name}_")
            try:
                pptx_path = os.path.join(work_dir, "deck.pptx")
                PPTXRenderer(template_path=None, color_theme=theme).render(
                    slides=slides, metadata={"title": name}, output_path=pptx_path,
                )
                pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
                assert pdf_path, f"LibreOffice could not convert the {name} deck"
                doc = fitz.open(pdf_path)
                try:
                    for index, slide in enumerate(slides):
                        rows.append((
                            f"{name}[{index}]",
                            count_blocks(doc[index]).block_count,
                            budget.measure_slide(slide).blocks,
                        ))
                finally:
                    doc.close()
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
        return rows

    def test_every_slide_is_predicted_within_tolerance(self, counted):
        off = [(name, rendered, planned) for name, rendered, planned in counted
               if abs(rendered - planned) > self.TOLERANCE]

        assert off == [], (
            "planned block counts drifted from what renders: "
            + ", ".join(f"{n} rendered {r} planned {p}" for n, r, p in off)
        )

    def test_the_decks_really_were_rendered(self, counted):
        assert len(counted) >= 10
        assert sum(rendered for _, rendered, _ in counted) > 0


class TestASlideBuiltToTheReferenceBudgetRendersAtIt:
    """
    The loop closed: budget -> composed slide -> rendered page -> measured.

    The acceptance criterion asks that under reference-deck density targets,
    generated slides reach comparable blocks-per-slide. Which slides a model
    writes is not something a test can fix, but this checks the half that is
    ours - that a slide built to the budget out of the block vocabulary really
    does render at the reference's density, rather than the budget being a
    number nothing on the page can reach.
    """

    REGIONS = [
        {"id": "title", "kind": "banner", "x": 0.045, "y": 0.07, "w": 0.91, "h": 0.10},
        {"id": "left", "kind": "panel", "x": 0.045, "y": 0.22, "w": 0.55, "h": 0.66},
        {"id": "right", "kind": "panel", "x": 0.63, "y": 0.22, "w": 0.325, "h": 0.66},
    ]

    @staticmethod
    def _reference_design_system():
        from app.eval import reference

        density, _ = reference.load_reference_density()
        design_system = get_default_design_system()
        design_system["density"] = density.to_dict()
        return design_system

    @staticmethod
    def _slide():
        return {
            "slideIndex": 0, "layoutId": "content", "layoutName": "Content",
            "section": "Market context", "titleRegister": "claim",
            "regions": TestASlideBuiltToTheReferenceBudgetRendersAtIt.REGIONS,
            "contents": [
                {"type": "text", "placeholderIdx": 0, "regionId": "title", "runs": [{
                    "text": "Adoption rose 21 percent across every surveyed sector "
                            "in 2024", "bold": True}]},
                {"type": "rowBand", "regionId": "left", "rowBands": [
                    {"label": f"Sector {i}",
                     "prose": "Adoption rose sharply on cost, with the steepest "
                              "gains in operations"}
                    for i in range(7)
                ]},
                {"type": "accentCard", "regionId": "right", "accentCards": [
                    {"heading": f"Signal {i}",
                     "body": "A complete sentence carrying the point and the "
                             "figure behind it"}
                    for i in range(3)
                ]},
            ],
        }

    @pytest.fixture(scope="class")
    @staticmethod
    def rendered():
        import fitz

        from app.agent.tools.validate_rendered_output import ValidateRenderedOutputTool
        from app.measure.structure import count_blocks
        from app.renderer.pptx_renderer import PPTXRenderer
        from app.renderer.preview_generator import PreviewGenerator

        cls = TestASlideBuiltToTheReferenceBudgetRendersAtIt
        design_system = cls._reference_design_system()
        work_dir = tempfile.mkdtemp(prefix="ts_dense_")
        try:
            pptx_path = os.path.join(work_dir, "dense.pptx")
            PPTXRenderer(template_path=None, design_system=design_system).render(
                slides=[cls._slide()], metadata={"title": "Dense"},
                output_path=pptx_path,
            )
            pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
            assert pdf_path, "LibreOffice could not convert the dense deck"
            doc = fitz.open(pdf_path)
            try:
                return {
                    "design_system": design_system,
                    "blocks": count_blocks(doc[0]).block_count,
                    "words": len(doc[0].get_text().split()),
                    "issues": ValidateRenderedOutputTool._check_geometry(pdf_path),
                }
            finally:
                doc.close()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_the_slide_meets_its_budget_before_it_is_rendered(self, rendered):
        design_system = rendered["design_system"]
        budget_for_page = budget.budget_for(design_system, budget.CONTENT, 1.0)
        substance = budget.measure_slide(self._slide(), design_system)

        assert budget_for_page.verdict(substance) == budget.SlideBudget.ON

    def test_the_rendered_page_carries_what_the_budget_asked_for(self, rendered):
        budget_for_page = budget.budget_for(
            rendered["design_system"], budget.CONTENT, 1.0
        )

        assert budget_for_page.blocks_min <= rendered["blocks"] <= budget_for_page.blocks_max
        assert rendered["words"] >= budget_for_page.words_min

    def test_it_is_denser_than_the_baseline_this_epic_started_from(self, rendered):
        """`eval_scorecards/baseline-main` recorded 2 blocks and 31 words a
        slide against the reference's 10 and 208."""
        assert rendered["blocks"] >= 8
        assert rendered["words"] >= 120

    def test_and_it_is_still_a_clean_page(self, rendered):
        """Density that overflows or overlaps is not density."""
        assert rendered["issues"] == []


# ──────────────────────────────────────────────────────────────────────
# Two-sided balance
# ──────────────────────────────────────────────────────────────────────

class TestBalanceBodyWorksInBothDirections:
    @staticmethod
    def _balance(lines, size, width_in, height_in):
        from app.renderer.composition import balance_body

        return balance_body([f"A line of body copy number {i}" for i in range(lines)],
                            [size] * lines, width_in, height_in)

    def test_the_sparse_case_it_was_written_for_still_grows(self):
        """#56's defect: three bullets leaving the bottom half of the page
        blank. Nothing here may regress that."""
        balance = self._balance(3, 20.0, 6.0, 4.5)

        assert balance.size_scale > 1.0
        assert balance.paragraph_gap_pt > 0
        assert balance.top_offset_in > 0

    def test_a_block_that_already_fills_its_band_is_left_alone(self):
        from app.renderer.composition import NO_CHANGE

        assert self._balance(11, 20.0, 6.0, 4.0) == NO_CHANGE

    def test_an_overfull_block_is_tightened(self):
        balance = self._balance(14, 20.0, 6.0, 4.0)

        assert balance.size_scale < 1.0

    def test_tightening_stays_inside_the_decks_type_scale(self):
        """The mirror of MAX_GROWTH, for the same reason: the deck should
        look like one type scale, not one per slide."""
        from app.renderer.composition import MAX_TIGHTEN

        balance = self._balance(60, 20.0, 6.0, 3.0)

        assert balance.size_scale >= MAX_TIGHTEN

    def test_a_tightened_block_is_not_also_leaded_apart(self):
        balance = self._balance(14, 20.0, 6.0, 4.0)

        assert balance.paragraph_gap_pt == 0.0
        assert balance.top_offset_in == 0.0

    def test_growth_is_still_bounded(self):
        from app.renderer.composition import MAX_GROWTH

        assert self._balance(1, 14.0, 6.0, 5.0).size_scale <= MAX_GROWTH


# ──────────────────────────────────────────────────────────────────────
# The budget reaches the tools
# ──────────────────────────────────────────────────────────────────────

class TestThePlannerIsGivenPerKindTargets:
    @staticmethod
    def _guidance(design_system, material=1.0):
        from app.agent.tools.plan_presentation_structure import _density_guidance

        return _density_guidance(design_system, material)

    def test_it_states_a_figure_for_each_kind_of_page(self):
        guidance = self._guidance(DENSE)

        assert "content slide should come to roughly" in guidance
        assert "chart or table slide" in guidance
        assert "cover or a section divider" in guidance

    def test_thin_material_is_told_to_plan_fewer_slides_not_fuller_ones(self):
        guidance = self._guidance(DENSE, material=0.2)

        assert "Plan fewer slides rather than padding them" in guidance
        assert "inventing content to reach a density is not" in guidance

    def test_rich_material_is_told_to_carry_more_per_slide(self):
        guidance = self._guidance(DENSE, material=1.0)

        assert "rather than adding more slides" in guidance
        assert "a slide of invented detail is worse than a sparse one" in guidance

    def test_an_unmeasured_template_gets_no_target(self):
        assert self._guidance({}) == ""


class TestTheDesignerIsGivenTheSlidesOwnBudget:
    @staticmethod
    def _guidance(design_system, outline, key_points, attached, index=1):
        from app.agent.tools.design_slide_content import _density_guidance

        return _density_guidance(design_system, outline, key_points, attached, index)

    def test_it_states_blocks_and_words_for_this_slide(self):
        guidance = self._guidance(
            DENSE, {"contentType": "content"}, ["a", "b", "c", "d", "e"], [{}],
        )

        assert "How much this slide should carry" in guidance
        assert "blocks and" in guidance

    def test_a_cover_is_told_not_to_fill_itself(self):
        guidance = self._guidance(DENSE, {"contentType": "title"}, ["a"], [])

        assert "Do not fill it" in guidance

    def test_a_thin_slide_is_told_that_sparse_is_the_right_answer(self):
        guidance = self._guidance(DENSE, {"contentType": "content"}, ["one"], [])

        assert "The material behind this slide is thin" in guidance
        assert "Do not pad it out" in guidance

    def test_an_unmeasured_template_gets_no_budget(self):
        assert self._guidance({}, {"contentType": "content"}, ["a"], []) == ""

    def test_the_material_it_computed_is_recorded_for_the_validator(self):
        """The ordering is the point: the model never gets to say how thin
        its own material was."""
        token = run_context.set_slide_material({})
        try:
            self._guidance(DENSE, {"contentType": "content"},
                           ["a", "b", "c", "d", "e"], [{}], index=7)
            rich = run_context.get_slide_material(7)

            self._guidance(DENSE, {"contentType": "content"}, ["only one"], [],
                           index=8)
            thin = run_context.get_slide_material(8)
        finally:
            run_context.reset_slide_material(token)

        assert rich is not None and thin is not None
        assert rich > thin

    def test_nothing_is_recorded_outside_a_run(self):
        run_context.record_slide_material(3, 0.9)

        assert run_context.get_slide_material(3) is None


class TestTheValidatorHoldsASlideToIt:
    @staticmethod
    async def _validate(slide):
        from app.agent.tools.validate_slide_json import ValidateSlideJsonTool
        import json

        return await ValidateSlideJsonTool.run(slide_json=json.dumps(slide))

    @pytest.mark.asyncio
    async def test_a_sparse_slide_in_a_rich_run_fails_validation(self):
        design_token = run_context.set_design_system(DENSE)
        material_token = run_context.set_slide_material({4: 1.0})
        try:
            result = await self._validate(_slide(index=4, points=2, words_each=4))
        finally:
            run_context.reset_slide_material(material_token)
            run_context.reset_design_system(design_token)

        assert not result.success
        assert any("density budget" in e for e in result.data["errors"])

    @pytest.mark.asyncio
    async def test_the_same_slide_passes_when_the_material_is_thin(self):
        design_token = run_context.set_design_system(DENSE)
        material_token = run_context.set_slide_material({4: 0.2})
        try:
            result = await self._validate(_slide(index=4, points=2, words_each=4))
        finally:
            run_context.reset_slide_material(material_token)
            run_context.reset_design_system(design_token)

        assert result.success

    @pytest.mark.asyncio
    async def test_validation_outside_a_run_is_unchanged(self):
        result = await self._validate(_slide(index=4, points=2, words_each=4))

        assert result.success


class TestTheBundleKeepsTheMeasurement:
    def test_the_bundle_has_somewhere_to_put_it(self):
        """Without the column the density is measured at upload, published
        for one run, and lost - every later deck falls back to the default."""
        from app.db.models import DesignSystemBundle

        assert "density" in DesignSystemBundle.__table__.columns

    def test_the_built_in_design_system_still_carries_one(self):
        assert budget.density_brief_available(get_default_design_system())


class TestTheArchetypeSurvivesToTheDesignedSlide:
    """
    ``archetype_for`` reads ``archetype`` or ``contentType`` off a slide, and
    ``design_slide_content`` used to emit neither - only slideIndex, layoutId,
    layoutName, section, titleRegister and contents.

    So every designed slide fell through to CONTENT, and the consequences were
    not cosmetic. The cover/divider guard in ``validate_density`` was dead for
    real slides. An exhibit slide was written to a 0.55 share and judged
    against 1.0, while UNDER_TOLERANCE is also 0.55 - so a chart slide landing
    exactly on the target it was given sat exactly on the validator's minimum,
    and any shortfall reported "under its density budget". A cover with enough
    material behind it was told to add a chart to it.
    """

    @staticmethod
    def _designed(outline):
        """
        What design_slide_content does to its parsed response.

        The real function, not a restatement of it - a test that reimplements
        the thing it is testing passes forever.
        """
        from app.agent.tools.design_slide_content import carry_archetype

        return carry_archetype({"slideIndex": 0, "layoutId": "1", "contents": []},
                               outline)

    def test_a_content_type_reaches_the_slide(self):
        slide = self._designed({"contentType": "chart"})

        assert budget.archetype_for(slide) == budget.EXHIBIT

    def test_an_explicit_archetype_reaches_the_slide(self):
        slide = self._designed({"archetype": "divider"})

        assert budget.archetype_for(slide) == budget.DIVIDER

    def test_a_cover_is_not_judged_as_a_content_page(self):
        """The guard that was dead for every real slide."""
        cover = self._designed({"contentType": "title"})
        cover["contents"] = [
            {"type": "text", "placeholderIdx": 0,
             "runs": [{"text": "A deck about something"}]},
        ]

        errors, _ = budget.validate_density(cover, DENSE, 1.0)

        assert errors == []

    def test_an_outline_with_neither_field_still_designs(self):
        """A plan that never typed its slides must not break."""
        slide = self._designed({})

        assert budget.archetype_for(slide) == budget.CONTENT
        assert "contentType" not in slide

    def test_a_slide_that_typed_itself_is_not_overruled(self):
        """The outline did not know what the model worked out while writing."""
        from app.agent.tools.design_slide_content import carry_archetype

        slide = carry_archetype({"archetype": "divider"}, {"contentType": "chart"})

        assert budget.archetype_for(slide) == budget.DIVIDER
