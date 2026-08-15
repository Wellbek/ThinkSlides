"""
Action titles: knowing which register a page is in, and earning it.

The headline of reference page 3 is "Applied AI recorded the highest
innovation score of all 14 trends, while clean energy drew the most interest
and investment." A reader who reads only that deck's headlines has read its
argument; a reader who reads only ours has read a table of contents.

The risk this brings with it is the one #78 recorded: told to write claims, a
model writes confident claims from thin data. So the tests here are weighted
towards the guard rather than the feature - a claim has to be *backed*, and a
slide with nothing to point at falls back to a label instead of inventing a
finding.
"""

import json

import pytest

from app import titles
from app.agent.tools.design_slide_content import _resolve_title_register
from app.agent.tools.validate_slide_json import ValidateSlideJsonTool


def _slide(title, register=None, contents=None, **extra):
    slide = {
        "slideIndex": 1, "layoutId": "content", "layoutName": "Content",
        "contents": [
            {"type": "text", "placeholderIdx": 0,
             "runs": [{"text": title, "bold": True}]},
            *(contents or []),
        ],
    }
    if register is not None:
        slide["titleRegister"] = register
    slide.update(extra)
    return slide


CHART = {"type": "chart", "chart": {
    "chartType": "bar", "categories": ["2024", "2025"],
    "series": [{"name": "Revenue ($B)", "values": [36.2, 37.4]}]}}
BULLETS = {"type": "text", "placeholderIdx": 1, "runs": [
    {"text": "A qualitative point with no figures in it", "bullet": True}]}
FIGURE_BULLETS = {"type": "text", "placeholderIdx": 1, "runs": [
    {"text": "Revenue reached $37.4B in 2025", "bullet": True}]}


class TestWhatCountsAsAClaim:
    @pytest.mark.parametrize("title", [
        "Applied AI recorded the highest innovation score of all 14 trends",
        "Revenue grew 12% on price rather than volume",
        "International margin fell to 11.2% while store openings accelerated",
        "Loyalty members now account for 57% of transactions",
    ])
    def test_a_finding_is_a_claim(self, title):
        assert titles.states_a_claim(title)
        assert titles.classify(title) == titles.CLAIM

    @pytest.mark.parametrize("title", [
        "Revenue",
        "Segment performance",
        "Our approach",
        "Advanced connectivity",
        "Leading indicators",
        "Cloud and edge computing",
    ])
    def test_a_topic_is_a_label(self, title):
        assert not titles.states_a_claim(title)
        assert titles.classify(title) == titles.LABEL

    def test_a_statement_without_a_figure_or_comparison_is_still_a_label(self):
        """"Revenue increased" is a statement and not yet a finding."""
        assert titles.classify("Revenue increased this year") == titles.LABEL

    def test_the_chapter_grammar_is_its_own_register(self):
        assert titles.classify("Why should leaders pay attention?") == titles.QUESTION

    def test_a_headline_that_is_a_paragraph_is_not_a_headline(self):
        assert not titles.states_a_claim(" ".join(["word"] * 40) + " grew 12%")

    def test_an_empty_title_classifies_rather_than_crashing(self):
        assert titles.classify("") == titles.LABEL
        assert titles.classify(None) == titles.LABEL


class TestWhyAClaimFails:
    def test_it_says_a_missing_verb(self):
        problems = titles.claim_problems("Segment performance by region")

        assert any("no verb" in p for p in problems)

    def test_it_says_a_missing_figure_or_comparison(self):
        problems = titles.claim_problems("The programme delivered its outcomes")

        assert any("no figure and no comparison" in p for p in problems)

    def test_it_says_a_question_is_the_wrong_register(self):
        problems = titles.claim_problems("Why should leaders pay attention?")

        assert any("register is 'question'" in p for p in problems)

    def test_a_real_claim_has_no_problems(self):
        assert titles.claim_problems("Revenue grew 12% on price") == []


class TestASlideHasToBeAbleToBackAClaim:
    @pytest.mark.parametrize("evidence", [CHART, FIGURE_BULLETS])
    def test_data_on_the_slide_supports_one(self, evidence):
        assert titles.supports_a_claim(_slide("Anything", contents=[evidence]))

    def test_prose_with_no_figures_does_not(self):
        assert not titles.supports_a_claim(_slide("Anything", contents=[BULLETS]))

    def test_a_typed_block_of_figures_supports_one(self):
        slide = _slide("Anything", contents=[
            {"type": "statCallout",
             "statCallouts": [{"value": "56%", "caption": "Adoption"}]},
        ])

        assert titles.supports_a_claim(slide)

    def test_a_declared_claim_is_demoted_when_nothing_backs_it(self):
        slide = _slide("Revenue grew 12% on price", titles.CLAIM, contents=[BULLETS])

        assert titles.register_for(slide) == titles.LABEL

    def test_a_declared_claim_stands_when_something_does(self):
        slide = _slide("Revenue grew 12% on price", titles.CLAIM, contents=[CHART])

        assert titles.register_for(slide) == titles.CLAIM


