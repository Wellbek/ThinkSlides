"""
The style-fidelity harness: does it measure what it claims to measure?

Two things are under test here. The first is the machinery - the corpus split
holds, a rendered deck can be measured, a scorecard comes out diffable. The
second is the harness's own most important property: that a deck can be made
denser without being made better, and the scorecard has to say so. Without
that, the density work in the epic could raise every number while the output
got worse.
"""

import json
import os
import shutil
import tempfile

import pytest

from app.eval import corpus, reference, scorecard
from app.eval.deck_metrics import (
    DeckProfile, build_profile, headline_register, profile_pdf,
    title_states_a_claim,
)
from app.eval.fixture_decks import DECKS
from app.eval.page_metrics import PageMetrics, measure_pdf, signature_distance
from app.renderer.preview_generator import PreviewGenerator
from app.renderer.pptx_renderer import PPTXRenderer


def _render(slides, theme, work_dir, name="deck"):
    pptx_path = os.path.join(work_dir, f"{name}.pptx")
    PPTXRenderer(template_path=None, color_theme=theme).render(
        slides=slides, metadata={"title": name}, output_path=pptx_path,
    )
    pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
    assert pdf_path, f"LibreOffice could not convert {name}"
    return pptx_path, pdf_path


class TestTheCorpusSplitIsEncodedNotConventional:
    def test_the_two_halves_do_not_overlap(self):
        corpus.assert_split_is_disjoint()

    def test_every_page_is_inside_the_reference_deck(self):
        for page in (*corpus.DESIGN_PAGES, *corpus.VALIDATION_PAGES):
            assert 1 <= page <= corpus.REFERENCE_PAGE_COUNT

    def test_opening_a_validation_page_raises(self):
        with pytest.raises(corpus.ValidationPeek):
            corpus.assert_design_page(corpus.VALIDATION_PAGES[0])

    def test_opening_a_design_page_is_allowed(self):
        for page in corpus.DESIGN_PAGES:
            corpus.assert_design_page(page)

    def test_a_retired_validation_page_is_no_longer_held_out(self, monkeypatch):
        retired = corpus.VALIDATION_PAGES[0]
        monkeypatch.setattr(corpus, "RETIRED_VALIDATION_PAGES", (retired,))

        assert retired not in corpus.active_validation_pages()
        corpus.assert_design_page(retired)


class TestTheReferenceProfileIsCommitted:
    def test_both_splits_load_without_the_pdf(self):
        for split in (reference.DESIGN_SPLIT, reference.VALIDATION_SPLIT):
            profile = reference.load_reference_profile(split)

            assert profile.page_count > 0
            assert profile.words_median > 0

    def test_the_fixture_records_which_pages_it_measured(self):
        metadata = reference.reference_metadata()

        assert metadata["designPages"] == list(corpus.DESIGN_PAGES)
        assert metadata["validationPages"] == list(corpus.active_validation_pages())

    def test_the_reference_is_denser_than_a_bulleted_deck(self):
        """The gap this epic exists to close, stated as a number."""
        profile = reference.load_reference_profile()

        assert profile.words_median > 100
        assert profile.blocks_median > 5

    def test_the_reference_pairs_a_serif_display_over_a_sans_body(self):
        profile = reference.load_reference_profile()

        assert profile.display_is_serif_fraction > 0.5
        assert profile.display_body_ratio > 1.3


class TestMeasuringARenderedDeck:
    @pytest.fixture(scope="class")
    @staticmethod
    def rendered():
        work_dir = tempfile.mkdtemp(prefix="ts_fidelity_")
        slides, theme = DECKS["numbers"]
        try:
            _, pdf_path = _render(slides, theme, work_dir, "numbers")
            yield {"pdf": pdf_path, "slides": slides}
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_every_slide_is_measured(self, rendered):
        pages = measure_pdf(rendered["pdf"])

        assert len(pages) == len(rendered["slides"])
        assert all(isinstance(page, PageMetrics) for page in pages)

    def test_a_page_reports_its_ground_and_its_type(self, rendered):
        pages = measure_pdf(rendered["pdf"])

        assert all(page.grounds for page in pages)
        assert any(page.body_size_pt for page in pages)

    def test_the_deck_profile_carries_the_deck_level_shape(self, rendered):
        profile = profile_pdf(rendered["pdf"], "numbers")

        assert profile.page_count == len(rendered["slides"])
        assert profile.ink_mean > 0
        assert profile.type_ramp
        assert profile.grounds

    def test_a_light_dark_rhythm_shows_up_as_more_than_one_ground(self, rendered):
        profile = profile_pdf(rendered["pdf"], "numbers")

        assert len(profile.grounds) > 1


