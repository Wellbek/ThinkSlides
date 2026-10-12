"""
One run's design languages, authored and checked.

The retry is the part worth pinning. A model told only "try again" tries the
same thing again; told "every language uses six columns" it changes the
measure - and the difference between those two prompts is the difference
between three arrangements of one idea and three designs.
"""
import asyncio
import json

import pytest

from app.design import author_run
from app.design.schema import Canvas

CANVAS = Canvas(w_in=13.333, h_in=7.5)

# Every one of these declares a content page with a `figure` slot, because a
# language without one is refused: a deck argues from charts and tables, and a
# vocabulary with nowhere to put them cannot hold the deck's own evidence.
# They differ in measure and in page vocabulary, which is the floor
# `differs_enough` states.
LEDGER = {
    "name": "Box office ledger", "rationale": "receipts read as a ledger",
    "columns": 6, "bodyPt": 13, "ratios": {"display": 4.2, "headline": 2.8},
    "displayFace": "Georgia", "bodyFace": "Arial", "accent": "#B3261E",
    "pages": [{"name": "Receipts", "role": "content", "frequency": 0.6,
               "slots": [{"kind": "heading", "span": 6, "required": True},
                         {"kind": "statistic", "span": 6},
                         {"kind": "figure", "span": 3},
                         {"kind": "prose", "span": 3}]}],
}
WIDE = {
    "name": "Wide screen", "rationale": "a franchise shown", "columns": 12,
    "bodyPt": 11, "ratios": {"headline": 1.7}, "displayFace": "Arial",
    "bodyFace": "Arial", "accent": "#0B6E4F",
    "pages": [{"name": "Spread", "role": "content", "frequency": 0.7,
               "slots": [{"kind": "heading", "span": 12, "required": True},
                         {"kind": "prose", "span": 4},
                         {"kind": "figure", "span": 4},
                         {"kind": "prose", "span": 4}]}],
}
STUB = {
    "name": "Ticket stub", "rationale": "a small dense stub", "columns": 4,
    "bodyPt": 10, "ratios": {"headline": 2.0},
    "displayFace": "Times New Roman", "bodyFace": "Verdana",
    "accent": "#3B3B98", "density": "dense",
    "pages": [{"name": "Stub", "role": "content", "frequency": 0.5,
               "slots": [{"kind": "heading", "span": 4, "required": True},
                         {"kind": "quote", "span": 4},
                         {"kind": "figure", "span": 4},
                         {"kind": "prose", "span": 4}]}],
}
GOOD = {"languages": [LEDGER, WIDE, STUB]}
SAME = {"languages": [dict(LEDGER, name=f"Twin {i}") for i in range(3)]}


