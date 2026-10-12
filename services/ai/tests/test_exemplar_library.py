"""
One reference cannot represent "publication grade".

Measured side by side, four decks that are all unarguably excellent disagree
on nearly every property the harness scores - words per page spans 204 to 632,
charts per page 0.14 to 1.91. Scoring every generated deck against a single
profile therefore steered every deck toward one house, which is the opposite
of being able to produce a wide range.

The library is a *spread* of what good looks like, not a set of buckets. These
tests pin that: that nothing sorts a deck into an exemplar by its subject, that
the split discipline the primary reference has is enforced for every exemplar
too, and that an exemplar with nothing honestly held out says so rather than
quietly pretending otherwise.
"""

import pytest

from app.eval import corpus, reference, scorecard
from app.measure.deck_metrics import DeckProfile


class TestTheSplitDisciplineHoldsForEveryExemplar:
    def test_no_exemplar_has_a_page_in_both_halves(self):
        corpus.assert_exemplar_splits_are_disjoint()

    def test_no_exemplar_names_a_page_it_does_not_have(self):
        for entry in corpus.EXEMPLARS:
            for page in (*entry.design_pages, *entry.validation_pages):
                assert 1 <= page <= entry.page_count, f"{entry.id} page {page}"

    def test_looking_at_a_held_out_page_raises(self):
        entry = next(e for e in corpus.EXEMPLARS if e.active_validation_pages())
        held_out = entry.active_validation_pages()[0]
        with pytest.raises(corpus.ValidationPeek):
            corpus.assert_exemplar_design_page(entry.id, held_out)

    def test_looking_at_a_design_page_is_allowed(self):
        entry = corpus.EXEMPLARS[0]
        corpus.assert_exemplar_design_page(entry.id, entry.design_pages[0])

    def test_an_exemplar_with_nothing_held_out_says_so(self):
        """
        The Work Trend Index was read end to end while the headline and
        attribution detectors were being fixed, so no page of it can honestly
        be held out. It stays in the library - it still describes a real
        publication worth comparing against - but it does not get to claim it
        is measuring generalisation.
        """
        wti = corpus.exemplar("wti-2025")
        assert wti.active_validation_pages() == ()
        assert "design-set only" in wti.note

    def test_an_unknown_exemplar_names_the_ones_that_exist(self):
        with pytest.raises(KeyError) as excinfo:
            corpus.exemplar("not-a-deck")
        assert "bxsl-investor" in str(excinfo.value)


class TestTheLibraryLoads:
    def test_every_declared_exemplar_has_a_committed_profile(self):
        """The PDFs are third-party and deliberately not in the repository, so
        the committed measurement is the whole of what the harness runs off.
        A declared exemplar with no fixture is a harness that silently scores
        against less than it says it does."""
        assert set(reference.available_exemplars()) == {e.id for e in corpus.EXEMPLARS}

    def test_the_library_includes_the_primary_reference(self):
        """So it is the whole of what the harness considers excellent, rather
        than "the reference, plus some others"."""
        library = reference.load_exemplar_library()
        assert "mckinsey-tech-trends" in library
        assert set(library) >= {e.id for e in corpus.EXEMPLARS}

    def test_an_exemplar_with_no_validation_set_falls_back_to_its_design_set(self):
        profile = reference.load_exemplar_profile("wti-2025", reference.VALIDATION_SPLIT)
        assert isinstance(profile, DeckProfile)
        assert profile.content_page_count > 0

    def test_the_exemplars_really_do_disagree(self):
        """
        The premise of the whole library. If the exemplars turned out to sit on
        top of each other, one profile would have been enough and this is all
        unnecessary machinery.
        """
        library = reference.load_exemplar_library()
        words = [p.words_mean for p in library.values() if p.words_mean]
        assert max(words) > 2 * min(words)


