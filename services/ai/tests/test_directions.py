"""
Three genuinely different design systems for one brief, and the run that
builds in the one the user picked.

The agent asked about visual style in words, and the user picked by reading
"professional", "bold", "monochrome" - five hardcoded palettes that rendered
the identical layouts, the identical type scale and the identical accent rule.
A user who picked "bold" and a user who picked "nature" got the same deck in
different colours.

Because the model now authors all three outright, nothing else enforces a
floor - so most of what is tested here is the floor: a set differing only in
hue is rejected, a direction whose text cannot be read on its own ground never
reaches the user, and an answer the user already gave constrains all three.

No model is called. The authoring prompt is exercised against a stub, which is
what lets the rejection paths be tested at all - a real model rarely produces
the bad set on demand.
"""

import json

import pytest

from app import directions
from app.design_system import get_default_design_system


def _spec(**kwargs) -> directions.DirectionSpec:
    base = {"id": "d", "name": "D", "rationale": "why", "ground": "light",
            "display_face": "Arial", "body_face": "Arial", "density": "balanced",
            "motif": "rule"}
    base.update(kwargs)
    return directions.DirectionSpec(**base)


# ──────────────────────────────────────────────────────────────────────
# A direction is a design system, not a palette
# ──────────────────────────────────────────────────────────────────────

class TestCompilingADirection:
    def test_it_produces_a_system_the_renderer_can_build_from(self):
        system = directions.compile_direction(_spec())

        for key in ("palette", "typography", "layouts", "slideDimensions",
                    "spacing", "chrome", "dataViz", "density"):
            assert system.get(key), key

    @pytest.mark.parametrize("ground,dark_content", [
        ("light", False), ("rail", False), ("dark", True), ("split", False),
    ])
    def test_the_ground_decides_what_a_content_slide_sits_on(self, ground, dark_content):
        from app import colors
        from app.renderer import deck_theme

        system = directions.compile_direction(_spec(ground=ground))
        style = deck_theme.DeckTheme.from_design_system(system).style_for("content")

        assert colors.is_dark(style.background) is dark_content

    def test_the_type_pairing_reaches_the_renderer(self):
        from app.renderer.typography import TypeScale

        system = directions.compile_direction(
            _spec(display_face="Georgia", body_face="Arial")
        )
        scale = TypeScale.from_design_system(system)

        assert scale.display_is_serif is True
        assert scale.title_font != scale.body_font

    def test_the_density_posture_becomes_the_planners_target(self):
        """The axis that is not only visual: it sets what the planner is asked
        to put on a slide, which #129 turns into a budget."""
        from app import budget

        dense = directions.compile_direction(_spec(density="dense"))
        airy = directions.compile_direction(_spec(density="airy"))

        assert budget.budget_for(dense).blocks_target > \
            budget.budget_for(airy).blocks_target * 2

    def test_the_motif_reaches_the_chrome(self):
        assert directions.compile_direction(_spec(motif="none"))["chrome"][
            "footerRule"]["show"] == "never"

    def test_a_spec_is_read_from_whatever_the_model_sent(self):
        spec = directions.DirectionSpec.from_dict({
            "name": "Dark Analyst", "ground": "DARK", "density": "dense",
            "displayFace": "Georgia", "bodySizePt": "15",
        })

        assert spec.id == "dark-analyst"
        assert spec.ground == "dark"
        assert spec.body_pt == 15.0
        assert spec.display_is_serif is True

    def test_nonsense_from_the_model_falls_back_rather_than_raising(self):
        spec = directions.DirectionSpec.from_dict({
            "ground": "purple", "density": "extremely", "motif": "swoosh",
            "displaySizePt": "huge",
        })

        assert (spec.ground, spec.density, spec.motif) == ("light", "balanced", "rule")
        assert spec.display_pt == 36.0