class _LLM:
    """Answers with each payload in turn, then repeats the last."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        self.calls += 1
        self.prompts.append(messages[0]["content"])
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return {"message": {"content": json.dumps(payload)}}


def _author(llm, brief="Cars movies finances"):
    return asyncio.run(author_run.author(llm, brief, CANVAS))


class TestAGoodSetIsTakenAsItStands:
    def test_every_language_comes_back(self):
        languages, why = _author(_LLM(GOOD))
        assert [language.name for language in languages] == [
            "Box office ledger", "Wide screen", "Ticket stub"]
        assert why == ""

    def test_it_asks_only_once(self):
        llm = _LLM(GOOD)
        _author(llm)
        assert llm.calls == 1

    def test_the_deck_reaches_the_prompt(self):
        llm = _LLM(GOOD)
        _author(llm, "Cars movies finances")
        assert "Cars movies finances" in llm.prompts[0]


class TestALanguageMustBeAbleToHoldAChart:
    """
    A deck argues from charts and tables, and they go in figure slots.

    A language without one composed its cover and its prose pages perfectly
    well and then failed at the evidence page with "no archetype in pack
    'marquee' can carry a page of 103 characters in 3 items" - a message
    about paragraph counts, for a problem that had nothing to do with
    paragraphs. Caught at authoring, the model is told what is missing.
    """

    @staticmethod
    def _no_figure(language: dict) -> dict:
        return {**language, "pages": [
            {**page, "slots": [slot for slot in page["slots"]
                               if slot["kind"] != "figure"]}
            for page in language["pages"]
        ]}

    def test_one_with_nowhere_to_put_a_chart_is_dropped(self):
        payload = {"languages": [self._no_figure(LEDGER)]}
        languages, why = asyncio.run(
            author_run.author(_LLM(payload), "a deck", CANVAS, wanted=1))

        assert languages == []
        assert "figure slot" in why

    def test_the_retry_names_what_was_missing(self):
        """"Try again" gets the same set back. "None of your content pages
        has a figure slot" gets a page for the deck's charts to go on."""
        llm = _LLM({"languages": [self._no_figure(LEDGER)]})
        asyncio.run(author_run.author(llm, "a deck", CANVAS, wanted=1))

        assert llm.calls == 2
        assert "figure slot" in llm.prompts[-1]

    def test_a_figure_slot_on_a_cover_does_not_count(self):
        """A cover carrying a photograph is not a page a chart can go on."""
        cover_only = {**LEDGER, "pages": [
            {"name": "Cover", "role": "cover", "frequency": 1.0, "slots": [
                {"kind": "heading", "span": 6, "required": True},
                {"kind": "figure", "span": 6}]},
        ]}
        languages, why = asyncio.run(
            author_run.author(_LLM({"languages": [cover_only]}), "a deck",
                              CANVAS, wanted=1))

        assert languages == []
        assert "figure slot" in why


class TestASamenessRetryCarriesTheReason:
    def test_a_set_that_differs_only_in_hue_is_asked_again(self):
        llm = _LLM(SAME, GOOD)
        languages, why = _author(llm)
        assert llm.calls == 2
        assert [language.name for language in languages] == [
            "Box office ledger", "Wide screen", "Ticket stub"]
        assert why == ""

    def test_the_second_ask_says_what_was_wrong(self):
        """Told only "try again" a model tries the same thing again."""
        llm = _LLM(SAME, GOOD)
        _author(llm)
        assert "was rejected" in llm.prompts[1]
        assert "columns" in llm.prompts[1]

    def test_a_set_that_never_improves_is_returned_anyway(self):
        """A user offered three similar designs has still been offered a
        choice; a user offered an error has not."""
        llm = _LLM(SAME)
        languages, why = _author(llm)
        assert len(languages) == 3
        assert why


class TestNothingUnusableIsOffered:
    def test_a_language_that_cannot_derive_is_dropped(self):
        broken = {"languages": [LEDGER, {"name": "Empty", "pages": []}]}
        languages, _ = _author(_LLM(broken))
        assert [language.name for language in languages] == ["Box office ledger"]

    def test_a_provider_failure_is_reported_not_raised(self):
        class Boom:
            async def chat(self, messages, tools=None):
                raise RuntimeError("provider down")

        languages, why = asyncio.run(
            author_run.author(Boom(), "x", CANVAS))
        assert languages == []
        assert "could not be reached" in why

    def test_nonsense_back_from_the_model_is_survivable(self):
        class Junk:
            async def chat(self, messages, tools=None):
                return {"message": {"content": "not json at all"}}

        languages, why = asyncio.run(author_run.author(Junk(), "x", CANVAS))
        assert languages == []
        assert why


