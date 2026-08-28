"""
The design directions a run offers are the ones its agent designed.

The mechanism to author a design language existed and was tested; nothing
offered one. These cover the wiring: the tool authors languages and puts them
ahead of the enum, each one previews through its own pack rather than through
the template renderer, the contrast floor applies to an invented design
exactly as it does to a designed one, and the language survives the chat pause
so a resumed run builds the deck the user actually chose.
"""

import asyncio
import json

import pytest

from app import colors
from app.agent.tools import propose_design_directions as tool_module
from app.design import authored_system
from app.design.authored import Language, PageKind, Slot, to_pack
from app.design.schema import Canvas
from app.design.vocabulary import ensure
from app.directions import MIN_CONTRAST
from app.renderer import specimen

CANVAS = Canvas(w_in=13.333, h_in=7.5)


def _language(**overrides) -> Language:
    base = dict(
        name="Box office ledger",
        rationale="a franchise read as receipts",
        columns=6, body_pt=13.0,
        ratios={"headline": 2.8, "display": 4.2},
        display_face="Georgia", body_face="Arial",
        ground="#FFFFFF", ink="#1A1A1A", accent="#B3261E",
        pages=(
            PageKind(name="cover", role="cover", frequency=0.1, slots=(
                Slot(kind="heading", span=5, required=True),
                Slot(kind="standfirst", span=4))),
            PageKind(name="receipts", role="content", frequency=0.5, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="prose", span=3, name="left"),
                Slot(kind="figure", span=3))),
        ),
    )
    base.update(overrides)
    return Language(**base)


# ── The compiled design system ─────────────────────────────────────────


class TestALanguageCompilesToADesignSystem:
    """Everything downstream of the pause reads a design system, not a pack:
    the chart renderer, the block builders, the planner's density budget."""

    def test_it_carries_the_languages_own_colours(self):
        system = authored_system.system_for(_language(accent="#B3261E"))
        by_name = {c["name"]: c["value"]
                   for c in system["palette"]["colors"]}

        assert by_name["primary"] == "#B3261E"
        assert by_name["white"] == "#FFFFFF"

    def test_it_carries_the_languages_own_type(self):
        system = authored_system.system_for(
            _language(display_face="Georgia", body_face="Arial", body_pt=13.0))

        assert system["typography"]["titleFont"]["typeface"] == "Georgia"
        assert system["typography"]["bodyFont"]["typeface"] == "Arial"
        assert system["typography"]["bodyFont"]["sizePt"] == 13.0
        # display is 4.2x the body, as the language's own ratio says.
        assert system["typography"]["titleFont"]["sizePt"] == pytest.approx(54.6)

    def test_it_keeps_every_token_the_renderer_relies_on(self):
        """Built on the default bundle for the reason `compile_direction` is:
        a design system missing `dataViz` or `chrome` renders nothing."""
        system = authored_system.system_for(_language())

        for key in ("dataViz", "chrome", "spacing", "layouts", "density"):
            assert key in system, key

    def test_a_serif_display_is_reported_as_one(self):
        assert authored_system.system_for(
            _language(display_face="Georgia"))["typography"]["displayIsSerif"]
        assert not authored_system.system_for(
            _language(display_face="Helvetica Neue")
        )["typography"]["displayIsSerif"]


# ── The contrast floor ─────────────────────────────────────────────────