class TestTheColourIsAlreadySettled:
    """The point that makes this useful rather than decorative: by the time
    directions are proposed the consultation has settled things, and a coral
    direction here is the tool ignoring an answer it already has."""

    def test_a_run_that_answered_green_gets_three_green_directions(self):
        proposed = directions.default_directions("#00704A")

        assert len(proposed) == 3
        for direction in proposed:
            palette = {c["name"]: c["value"] for c in
                       direction["designSystem"]["palette"]["colors"]}
            assert palette["primary"] == "#00704A"

    def test_they_still_differ_on_everything_that_is_open(self):
        proposed = directions.default_directions("#00704A")

        grounds = {d["designSystem"]["ground"] for d in proposed}
        faces = {d["designSystem"]["typography"]["titleFont"]["typeface"]
                 for d in proposed}

        assert len(grounds) == 3
        assert len(faces) > 1

    def test_a_dark_direction_still_has_a_light_dark_rhythm(self):
        """A deck of identically dark pages is the defect #56 opened on with
        the colours the other way round."""
        from app.renderer import deck_theme

        dark = [d for d in directions.default_directions("#00704A")
                if d["id"] == "dark-analyst"][0]
        theme = deck_theme.DeckTheme.from_design_system(dark["designSystem"])

        assert theme.style_for("cover").background != \
            theme.style_for("content").background

    def test_every_direction_keeps_its_cover_distinct_from_its_content(self):
        from app.renderer import deck_theme

        for direction in directions.default_directions():
            theme = deck_theme.DeckTheme.from_design_system(direction["designSystem"])
            assert theme.style_for("cover").background != \
                theme.style_for("content").background, direction["id"]

    def test_no_brand_colour_falls_back_to_the_house_palette(self):
        palette = {c["name"]: c["value"] for c in
                   directions.default_directions()[0]
                   ["designSystem"]["palette"]["colors"]}

        assert palette["primary"]


# ──────────────────────────────────────────────────────────────────────
# The floor
# ──────────────────────────────────────────────────────────────────────

class TestASetThatIsNotASet:
    def test_three_directions_differing_only_in_hue_are_rejected(self):
        """Which is exactly what the five colour themes this replaces were."""
        same = [_spec(id=f"d{n}", name=f"D{n}") for n in range(3)]

        problems = directions.distinctness_problems(same)

        assert any("the same direction" in problem for problem in problems)
        # And they share a ground, so they render the same cover too.
        assert any("the same cover" in problem for problem in problems)

    def test_differing_on_one_axis_is_not_enough(self):
        pair = [_spec(id="a", name="A", ground="light"),
                _spec(id="b", name="B", ground="dark")]

        assert directions.distinctness_problems(pair)

    def test_differing_on_two_axes_is(self):
        pair = [_spec(id="a", name="A", ground="light", density="airy"),
                _spec(id="b", name="B", ground="dark", density="dense")]

        assert directions.distinctness_problems(pair) == []

    def test_the_default_set_passes_its_own_check(self):
        assert directions.distinctness_problems(directions.default_specs()) == []

    def test_two_directions_sharing_an_id_are_reported(self):
        pair = [_spec(id="same", name="A", ground="light", density="airy"),
                _spec(id="same", name="B", ground="dark", density="dense")]

        assert any("share an id" in p for p in directions.distinctness_problems(pair))

    def test_one_direction_alone_is_not_a_set_to_check(self):
        assert directions.distinctness_problems([_spec()]) == []