class TestScoringAgainstTheLibrary:
    def _library(self):
        return reference.load_exemplar_library()

    def test_a_deck_is_scored_against_every_exemplar(self):
        library = self._library()
        result = scorecard.score_library(library["bxsl-investor"], library)
        assert len(result.cards) == len(library)
        assert {card.reference for card in result.cards} == set(library)

    def test_an_exemplar_scores_best_against_itself(self):
        """The sanity check the ranking has to pass: a deck measured against
        its own profile is as close as anything can be."""
        library = self._library()
        result = scorecard.score_library(library["bxsl-investor"], library)
        assert result.best.reference == "bxsl-investor"

    def test_the_cards_are_ordered_nearest_first(self):
        library = self._library()
        result = scorecard.score_library(library["wti-2025"], library)
        scores = [card.style_score for card in result.cards if card.style_score is not None]
        assert scores == sorted(scores, reverse=True)

    def test_ranking_ignores_substance(self):
        """
        Substance is about whether there is anything in the deck, which is a
        property of the deck alone and not of which publication it resembles.
        Ranking on a blend would let a deck with more filler look closer to an
        exemplar it is nothing like.
        """
        library = self._library()
        result = scorecard.score_library(library["ey-iif-risk"], library)
        best = result.best
        assert best.reference == "ey-iif-risk"
        # Its substance is not the highest in the list, yet it still ranks first.
        assert best.style_score == max(
            card.style_score for card in result.cards if card.style_score is not None
        )

    def test_an_empty_library_reports_rather_than_crashes(self):
        result = scorecard.score_library(DeckProfile(name="x", page_count=0), {})
        assert result.best is None
        assert "No exemplar profiles" in result.to_markdown()

    def test_the_markdown_lists_every_exemplar_and_names_the_nearest(self):
        library = self._library()
        text = scorecard.score_library(library["bxsl-investor"], library).to_markdown()
        for name in library:
            assert name in text
        assert "Nearest exemplar" in text


class TestAimingAtOneRegister:
    """
    The nearest exemplar answers "what does this deck resemble". A run aimed at
    a register needs the other question - "how close did it get to the one it
    was aiming at" - and those are different cards precisely when the deck
    drifted, which is when the answer matters most. Iterating on the nearest
    card instead would let a deck aimed at an investor register score well by
    quietly becoming a magazine.
    """

    def _library(self):
        return reference.load_exemplar_library()

    def test_the_aimed_at_card_is_returned_even_when_it_is_not_nearest(self):
        library = self._library()
        result = scorecard.score_library(library["wti-2025"], library)
        assert result.best.reference == "wti-2025"
        aimed = result.card_for("bxsl-investor")
        assert aimed is not None
        assert aimed.reference == "bxsl-investor"
        assert aimed is not result.best

    def test_asking_for_an_exemplar_that_was_not_scored_gives_nothing(self):
        library = self._library()
        result = scorecard.score_library(library["ey-iif-risk"], library)
        assert result.card_for("not-an-exemplar") is None

    def test_the_markdown_says_so_when_the_deck_drifted(self):
        library = self._library()
        text = scorecard.score_library(
            library["wti-2025"], library
        ).to_markdown("bxsl-investor")
        assert "aimed at" in text
        assert "drift is the finding" in text

    def test_no_drift_note_when_the_deck_landed_on_its_target(self):
        library = self._library()
        text = scorecard.score_library(
            library["bxsl-investor"], library
        ).to_markdown("bxsl-investor")
        assert "drift is the finding" not in text
        assert "The register this run aimed at" in text


class TestTheCliRefusesAnUnknownTarget:
    """A render and a vision call are spent before the library is consulted,
    so an unknown --against has to fail on the way in, not on the way out."""

    def test_an_unknown_target_names_the_ones_that_exist(self):
        from app.eval.__main__ import _assert_known_exemplar

        with pytest.raises(SystemExit) as excinfo:
            _assert_known_exemplar("mckinsey-investor")
        assert "bxsl-investor" in str(excinfo.value)

    def test_no_target_is_allowed(self):
        from app.eval.__main__ import _assert_known_exemplar

        _assert_known_exemplar(None)