class TestAnInventedDesignIsHeldToTheSameFloor:
    """`directions.validate` holds a designed direction to WCAG AA. "The
    agent chose it" is not a reason a reader can see the text."""

    def test_a_readable_language_is_left_exactly_as_authored(self):
        language = _language(ground="#FFFFFF", ink="#1A1A1A")

        assert authored_system.readable(language) == language

    @pytest.mark.parametrize("ground,ink", [
        ("#101010", "#1A1A1A"),   # near-black ink on a near-black ground
        ("#FFFFFF", "#E8E8E8"),   # near-white ink on white
        ("#B3261E", "#8B1A15"),   # two shades of the same red
    ])
    def test_an_unreadable_one_is_repaired_until_it_reads(self, ground, ink):
        repaired = authored_system.readable(_language(ground=ground, ink=ink))

        assert colors.contrast_ratio(repaired.ink, ground) >= MIN_CONTRAST
        assert authored_system.contrast_problems(repaired) == []

    def test_the_accent_is_held_to_it_too(self):
        """An accent is set as type - an eyebrow, a bullet, a figure's
        label - so an accent nobody can read is the same failure."""
        repaired = authored_system.readable(
            _language(ground="#101010", accent="#141414"))

        assert colors.contrast_ratio(repaired.accent, "#101010") >= MIN_CONTRAST

    def test_the_repair_is_the_smallest_one_that_works(self):
        """Snapping to black would throw away the colour the agent chose."""
        repaired = authored_system.readable(
            _language(ground="#FFFFFF", ink="#9A9A9A"))

        assert repaired.ink not in ("#000000", "#FFFFFF")
        assert colors.contrast_ratio(repaired.ink, "#FFFFFF") >= MIN_CONTRAST

    def test_the_check_does_not_go_through_deck_theme(self):
        """
        The trap this check exists to avoid.

        `directions.contrast_problems` compiles a design system and asks
        `DeckTheme` what colour the text will be - and `DeckTheme` derives a
        readable ink from the ground through `colors.readable_on`, so it
        reports no failure it has not already fixed. That is fine for the
        template path, which goes through it. The design-language path does
        not: `render_doc` sets type in the pack's own ink on the pack's own
        ground, exactly as authored. Checking the compiled system would have
        passed an unreadable language every time.
        """
        from app.directions import contrast_problems as via_deck_theme

        unreadable = _language(ground="#101010", ink="#1A1A1A")

        assert via_deck_theme(authored_system.system_for(unreadable)) == []
        assert authored_system.contrast_problems(unreadable)


# ── The specimen ───────────────────────────────────────────────────────


class TestTheSpecimenIsComposedByThePack:
    def test_its_slides_are_the_shape_material_is_read_from(self):
        """`specimen_slides` writes `placeholderIdx` and typed blocks for the
        template renderer. A pack reads material - a role, a heading, copy -
        and saying where anything goes would be the model emitting geometry,
        which is what this path exists to stop."""
        from app.design import run as design_run

        slides = specimen.language_specimen_slides()

        assert [s["role"] for s in slides] == ["cover", "content", "content"]
        for slide in slides:
            assert "placeholderIdx" not in json.dumps(slide)
            shape = design_run.material_from(
                slide, index=slide["slideIndex"], total=len(slides))
            assert shape.heading
            assert shape.items

    def test_every_point_reaches_the_page(self):
        from app.design import run as design_run

        argument = specimen.language_specimen_slides()[1]
        shape = design_run.material_from(argument, index=1, total=3)
        body = " ".join(item.text for item in shape.items)

        for point in specimen.default_content().points:
            assert point.label in body

    def test_the_evidence_page_keeps_its_chart(self):
        from app.design import run as design_run

        shape = design_run.material_from(
            specimen.language_specimen_slides()[2], index=2, total=3)

        assert any(item.kind == "chart" for item in shape.items)

    def test_a_language_composes_all_three_without_splitting(self):
        """A three-page specimen that composes into five is a preview of a
        deck the user is not being offered."""
        from app.design import run as design_run

        pack = ensure(to_pack(_language(), CANVAS))
        doc = design_run.compose(specimen.language_specimen_slides(), pack)

        assert len(doc.pages) == 3

    def test_a_direction_carrying_a_language_previews_through_its_pack(self):
        direction = {"id": "d", "language": _language().model_dump(mode="json")}

        assert specimen._pack_for(direction) is not None

    def test_a_template_direction_still_previews_through_the_renderer(self):
        assert specimen._pack_for({"id": "d", "designSystem": {}}) is None