class TestADirectionNobodyCanRead:
    def test_every_default_direction_clears_wcag_aa(self):
        for direction in directions.default_directions():
            assert directions.contrast_problems(direction["designSystem"]) == []

    def test_a_direction_whose_text_vanishes_into_its_ground_is_rejected(self):
        system = directions.compile_direction(_spec())
        # A palette whose paper and its ink are the same colour. The per-role
        # grounds are compiled from that palette, so they move with it.
        for entry in system["palette"]["colors"]:
            if entry["name"] in ("white", "light", "dark", "primary"):
                entry["value"] = "#7A7A7A"
        system["grounds"] = {role: "#7A7A7A" for role in system["grounds"]}

        problems = directions.contrast_problems(system)

        assert problems
        assert "WCAG AA" in problems[0]

    def test_an_unreadable_direction_never_reaches_the_user(self):
        broken = _spec(id="broken", name="Broken", ground="light")

        compiled, problems = directions.validate([broken])

        assert len(compiled) <= 1
        assert any("survived" in p for p in problems)

    def test_a_palette_that_will_not_parse_falls_back_rather_than_crashing(self):
        """``DeckTheme`` fills every unreadable slot from its own defaults,
        so what comes back is a readable direction rather than an exception."""
        assert directions.contrast_problems({"palette": {"colors": "nonsense"}}) == []


class TestTheFallbackSet:
    def test_it_is_three_directions(self):
        assert len(directions.default_directions()) == \
            directions.DIRECTION_COUNT

    def test_each_carries_a_name_a_reason_and_a_system(self):
        for direction in directions.default_directions():
            assert direction["name"] and not direction["name"].startswith("Option")
            assert direction["rationale"]
            assert direction["designSystem"]["typography"]

    def test_none_of_them_is_lettered(self):
        for direction in directions.default_directions():
            assert direction["id"] not in ("a", "b", "c")


class TestAnUploadedTemplate:
    def test_it_is_offered_back_unchanged_as_a_direction(self):
        uploaded = get_default_design_system("warm")
        uploaded["sourceType"] = "pptx"

        direction = directions.template_direction(uploaded, "Acme deck")

        assert direction["designSystem"] is uploaded
        assert direction["name"] == "Acme deck"


# ──────────────────────────────────────────────────────────────────────
# The tool
# ──────────────────────────────────────────────────────────────────────

class _StubLLM:
    """A model that returns whatever set the test wants to see rejected."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        self.prompts.append(messages[0]["content"])
        payload = self.payloads.pop(0) if self.payloads else self.payloads[-1]
        return {"message": {"content": json.dumps(payload)}}


def _payload(*specs):
    return {"directions": list(specs)}


GOOD = _payload(
    {"id": "clean", "name": "Clean minimal", "rationale": "Airy.",
     "ground": "light", "displayFace": "Arial", "bodyFace": "Arial",
     "density": "airy", "motif": "rule"},
    {"id": "dark", "name": "Dark analyst", "rationale": "Dense.",
     "ground": "dark", "displayFace": "Arial", "bodyFace": "Arial",
     "density": "dense", "motif": "band"},
    {"id": "editorial", "name": "Editorial serif", "rationale": "Written.",
     "ground": "split", "displayFace": "Georgia", "bodyFace": "Arial",
     "density": "balanced", "motif": "none"},
)
HUE_ONLY = _payload(
    *[{"id": f"d{n}", "name": f"D{n}", "rationale": "r", "ground": "light",
       "displayFace": "Arial", "bodyFace": "Arial", "density": "balanced",
       "motif": "rule"} for n in range(3)]
)


@pytest.fixture
def no_previews(monkeypatch):
    """The tool's own logic, without paying for LibreOffice three times."""
    from app.renderer import specimen

    async def fake(directions_, session_id, output_dir=None):
        return {d["id"]: [f"/previews/{session_id}/direction/{d['id']}/1.png"]
                for d in directions_}

    monkeypatch.setattr(specimen, "render_directions", fake)
    import app.agent.tools.propose_design_directions as tool

    monkeypatch.setattr(tool.specimen, "render_directions", fake)


def _install(monkeypatch, llm):
    import app.agent.tools.propose_design_directions as tool

    monkeypatch.setattr(tool, "require_chat_provider", lambda: llm)
    return tool.ProposeDesignDirectionsTool


