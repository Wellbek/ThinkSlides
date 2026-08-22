"""
The eye outranks the numbers.

The scorecard measures properties, which is what an iteration loop needs and
is not what "publication grade" means: a deck can match every number and be
obviously amateur, and the exemplars themselves disagree by 3x on the numbers.
So a vision model looks at the rendered pages next to exemplar pages, and its
verdict is reconciled with the score.

The case these tests exist for is the third one below: a deck the judge is
confident about, scoring badly. That combination is evidence about the
*metric*, not the deck. Optimising against a number that disagrees with a
clear-eyed look at the artefact makes the output worse while the dashboard
improves - which is the failure the whole harness is supposed to prevent.
"""

import json

import pytest

from app.eval import visual_judge as judge


def _verdict(grade: str, confident: bool) -> judge.Verdict:
    return judge.Verdict(grade=grade, confident=confident, summary="s")


class TestReadingTheJudgesReply:
    def test_a_well_formed_reply_is_read_whole(self):
        verdict = judge._parse(json.dumps({
            "grade": "publication",
            "confident": True,
            "summary": "Reads like a published report.",
            "strengths": ["Four distinct type levels", " "],
            "faults": [],
            "pages": [{"page": 2, "grade": "competent", "note": "chart unlabelled"}],
        }))
        assert verdict.grade == "publication"
        assert verdict.confident is True
        assert verdict.strengths == ["Four distinct type levels"]
        assert verdict.pages[0].page == 2
        assert verdict.pages[0].grade == "competent"

    def test_an_unrecognised_grade_is_not_read_as_an_endorsement(self):
        """A malformed reply must never be able to pass a deck. Falling back
        to 'competent' means the numbers decide, which is the safe default."""
        verdict = judge._parse('{"grade": "amazing", "confident": true}')
        assert verdict.grade == "competent"
        assert verdict.is_publication_grade is False

    def test_a_reply_that_is_not_json_is_an_error_not_a_pass(self):
        verdict = judge._parse("I had a look and it seems fine to me")
        assert verdict.error
        assert verdict.is_publication_grade is False

    def test_a_page_entry_with_no_usable_number_is_dropped(self):
        verdict = judge._parse(json.dumps({
            "grade": "competent", "confident": False,
            "pages": [{"page": "two"}, {"page": 3, "grade": "amateur"}],
        }))
        assert [p.page for p in verdict.pages] == [3]

    def test_prose_around_the_json_is_tolerated(self):
        verdict = judge._parse(
            'Here is my assessment:\n{"grade": "amateur", "confident": true}\nHope that helps.'
        )
        assert verdict.grade == "amateur"
        assert verdict.confident is True


class TestAdjudicatingBetweenTheEyeAndTheNumbers:
    def test_a_confident_pass_overrules_a_low_score(self):
        call = judge.adjudicate(_verdict("publication", True), 0.41)
        assert call.accepted is True
        assert call.metric_is_suspect is True
        assert "against the metric" in call.reason

    def test_a_confident_pass_agreeing_with_a_good_score_flags_nothing(self):
        call = judge.adjudicate(_verdict("publication", True), 0.82)
        assert call.accepted is True
        assert call.metric_is_suspect is False

    def test_an_unconfident_pass_does_not_overrule_the_score(self):
        """The judge only gets to overrule when it would stand behind the
        grade. Otherwise the numbers decide, which is right for the case where
        the eye has nothing decisive to say."""
        call = judge.adjudicate(_verdict("publication", False), 0.41)
        assert call.accepted is False
        assert call.metric_is_suspect is False

    def test_the_eye_can_fail_a_deck_the_numbers_passed(self):
        call = judge.adjudicate(_verdict("amateur", True), 0.78)
        assert call.accepted is False
        assert call.metric_is_suspect is True
        assert "defects the score missed" in call.reason

    def test_both_agreeing_it_is_bad_does_not_blame_the_metric(self):
        call = judge.adjudicate(_verdict("amateur", True), 0.30)
        assert call.accepted is False
        assert call.metric_is_suspect is False

    def test_competent_falls_through_to_the_score(self):
        assert judge.adjudicate(_verdict("competent", True), 0.80).accepted is True
        assert judge.adjudicate(_verdict("competent", True), 0.30).accepted is False

    def test_a_judge_that_could_not_run_falls_back_to_the_score(self):
        """The vision call is the part most likely to be unavailable - no
        credit, no network, no key. That must degrade to the old behaviour,
        loudly, rather than failing every deck."""
        broken = judge.Verdict(error="Error code: 429")
        passing = judge.adjudicate(broken, 0.80)
        failing = judge.adjudicate(broken, 0.30)
        assert passing.accepted is True
        assert failing.accepted is False
        assert "could not look at the deck" in passing.reason
        assert passing.metric_is_suspect is False

    def test_a_deck_with_no_score_at_all_is_not_silently_passed(self):
        call = judge.adjudicate(judge.Verdict(error="no key"), None)
        assert call.accepted is False