# ── The language survives the pause ────────────────────────────────────


class TestTheChosenLanguageSurvivesTheChatPause:
    """`_resolve_pack` reinstalls a pack *by id from disk*, and an authored
    language has no file. It rides through inside the chosen direction, which
    is already persisted."""

    def test_a_language_round_trips_through_json(self):
        language = _language()
        payload = json.loads(json.dumps(language.model_dump(mode="json")))

        assert Language(**payload) == language

    def test_the_loop_derives_the_pack_from_the_chosen_direction(self):
        from app.agent.loop import AgentLoop

        loop = AgentLoop(db=None)
        loop.working_state.chosen_direction = {
            "id": "box-office-ledger",
            "language": _language().model_dump(mode="json"),
        }

        pack = loop._authored_pack()

        assert pack is not None
        assert {a.id for a in pack.archetypes} >= {"cover", "receipts"}

    def test_a_direction_with_no_language_leaves_the_pack_alone(self):
        from app.agent.loop import AgentLoop

        loop = AgentLoop(db=None)
        loop.working_state.chosen_direction = {"id": "d", "designSystem": {}}

        assert loop._authored_pack() is None

    def test_a_language_that_will_not_derive_does_not_fail_the_run(self):
        from app.agent.loop import AgentLoop

        loop = AgentLoop(db=None)
        loop.working_state.chosen_direction = {"id": "d", "language": {"junk": 1}}

        assert loop._authored_pack() is None


# ── The tool ───────────────────────────────────────────────────────────


PAYLOAD = {
    "languages": [
        {
            "name": "Box office ledger",
            "rationale": "a franchise read as receipts",
            "columns": 6, "bodyPt": 13,
            "ratios": {"headline": 2.8, "display": 4.2},
            "displayFace": "Georgia", "bodyFace": "Arial",
            "ground": "#FFFFFF", "ink": "#1A1A1A", "accent": "#B3261E",
            "density": "balanced",
            "pages": [
                {"name": "cover", "role": "cover", "frequency": 0.1, "slots": [
                    {"kind": "heading", "span": 5, "required": True},
                    {"kind": "standfirst", "span": 4}]},
                {"name": "receipts", "role": "content", "frequency": 0.6,
                 "slots": [
                     {"kind": "heading", "span": 6, "required": True},
                     {"kind": "prose", "span": 3, "name": "left"},
                     {"kind": "figure", "span": 3}]},
            ],
        },
        {
            "name": "Marquee",
            "rationale": "the poster, not the ledger",
            "columns": 4, "bodyPt": 16,
            "ratios": {"headline": 2.0, "display": 3.0},
            "displayFace": "Helvetica Neue", "bodyFace": "Helvetica Neue",
            "ground": "#101010", "ink": "#F5F5F5", "accent": "#E8A33D",
            "density": "airy",
            "pages": [
                {"name": "billboard", "role": "cover", "frequency": 0.2,
                 "slots": [{"kind": "heading", "span": 4, "required": True}]},
                {"name": "still", "role": "content", "frequency": 0.6, "slots": [
                    {"kind": "heading", "span": 4, "required": True},
                    {"kind": "figure", "span": 4},
                    {"kind": "prose", "span": 4, "name": "caption"}]},
            ],
        },
    ]
}


class _LLM:
    """Answers the authoring call with a fixed, usable set."""

    def __init__(self, payload=None):
        self.payload = PAYLOAD if payload is None else payload
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return {"message": {"content": json.dumps(self.payload)}}


def _authored(monkeypatch, llm=None):
    llm = llm or _LLM()
    monkeypatch.setattr(tool_module, "require_chat_provider", lambda: llm)
    return asyncio.run(tool_module.ProposeDesignDirectionsTool._author_languages(
        "a deck about the Cars franchise", "", None)), llm