class TestWhatTheUserIsOffered:
    def test_each_language_is_described_in_terms_a_user_can_act_on(self):
        languages, _ = _author(_LLM(GOOD))
        described = author_run.describe(languages)
        assert [d["name"] for d in described] == [
            "Box office ledger", "Wide screen", "Ticket stub"]
        for entry in described:
            assert entry["rationale"] and entry["summary"] and entry["pages"]

    def test_the_summary_says_how_the_designs_differ(self):
        languages, _ = _author(_LLM(GOOD))
        summaries = [author_run.summary(language) for language in languages]
        assert "6 columns" in summaries[0] and "12 columns" in summaries[1]
        assert "loud" in summaries[0] and "quiet" in summaries[1]
        assert "Georgia" in summaries[0]

    def test_a_choice_resolves_back_to_its_language(self):
        languages, _ = _author(_LLM(GOOD))
        assert author_run.find(languages, "wide-screen").columns == 12
        assert author_run.find(languages, "Ticket stub").columns == 4
        assert author_run.find(languages, "nothing") is None

    def test_each_becomes_a_pack_a_deck_could_be_composed_against(self):
        languages, _ = _author(_LLM(GOOD))
        packs = author_run.packs_for(languages, CANVAS)
        assert len(packs) == 3
        assert len({pack.grid.columns for pack in packs}) == 3
        for pack in packs:
            assert pack.archetypes and pack.type_roles

    @pytest.mark.parametrize("index,expected", [(0, 6), (1, 12), (2, 4)])
    def test_the_measure_survives_into_the_pack(self, index, expected):
        languages, _ = _author(_LLM(GOOD))
        packs = author_run.packs_for(languages, CANVAS)
        assert packs[index].grid.columns == expected


# A ledger whose only figure slot sits under a heading, a standfirst, two
# stat rows and a caption: it derives, it has a figure slot, and it passed the
# old gate - and every table set in it was a sliver (#351). Both recorded runs
# authored languages like this one.
CRAMPED = {
    "name": "Cramped ledger", "rationale": "everything stacked", "columns": 6,
    "bodyPt": 14, "ratios": {"headline": 2.4}, "displayFace": "Georgia",
    "bodyFace": "Arial", "accent": "#7A1F1F",
    "pages": [{"name": "Stack", "role": "content", "frequency": 1.0,
               "slots": [{"kind": "heading", "span": 6, "required": True},
                         {"kind": "standfirst", "span": 6},
                         {"kind": "statistic", "span": 6},
                         {"kind": "statistic", "span": 6},
                         {"kind": "figure", "span": 6},
                         {"kind": "prose", "span": 6},
                         {"kind": "caption", "span": 6}]}],
}


class TestALanguageIsCompiledBeforeItIsOffered:
    """#322. The gate was the derivation alone, so a language could pass it
    and still ship nothing (#306, a cover that would not compile) or ship
    every table as a sliver (#351)."""

    def test_the_shipped_languages_compile(self):
        from app.design import authoring

        for payload in (LEDGER, WIDE, STUB):
            (language,) = authoring.read({"languages": [payload]})
            assert author_run._problem(language, CANVAS, 12.0) == "", payload["name"]

    def test_one_with_no_room_for_a_table_is_refused_with_the_numbers(self):
        from app.design import authoring

        (language,) = authoring.read({"languages": [CRAMPED]})
        why = author_run._problem(language, CANVAS, 12.0)
        assert why.startswith("no content page gives a table room to be read")
        assert "tallest figure slot is" in why and "needs about" in why

    def test_a_failing_language_is_not_let_through_by_a_twin_of_its_name(self):
        twin = {**CRAMPED, "name": LEDGER["name"]}
        llm = _LLM({"languages": [LEDGER, twin]})
        languages, _ = asyncio.run(
            author_run.author(llm, "a deck", CANVAS, wanted=1))
        assert len(languages) == 1 and languages[0].pages[0].name == "receipts"

    def test_the_retry_carries_the_reason(self):
        llm = _LLM({"languages": [CRAMPED]})
        languages, _ = asyncio.run(
            author_run.author(llm, "a deck", CANVAS, wanted=1))
        assert languages == []
        assert llm.calls == 2
        assert "Cramped ledger: no content page gives a table room" in llm.prompts[-1]