class TestTheScorecard:
    @pytest.fixture(scope="class")
    @staticmethod
    def card():
        work_dir = tempfile.mkdtemp(prefix="ts_fidelity_card_")
        slides, theme = DECKS["numbers"]
        try:
            _, pdf_path = _render(slides, theme, work_dir, "numbers")
            generated = profile_pdf(pdf_path, "numbers")
            yield scorecard.score(generated, reference.load_reference_profile())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_every_score_is_a_fraction(self, card):
        for group in (card.style, card.substance):
            for metric in group.values():
                assert metric.score is None or 0.0 <= metric.score <= 1.0

    def test_style_and_substance_are_reported_separately(self, card):
        assert card.style_score is not None
        assert card.substance_score is not None
        assert "styleScore" in card.to_dict()
        assert "substanceScore" in card.to_dict()

    def test_a_metric_with_nothing_to_compare_is_excluded_not_zeroed(self):
        empty = DeckProfile(name="empty")
        card = scorecard.score(empty, DeckProfile(name="no-reference"))

        assert card.style_score is None

    def test_the_json_is_stable_between_identical_runs(self, card):
        first = tempfile.mkdtemp(prefix="ts_card_a_")
        second = tempfile.mkdtemp(prefix="ts_card_b_")
        try:
            path_a, _ = scorecard.write(card, first)
            path_b, _ = scorecard.write(card, second)

            with open(path_a) as a, open(path_b) as b:
                assert a.read() == b.read()
        finally:
            shutil.rmtree(first, ignore_errors=True)
            shutil.rmtree(second, ignore_errors=True)

    def test_the_json_is_a_flat_diffable_document(self, card):
        directory = tempfile.mkdtemp(prefix="ts_card_")
        try:
            json_path, md_path = scorecard.write(card, directory)
            with open(json_path) as handle:
                data = json.load(handle)

            assert set(data) >= {"deck", "reference", "style", "substance"}
            assert os.path.exists(md_path)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_current_output_is_far_from_the_reference(self, card):
        """
        The baseline this epic is measured against. If this ever passes
        comfortably without the epic's work landing, the harness has stopped
        measuring anything.
        """
        assert card.style["words_per_slide"].score < 0.5
        assert card.style["blocks_per_slide"].score < 0.5


class TestOvershootingTheReferenceIsNotAnImprovement:
    def test_twice_the_reference_scores_the_same_as_half_of_it(self):
        over = scorecard._ratio_metric("words", 200.0, 100.0)
        under = scorecard._ratio_metric("words", 50.0, 100.0)

        assert over.score == under.score == 0.5

    def test_a_rate_is_not_penalised_for_exceeding_the_reference(self):
        metric = scorecard._fraction_metric("sourced", 1.0, 0.8)

        assert metric.score == 1.0