class TestTheToolOffersWhatTheAgentDesigned:
    def test_each_language_becomes_a_direction(self, monkeypatch):
        (out, why), _llm = _authored(monkeypatch)

        assert [d["name"] for d in out] == ["Box office ledger", "Marquee"]
        assert all(d["rationale"] for d in out)
        # Two where three were asked for, and the shortfall is stated rather
        # than swallowed - the caller tops the set up from the enum.
        assert "2 of 3" in why

    def test_each_carries_the_design_system_the_deck_renders_against(
            self, monkeypatch):
        (out, _why), _llm = _authored(monkeypatch)

        for direction in out:
            assert direction["designSystem"]["typography"]["bodyFont"]["typeface"]
            assert direction["designSystem"]["dataViz"]

    def test_each_carries_its_language_so_the_pack_can_be_rebuilt(
            self, monkeypatch):
        (out, _why), _llm = _authored(monkeypatch)

        for direction in out:
            language = Language(**direction["language"])
            assert ensure(to_pack(language, CANVAS)).archetypes

    def test_a_settled_colour_wins_over_the_one_the_language_proposed(
            self, monkeypatch):
        llm = _LLM()
        monkeypatch.setattr(tool_module, "require_chat_provider", lambda: llm)

        out, _why = asyncio.run(
            tool_module.ProposeDesignDirectionsTool._author_languages(
                "a deck", "", "#0F7B6C"))

        # Applied to the language, not only to the compiled system, so the
        # pack composes in it too. Not necessarily the hex verbatim: the
        # settled colour still has to clear the contrast floor, and on
        # Marquee's near-black ground this one does not, so it is lifted -
        # what matters is that neither language kept the accent it proposed.
        accents = [Language(**d["language"]).accent for d in out]

        assert accents[0] == "#0F7B6C"
        assert "#B3261E" not in accents and "#E8A33D" not in accents
        for accent, direction in zip(accents, out):
            ground = Language(**direction["language"]).ground
            assert colors.contrast_ratio(accent, ground) >= MIN_CONTRAST

    def test_a_model_that_cannot_be_reached_does_not_fail_the_pause(
            self, monkeypatch):
        class Broken:
            async def chat(self, messages, tools=None):
                raise RuntimeError("provider down")

        monkeypatch.setattr(tool_module, "require_chat_provider", lambda: Broken())
        out, why = asyncio.run(
            tool_module.ProposeDesignDirectionsTool._author_languages(
                "a deck", "", None))

        assert out == []
        assert why

    def test_a_language_with_nowhere_to_put_a_chart_is_rejected(
            self, monkeypatch):
        """
        A deck argues from charts and tables, and they go in figure slots.

        A language with none composed its cover and its prose pages fine and
        then failed at the evidence page with "no archetype can carry a page
        of 103 characters in 3 items" - a message about paragraph counts,
        for a problem that was nothing to do with paragraphs. Caught at
        authoring, the model is told what is missing and writes the page.
        """
        no_figure = {"languages": [
            {**PAYLOAD["languages"][0],
             "pages": [
                 page for page in PAYLOAD["languages"][0]["pages"]
                 if page["role"] == "cover"
             ] + [{"name": "prose-only", "role": "content", "frequency": 0.6,
                   "slots": [{"kind": "heading", "span": 6, "required": True},
                             {"kind": "prose", "span": 6}]}]},
        ]}
        (out, why), _llm = _authored(monkeypatch, _LLM(no_figure))

        assert out == []
        assert "figure slot" in why

    def test_a_set_that_is_three_of_one_idea_is_reported_not_offered(
            self, monkeypatch):
        """`differs_enough` is the floor: same measure and same page
        vocabulary is one design in three palettes, which is the failure the
        enum had."""
        same = {"languages": [
            dict(PAYLOAD["languages"][0], name="One"),
            dict(PAYLOAD["languages"][0], name="Two"),
        ]}
        (out, why), llm = _authored(monkeypatch, _LLM(same))

        # Retried once with the reason stated back, then whatever survived
        # is offered rather than the user being shown nothing.
        assert llm.calls == 2
        assert "differ" in why or "same" in why
        assert out