class TestValidationRejectsAnUnearnedClaim:
    @pytest.mark.asyncio
    async def test_a_topic_label_declared_as_a_claim_is_rejected(self):
        slide = _slide("Segment performance", titles.CLAIM, contents=[CHART])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is False
        assert any("declared a claim" in e for e in result.data["errors"])

    @pytest.mark.asyncio
    async def test_a_claim_on_a_slide_with_no_evidence_is_rejected(self):
        slide = _slide("Revenue grew 12% on price", titles.CLAIM, contents=[BULLETS])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is False
        assert any("no chart, table, figures" in e for e in result.data["errors"])

    @pytest.mark.asyncio
    async def test_a_real_claim_passes(self):
        slide = _slide("Revenue grew 12% on price rather than volume",
                       titles.CLAIM, contents=[CHART])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True, result.data["errors"]

    @pytest.mark.asyncio
    async def test_a_question_register_title_is_left_alone(self):
        """
        The chapter grammar is deliberate. A question has no verb-and-figure
        requirement and must not be pushed towards one.
        """
        slide = _slide("Why should leaders pay attention?", titles.QUESTION,
                       contents=[BULLETS])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True, result.data["errors"]

    @pytest.mark.asyncio
    async def test_a_question_register_title_that_asks_nothing_warns(self):
        slide = _slide("Leaders and attention", titles.QUESTION, contents=[BULLETS])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True
        assert any("declared a question" in w for w in result.data["warnings"])

    @pytest.mark.asyncio
    async def test_a_label_is_never_held_to_the_claim_bar(self):
        slide = _slide("Segment performance", titles.LABEL, contents=[BULLETS])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True

    @pytest.mark.asyncio
    async def test_a_slide_declaring_no_register_validates_as_before(self):
        slide = _slide("Segment performance", contents=[BULLETS])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True

    @pytest.mark.asyncio
    async def test_an_unknown_register_warns_rather_than_failing(self):
        slide = _slide("Anything", "manifesto", contents=[BULLETS])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True
        assert any("unknown titleRegister" in w for w in result.data["warnings"])


class TestTheContentDesignerResolvesTheRegister:
    """
    The register is decided where the answer is knowable: after this slide's
    visuals have been built. That is the ordering #60 established for body
    copy, and it applies with more force to a headline that is supposed to
    state what the chart proves.
    """

    def test_a_claim_stands_when_a_visual_was_built(self):
        register, guidance, note = _resolve_title_register(
            {"titleRegister": "claim"}, [{"type": "chart"}], ["A point"]
        )

        assert register == titles.CLAIM
        assert "state its FINDING" in guidance
        assert note == ""

    def test_a_claim_stands_when_the_key_points_carry_figures(self):
        register, _, _ = _resolve_title_register(
            {"titleRegister": "claim"}, [], ["Revenue reached $37.4B in 2025"]
        )

        assert register == titles.CLAIM

    def test_a_claim_is_demoted_when_nothing_backs_it(self):
        register, guidance, note = _resolve_title_register(
            {"titleRegister": "claim"}, [], ["A qualitative point"]
        )

        assert register == titles.LABEL
        assert "Do NOT invent a finding" in guidance
        assert "build_chart" in note

    def test_a_question_is_passed_through_verbatim(self):
        register, guidance, _ = _resolve_title_register(
            {"titleRegister": "question"}, [], []
        )

        assert register == titles.QUESTION
        assert "EXACTLY as written" in guidance

    def test_no_declared_register_defaults_to_a_label(self):
        register, _, _ = _resolve_title_register({}, [], [])

        assert register == titles.LABEL


class TestThePlannerAndTheHarnessShareOneDefinition:
    def test_the_planner_asks_for_a_register(self):
        import inspect

        from app.agent.tools import plan_presentation_structure as planner

        source = inspect.getsource(planner)
        assert '"titleRegister"' in source
        assert "lying about its own evidence" in source

    def test_the_eval_harness_reads_the_same_rule(self):
        from app.measure.deck_metrics import headline_register, title_states_a_claim

        assert headline_register("Revenue grew 12% on price") == titles.CLAIM
        assert title_states_a_claim("Revenue grew 12% on price")
        assert headline_register("Segment performance") == titles.LABEL

    def test_that_makes_the_metric_a_conformance_check(self):
        """
        Sharing the definition is deliberate and worth being explicit about:
        the title metric measures whether the deck held to the standard, not
        whether the standard is any good. The guard against filler rests on
        the three metrics that are independent of it.
        """
        import inspect

        from app.measure import deck_metrics

        source = inspect.getsource(deck_metrics.title_states_a_claim)
        assert "conformance check" in source
