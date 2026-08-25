"""
The instrument has to measure the property, not one publication's spelling of
it.

The eval harness scores a generated deck by comparing measured properties
against a reference profile. That only means anything if the measurements are
right about decks the harness has never seen. Profiling three publication-grade
references - an EY/IIF survey report, Microsoft's Work Trend Index and
Blackstone's BXSL investor deck - showed they were not:

* `informative_title_fraction` scored the Work Trend Index at 0.07, on a deck
  whose pages are titled "Employees turn to AI for things that humans can't
  provide" and "Human-agent teams will upend the org chart".
* `source_line_fraction` scored BXSL at 0.06, on a deck that notes almost
  every page.

Both were false. Iterating the generator against numbers like these would have
been chasing the wrong target - so these tests pin the two causes, and pin the
boundaries the fixes must not cross.
"""

import re

from app.measure.deck_metrics import _has_word
from app.measure.page_metrics import _SOURCE_LINE_RE
from app.titles import classify, has_finite_verb, states_a_claim


def _is_source_line(text: str) -> bool:
    return bool(_SOURCE_LINE_RE.match(text))


class TestAHeadlineHasWordsInIt:
    """
    "The largest type on the page" is not the same as "the headline".

    A stat panel sets its figures larger than its title, a section opener sets
    a ghosted numeral larger than anything else, and on a sparse page the folio
    can be the biggest run. Every one of those was being read as the page's
    headline and then classified.
    """

    def test_a_stat_panels_figures_are_not_a_headline(self):
        assert _has_word("$212B $125B $121B $78B") is False
        assert _has_word("11.4% 11.7% 97.6% $0.77") is False

    def test_a_section_numeral_is_not_a_headline(self):
        assert _has_word("04") is False
        assert _has_word("1") is False

    def test_a_rule_or_mark_is_not_a_headline(self):
        assert _has_word("=") is False
        assert _has_word("760+ 17") is False

    def test_a_real_headline_is_kept(self):
        assert _has_word("Employees turn to AI") is True

    def test_a_headline_of_names_and_acronyms_is_kept(self):
        """Deliberately not a dictionary check: publication headlines are full
        of names and tickers, and requiring known words would drop them."""
        assert _has_word("BXSL has led peer BDCs") is True
        assert _has_word("BXCI") is True


class TestAQualitativeFindingIsStillAClaim:
    """
    A claim used to need a verb *and* a comparison or a quantity, which made
    the metric blind to every finding stated without a number.
    """

    def test_a_finding_with_no_number_in_it_is_a_claim(self):
        assert states_a_claim("Employees turn to AI for things that humans can't provide")
        assert states_a_claim("Human-agent teams will upend the org chart")
        assert states_a_claim("Business demands outpace human capacity")
        assert states_a_claim("BXSL has led peer BDCs across many key metrics")

    def test_a_quantified_finding_is_still_a_claim(self):
        assert states_a_claim("Revenue grew 12% on price")

    def test_a_topic_label_is_still_a_label(self):
        assert classify("Executive summary") == "label"
        assert classify("High degree of shareholder alignment") == "label"
        assert classify("Blackstone Secured Lending Fund (BXSL)") == "label"
        assert classify("Journey to the Frontier Firm") == "label"

    def test_a_participle_alone_does_not_make_a_claim(self):
        """
        The boundary the relaxation must not cross. "Advanced connectivity in
        emerging markets" is a noun phrase, and the only verb-ish thing in it
        is a participle - so it still has to bring a comparison or a quantity.
        """
        assert has_finite_verb("Advanced connectivity in emerging markets") is False
        assert states_a_claim("Advanced connectivity in emerging markets") is False

    def test_a_participle_with_something_to_weigh_is_a_claim(self):
        assert states_a_claim("Advanced connectivity grew 40% in emerging markets")

    def test_a_headline_too_short_to_assert_anything_is_a_label(self):
        assert classify("Revenue") == "label"
        assert classify("Revenue is up") == "label"

    def test_a_question_is_still_a_question(self):
        assert classify("What is holding productivity back?") == "question"

    def test_every_curated_verb_is_a_single_lowercase_word(self):
        """The list is curated so the rule can be explained to whoever hits it.
        An entry with whitespace or capitals would silently never match."""
        from app.titles import CLAIM_VERBS

        assert all(re.fullmatch(r"[a-z]+", verb) for verb in CLAIM_VERBS)


class TestAnAttributionLineIsNotAlwaysCalledSource:
    """
    Matching only "Source:" made the metric a test of one house's vocabulary.
    An investor deck opens the line "Note:", a survey report "Survey
    question:", a research piece "Methodology:".
    """

    def test_the_spellings_publications_actually_use_all_count(self):
        assert _is_source_line("Source: Company filings")
        assert _is_source_line("Sources: Company filings")
        assert _is_source_line("Note: All figures are as of March 31, 2026")
        assert _is_source_line("Survey question: What is your primary reason")
        assert _is_source_line("Survey questions: How familiar are you")
        assert _is_source_line("Methodology: 1,200 respondents")
        assert _is_source_line("Data: internal telemetry")

    def test_it_stays_anchored_at_the_start_of_the_line(self):
        """Loosening this to any sentence mentioning data would start counting
        body copy, which is the failure in the other direction."""
        assert not _is_source_line("Our data: the strongest quarter yet")
        assert not _is_source_line("We note: the market softened")

    def test_a_keyword_with_no_colon_is_not_an_attribution_line(self):
        assert not _is_source_line("Data reflects the top three workforce strategies")
        assert not _is_source_line("Notes from the field")