class TestTheJudgeNeverLeaksImages:
    @pytest.mark.asyncio
    async def test_the_rendered_pages_do_not_outlive_the_call(self, tmp_path, monkeypatch):
        """
        Feeding a rendered page back into the run's own conversation is what
        killed a run with a 767k-token request (#237). The judge reads its
        images, sends them on one bounded call, and drops them.
        """
        import fitz

        pdf_path = tmp_path / "deck.pdf"
        doc = fitz.open()
        for _ in range(3):
            doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        seen: dict = {}

        class _Stub:
            async def chat_with_images(self, prompt, images, **kwargs):
                seen["count"] = len(images)
                seen["prefix"] = images[0][:23] if images else ""
                return '{"grade": "competent", "confident": false}'

        monkeypatch.setattr(
            "app.llm.provider.get_vision_llm_provider", lambda: _Stub()
        )
        # No exemplar PDFs on this machine - the judge still works, it just has
        # nothing to compare against.
        monkeypatch.setattr("app.eval.corpus.find_exemplar_pdf", lambda _id: None)

        verdict = await judge.judge_pdf(str(pdf_path))

        assert verdict.grade == "competent"
        assert seen["count"] == 3
        assert seen["prefix"].startswith("data:image/jpeg;base64,")
        # Nothing written beside the deck, and the work directory is gone.
        assert list(tmp_path.iterdir()) == [pdf_path]

    @pytest.mark.asyncio
    async def test_only_the_first_pages_are_looked_at(self, tmp_path, monkeypatch):
        """A verdict on a whole deck does not get better past the first
        handful of pages, and every extra image is cost on a call that runs
        many times over an iteration."""
        import fitz

        pdf_path = tmp_path / "long.pdf"
        doc = fitz.open()
        for _ in range(judge.MAX_DECK_PAGES + 5):
            doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        seen: dict = {}

        class _Stub:
            async def chat_with_images(self, prompt, images, **kwargs):
                seen["count"] = len(images)
                return '{"grade": "competent", "confident": false}'

        monkeypatch.setattr("app.llm.provider.get_vision_llm_provider", lambda: _Stub())
        monkeypatch.setattr("app.eval.corpus.find_exemplar_pdf", lambda _id: None)

        await judge.judge_pdf(str(pdf_path))
        assert seen["count"] == judge.MAX_DECK_PAGES

    @pytest.mark.asyncio
    async def test_a_provider_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        import fitz

        pdf_path = tmp_path / "deck.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        class _Broken:
            async def chat_with_images(self, prompt, images, **kwargs):
                raise RuntimeError("Error code: 429, insufficient balance")

        monkeypatch.setattr("app.llm.provider.get_vision_llm_provider", lambda: _Broken())
        monkeypatch.setattr("app.eval.corpus.find_exemplar_pdf", lambda _id: None)

        verdict = await judge.judge_pdf(str(pdf_path))
        assert "429" in verdict.error
        assert verdict.is_publication_grade is False
