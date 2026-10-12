"""
Whether a figure on a page can be traced to the sentence it came from.

A deck can be beautiful and unsourced, and this is the axis a source-grounded
generator is actually defined by. Nothing tied the two together:
`analyze_document` and `web_search` both existed, pgvector was in the stack,
and none of it survived into a slide - so "which sentence of which source does
this number come from" was unanswerable, and a pass-3 deck stated Gartner and
IDC figures with no attribution anywhere on any page.

Two properties, and the second is the one that keeps the first honest.

A figure is checked **mechanically**. Whether "61%" appears in a span the run
retrieved is a fact; asking a model to grade its own sourcing is exactly the
check that passes when it should fail.

And the check runs **in code**. The validator this follows was a tool the
model could decline to call, and it was invoked zero times across every
observed run.
"""
import pytest

from app.grounding.check import ground, states_researched_figures
from app.grounding.claim import Evidence, figures_in, support_for

SNIPPET = ("Cyber risk was named by 61% of respondents, the most cited "
           "near-term risk in this year's survey of chief risk officers.")


@pytest.fixture
def evidence():
    return Evidence.from_research([
        {"url": "https://example.test/survey", "title": "CRO Survey",
         "snippet": SNIPPET},
    ])


class TestWhatCountsAsAFigure:
    def test_a_measurement_does(self):
        assert "61%" in figures_in("named by 61% of respondents")
        assert "$4.2bn" in figures_in("assets of $4.2bn under management")
        assert "3x" in figures_in("growth of 3x on last year")
        assert "12,400" in figures_in("across 12,400 incidents")

    def test_a_year_does_not(self):
        """Demanding a source for "in 2026" would flag every deck ever
        written, and a checker nobody trusts is a checker nobody runs."""
        assert figures_in("the outlook for 2026 and 2025") == []

    def test_prose_with_no_numbers_has_none(self):
        assert figures_in("Risk management is changing quickly.") == []

    def test_one_figure_is_reported_once(self):
        """`$4.2bn` and `4.2bn` are the same figure read less completely."""
        assert figures_in("assets of $4.2bn") == ["$4.2bn"]

    def test_it_survives_empty_and_missing_text(self):
        assert figures_in("") == []
        assert figures_in(None) == []


class TestAFigureIsTiedToTheSpanThatCarriesIt:
    def test_a_stated_figure_finds_its_span(self, evidence):
        claim = support_for(
            "61%", "Cyber remains the most cited risk, named by 61% of "
                   "respondents.", evidence)
        assert claim is not None
        assert claim.span.source_id == "https://example.test/survey"
        assert "61%" in claim.span.text

    def test_a_figure_nothing_carries_finds_nothing(self, evidence):
        assert support_for("48%", "Regulatory change follows at 48%.",
                           evidence) is None

    def test_the_same_number_about_something_else_is_not_support(self):
        """Without this, "61%" on a slide about cyber risk is "supported" by a
        span about 61% of something else entirely - which is worse than no
        check at all, because it puts a source line under a number the source
        does not make."""
        unrelated = Evidence.from_research([
            {"url": "https://example.test/other", "title": "Other",
             "snippet": "Some 61% of households own a dishwasher."},
        ])
        assert support_for(
            "61%", "Cyber remains the most cited near-term risk, named by 61% "
                   "of respondents.", unrelated) is None

    def test_a_claim_says_where_a_reader_should_look(self, evidence):
        claim = support_for("61%", "named by 61% of respondents", evidence)
        payload = claim.to_dict()
        assert payload["locator"] == "https://example.test/survey"
        assert payload["quote"]
        assert payload["sourceTitle"] == "CRO Survey"

    def test_nothing_claims_the_span_means_what_the_slide_says(self, evidence):
        """The honest limit of a mechanical check, stated in the record."""
        claim = support_for("61%", "named by 61% of respondents", evidence)
        assert claim.basis == "exact"


class TestADocumentSpanIsLocatableByOffset:
    def test_a_figure_resolves_to_a_place_in_the_file(self):
        text = ("Introduction. " * 20) + SNIPPET + (" Conclusion." * 20)
        evidence = Evidence.from_document(text, source_id="/tmp/report.pdf",
                                          title="report.pdf")
        claim = support_for("61%", "named by 61% of respondents", evidence)
        assert claim is not None
        assert "#" in claim.span.locator
        start, end = claim.span.locator.split("#")[1].split("-")
        assert text[int(start):int(end)].find("61%") >= 0

    def test_a_span_carries_the_words_that_qualify_its_figure(self):
        """A span of "61" is not evidence of anything."""
        evidence = Evidence.from_document(SNIPPET, source_id="d")
        assert all(len(span.text) > 20 for span in evidence.spans)


class TestTheDeckIsCheckedInCode:
    def _deck(self):
        return [{
            "slideIndex": 0,
            "contents": [{"type": "text", "runs": [
                {"text": "Cyber remains the most cited near-term risk, "
                         "named by 61% of respondents."},
                {"text": "Regulatory change follows at 48%, up nine points."},
            ]}],
        }]

    def test_a_supported_figure_becomes_a_claim_on_the_slide(self, evidence):
        slides = self._deck()
        claims, _ = ground(slides, evidence)
        assert claims
        assert slides[0]["claims"][0]["figure"] == "61%"

    def test_an_unsupported_figure_is_named(self, evidence):
        _, gaps = ground(self._deck(), evidence)
        assert [g.figure for g in gaps] == ["48%"]
        assert "slide 1" in gaps[0].describe()

    def test_a_figure_with_nothing_behind_it_is_reported_not_removed(
            self, evidence):
        """The deck is the user's and the copy is theirs to keep. What they
        should not have is a page asserting a measurement while the run has no
        record of where it came from."""
        slides = self._deck()
        ground(slides, evidence)
        runs = slides[0]["contents"][0]["runs"]
        assert any("48%" in run["text"] for run in runs)

    def test_a_deck_with_no_figures_needs_no_attribution(self, evidence):
        slides = [{"slideIndex": 0, "contents": [{"type": "text", "runs": [
            {"text": "Risk management is changing quickly."}]}]}]
        claims, gaps = ground(slides, evidence)
        assert not claims and not gaps
        assert not states_researched_figures(slides)

    def test_a_page_carrying_a_claim_owes_the_reader_attribution(self, evidence):
        """What the on-page source line fires on. Whether the deck also gets a
        numbered trail and a Sources appendix stays the user's preference - the
        two were conflated, and a deck cited Gartner and IDC with nothing on
        any page."""
        slides = self._deck()
        ground(slides, evidence)
        assert states_researched_figures(slides)

    def test_it_runs_before_the_render_and_is_not_a_tool(self):
        import inspect

        from app.agent.loop import AgentLoop

        assert "_ground_the_deck" in inspect.getsource(AgentLoop._phase_render)
        from app.agent.registry import ToolRegistry

        assert not any("ground" in name for name in
                       getattr(ToolRegistry, "_tools", {}) or {})

    def test_a_malformed_slide_does_not_break_the_check(self, evidence):
        claims, gaps = ground(["not a slide", {}, {"contents": None}], evidence)
        assert claims == [] and gaps == []


class TestTheDocumentTextNeverReachesTheModel:
    def test_it_is_stripped_from_a_tool_result(self):
        """A whole uploaded document in the conversation would spend the
        context window on text the model already has a summary of."""
        from app.agent.redact import redact_binary

        payload = {"content": "a summary", "sourceText": "x" * 10000}
        assert "sourceText" not in redact_binary(payload)
        assert redact_binary(payload)["content"] == "a summary"