class TestProposingDirections:
    @pytest.mark.asyncio
    async def test_it_returns_three_directions_with_previews(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck on cloud costs", session_id="s1")

        assert result.success
        assert [d["id"] for d in result.data["directions"]] == \
            ["clean", "dark", "editorial"]
        assert all(d["previewUrls"] for d in result.data["directions"])

    @pytest.mark.asyncio
    async def test_the_options_are_previews_the_card_can_render(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", session_id="s1")

        options = result.data["questions"][0]["options"]
        assert all(o["kind"] == "preview" for o in options)
        assert all(o["id"] and o["label"] and o["previewUrls"] for o in options)

    @pytest.mark.asyncio
    async def test_a_set_differing_only_in_hue_is_re_asked(
            self, monkeypatch, no_previews):
        llm = _StubLLM(HUE_ONLY, GOOD)
        tool = _install(monkeypatch, llm)

        result = await tool.run(brief="a deck", session_id="s1")

        assert len(llm.prompts) == 2
        assert "rejected" in llm.prompts[1]
        assert [d["id"] for d in result.data["directions"]] == \
            ["clean", "dark", "editorial"]

    @pytest.mark.asyncio
    async def test_a_model_that_never_produces_a_set_falls_back(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(HUE_ONLY, HUE_ONLY))

        result = await tool.run(brief="a deck", session_id="s1")

        assert result.success
        assert len(result.data["directions"]) == 3
        assert [d["id"] for d in result.data["directions"]] == \
            [s.id for s in directions.default_specs()]

    @pytest.mark.asyncio
    async def test_a_response_with_no_directions_is_re_asked(
            self, monkeypatch, no_previews):
        llm = _StubLLM({"nope": True}, GOOD)
        tool = _install(monkeypatch, llm)

        result = await tool.run(brief="a deck", session_id="s1")

        assert len(llm.prompts) == 2
        assert result.success

    @pytest.mark.asyncio
    async def test_the_settled_colour_is_put_in_the_prompt_and_the_palette(
            self, monkeypatch, no_previews):
        llm = _StubLLM(GOOD)
        tool = _install(monkeypatch, llm)

        result = await tool.run(
            brief="a Starbucks deck",
            answers="Which colour? The Starbucks green #00704A",
            session_id="s1",
        )

        assert "#00704A" in llm.prompts[0]
        assert "do not propose another hue" in llm.prompts[0]
        for direction in result.data["directions"]:
            palette = {c["name"]: c["value"] for c in
                       direction["designSystem"]["palette"]["colors"]}
            assert palette["primary"] == "#00704A"

    @pytest.mark.asyncio
    async def test_what_the_user_already_said_is_in_the_prompt(
            self, monkeypatch, no_previews):
        llm = _StubLLM(GOOD)
        tool = _install(monkeypatch, llm)

        await tool.run(brief="a deck", answers="Audience: the board. Keep it short.",
                       session_id="s1")

        assert "Audience: the board" in llm.prompts[0]
        assert "settled" in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_an_uploaded_template_is_direction_one(
            self, monkeypatch, no_previews):
        uploaded = get_default_design_system("warm")
        uploaded["sourceType"] = "pptx"
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", session_id="s1",
                                design_system=uploaded)

        proposed = result.data["directions"]
        assert proposed[0]["id"] == "your-template"
        assert proposed[0]["designSystem"] is uploaded
        assert len(proposed) == 3

    @pytest.mark.asyncio
    async def test_the_built_in_system_is_not_mistaken_for_a_template(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", session_id="s1",
                                design_system=get_default_design_system())

        assert "your-template" not in [d["id"] for d in result.data["directions"]]

    @pytest.mark.asyncio
    async def test_a_model_that_raises_does_not_fail_the_run(
            self, monkeypatch, no_previews):
        class Boom:
            async def chat(self, messages, tools=None):
                raise RuntimeError("provider down")

        tool = _install(monkeypatch, Boom())

        result = await tool.run(brief="a deck", session_id="s1")

        assert result.success
        assert len(result.data["directions"]) == 3