class TestTheGuardAgainstFiller:
    """
    The harness's most important job. A deck can always be made denser by
    repeating itself, and every density metric will improve when it is. The
    substance group is what has to fall instead - it is the documented guard
    on the density-budget work.
    """

    FILLER_SLIDE_COUNT = 6
    FILLER_SENTENCE = (
        "The programme delivers strategic value across the organisation and "
        "supports the operating model through the transformation."
    )

    @classmethod
    def _filler_deck(cls):
        return [
            {"slideIndex": index, "layoutId": "content", "contents": [
                {"type": "text", "runs": [
                    {"text": "Strategic overview", "bold": True, "bullet": False}]},
                {"type": "text", "runs": [
                    {"text": cls.FILLER_SENTENCE, "bullet": True, "level": 0}
                    for _ in range(6)
                ]},
            ]}
            for index in range(cls.FILLER_SLIDE_COUNT)
        ]

    @classmethod
    def _substantive_deck(cls):
        findings = [
            "Revenue grew 12% in 2025, driven entirely by price rather than volume",
            "International margin fell to 11.2% as store openings outpaced traffic",
            "Loyalty members now account for 57% of transactions, up from 41%",
            "Channel Development returned 42.1% operating margin on $1.8B of revenue",
            "Cold beverages contributed two thirds of the mix shift since 2023",
            "Labour inflation added 180 basis points of cost in the second half",
        ]
        details = [
            "Segment disclosure, fiscal 2025 annual report, filed February 2026",
            "Store-level traffic counts, weekly panel, four quarters to December",
            "Loyalty programme enrolment and redemption ledger, December snapshot",
            "Licensing and packaged-goods contribution, segment note 12",
            "Menu mix analysis, beverage category, rolling twelve months",
            "Wage schedules and scheduled hours, corporate stores only",
        ]
        return [
            {"slideIndex": index, "layoutId": "content", "contents": [
                {"type": "text", "runs": [
                    {"text": finding, "bold": True, "bullet": False}]},
                {"type": "text", "runs": [
                    {"text": detail, "bullet": True, "level": 0},
                    {"text": f"Source: {detail.split(',')[0]}", "bullet": True, "level": 0},
                ]},
            ]}
            for index, (finding, detail) in enumerate(zip(findings, details))
        ]

    @pytest.fixture(scope="class")
    @classmethod
    def cards(cls):
        work_dir = tempfile.mkdtemp(prefix="ts_filler_")
        try:
            reference_profile = reference.load_reference_profile()
            out = {}
            for name, slides in (("filler", cls._filler_deck()),
                                 ("substantive", cls._substantive_deck())):
                _, pdf_path = _render(slides, "professional", work_dir, name)
                profile = profile_pdf(pdf_path, name)
                out[name] = (profile, scorecard.score(profile, reference_profile))
            yield out
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_the_filler_deck_really_is_the_denser_one(self, cards):
        filler, substantive = cards["filler"][0], cards["substantive"][0]

        assert filler.words_median > substantive.words_median

    def test_filler_loses_on_saying_different_things(self, cards):
        filler, substantive = cards["filler"][0], cards["substantive"][0]

        assert filler.distinct_content < substantive.distinct_content
        assert filler.lexical_variety < substantive.lexical_variety

    def test_filler_loses_on_headlines_that_state_something(self, cards):
        filler, substantive = cards["filler"][0], cards["substantive"][0]

        assert filler.informative_title_fraction < substantive.informative_title_fraction

    def test_the_substance_score_falls_even_as_density_rises(self, cards):
        filler_card = cards["filler"][1]
        substantive_card = cards["substantive"][1]

        assert filler_card.substance_score < substantive_card.substance_score


class TestHeadlineRegister:
    @pytest.mark.parametrize("title", [
        "Revenue grew 12% on price rather than volume",
        "Applied AI recorded the highest innovation score of all 14 trends",
        "International margin fell to 11.2% while stores expanded",
    ])
    def test_a_finding_is_a_claim(self, title):
        assert headline_register(title) == "claim"
        assert title_states_a_claim(title)

    @pytest.mark.parametrize("title", [
        "Revenue", "Segment performance", "Our approach", "Strategic overview",
    ])
    def test_a_topic_is_a_label(self, title):
        assert headline_register(title) == "label"
        assert not title_states_a_claim(title)

    def test_the_chapter_grammar_is_its_own_register(self):
        assert headline_register("Why should leaders pay attention?") == "question"


class TestArchetypeSignatures:
    def test_a_page_is_closest_to_itself(self):
        profile = reference.load_reference_profile(reference.DESIGN_SPLIT)
        first = profile.signatures[0]

        assert signature_distance(first, first) == 0.0

    def test_distance_is_bounded(self):
        profile = reference.load_reference_profile(reference.DESIGN_SPLIT)

        for signature in profile.signatures:
            for other in profile.signatures:
                assert 0.0 <= signature_distance(signature, other) <= 1.0

    def test_an_empty_signature_is_maximally_distant(self):
        assert signature_distance([], [0.1, 0.2]) == 1.0


class TestBuildProfileWithoutText:
    def test_substance_is_left_at_zero_when_no_text_was_supplied(self):
        page = PageMetrics(page_number=1, width_pt=960, height_pt=540,
                           ink_coverage=0.5, word_count=200, block_count=8)
        profile = build_profile("no-text", [page])

        assert profile.ink_mean == 0.5
        assert profile.distinct_content == 0.0
