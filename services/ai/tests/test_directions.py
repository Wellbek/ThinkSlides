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
            "motif": "rule", "structure": "list"}
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
        """And in the key ChromeSpec reads. Declaring {"show": "never"} read
        as an element with no opinion, so it defaulted to on - the one
        direction asking for no footer rule was the only one drawing one."""
        from app.renderer.chrome import ChromeSpec

        no_rule = directions.compile_direction(_spec(motif="none"))
        banded = directions.compile_direction(_spec(motif="band"))

        assert ChromeSpec.from_design_system(no_rule).footer_rule.enabled is False
        assert ChromeSpec.from_design_system(banded).footer_rule.enabled is True

    def test_the_structure_travels_on_the_design_system(self):
        """It is what the specimen deck shapes its points with, and what the
        drafting phase tells the model to keep shaping them with."""
        system = directions.compile_direction(_spec(structure="bands"))

        assert system["structure"] == "bands"

    def test_a_spec_is_read_from_whatever_the_model_sent(self):
        spec = directions.DirectionSpec.from_dict({
            "name": "Dark Analyst", "ground": "DARK", "density": "dense",
            "displayFace": "Georgia", "bodySizePt": "15", "structure": "BANDS",
            "paletteHex": "1b4332",
        })

        assert spec.id == "dark-analyst"
        assert spec.ground == "dark"
        assert spec.body_pt == 15.0
        assert spec.display_is_serif is True
        assert spec.structure == "bands"
        assert spec.palette_hex == "#1B4332"

    def test_nonsense_from_the_model_falls_back_rather_than_raising(self):
        spec = directions.DirectionSpec.from_dict({
            "ground": "purple", "density": "extremely", "motif": "swoosh",
            "displaySizePt": "huge", "structure": "swirls",
            "paletteHex": "not a colour",
        })

        assert (spec.ground, spec.density, spec.motif) == ("light", "balanced", "rule")
        assert spec.display_pt == 36.0
        assert spec.structure == "list"
        assert spec.palette_hex == ""

    def test_a_spec_round_trips_so_it_can_be_compiled_again(self):
        """The colour is answered in the same pause the direction is chosen
        in, so the chosen direction has to be recompilable after the fact."""
        spec = _spec(structure="cards", palette_hex="#1B4332", motif="band")

        assert directions.DirectionSpec.from_dict(spec.to_dict()) == spec


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

    def test_with_nothing_settled_a_direction_proposes_its_own_hue(self):
        """Every direction of every deck used to resolve to the house navy,
        so a preview said nothing about the subject it was drawn for."""
        system = directions.compile_direction(_spec(palette_hex="#1B4332"))
        palette = {c["name"]: c["value"] for c in system["palette"]["colors"]}

        assert palette["primary"] == "#1B4332"

    def test_a_settled_colour_still_overrules_the_proposed_one(self):
        system = directions.compile_direction(_spec(palette_hex="#1B4332"),
                                              brand_hex="#00704A")
        palette = {c["name"]: c["value"] for c in system["palette"]["colors"]}

        assert palette["primary"] == "#00704A"


class TestRecolouringTheChosenDirection:
    """The colour rides in the same card as the direction, so its answer
    lands after the specimens were drawn - it is applied afterwards, to the
    one direction the user actually picked."""

    @staticmethod
    def _chosen(**kwargs):
        compiled, _ = directions.validate([_spec(**kwargs)])
        return compiled[0]

    def test_the_chosen_direction_is_compiled_again_in_the_new_hue(self):
        recoloured = directions.recolour(self._chosen(), "#00704A")
        palette = {c["name"]: c["value"] for c in
                   recoloured["designSystem"]["palette"]["colors"]}

        assert palette["primary"] == "#00704A"

    def test_everything_else_about_the_direction_survives_it(self):
        chosen = self._chosen(ground="dark", structure="bands", motif="band")

        recoloured = directions.recolour(chosen, "#00704A")

        assert recoloured["designSystem"]["ground"] == "dark"
        assert recoloured["designSystem"]["structure"] == "bands"
        assert recoloured["id"] == chosen["id"]

    def test_a_colour_nobody_could_read_is_not_applied(self):
        """A user who asked for a colour and got unreadable body copy has been
        given a worse deck than the one they were looking at."""
        chosen = self._chosen(ground="split")

        assert directions.recolour(chosen, "#7A7A7A") == chosen

    def test_nonsense_leaves_the_direction_alone(self):
        chosen = self._chosen()

        assert directions.recolour(chosen, "cornflower") == chosen

    def test_an_uploaded_template_is_never_recoloured(self):
        """Its palette is the user's own and is not ours to overwrite."""
        uploaded = directions.template_direction(get_default_design_system("warm"))

        assert directions.recolour(uploaded, "#00704A") == uploaded


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

    def test_shaping_the_points_identically_everywhere_is_rejected(self):
        """Three covers a user can tell apart, and three content slides they
        cannot, is a comparison of colour - which is #151."""
        same = [_spec(id="a", name="A", ground="light", density="airy"),
                _spec(id="b", name="B", ground="dark", density="dense"),
                _spec(id="c", name="C", ground="split", motif="none")]

        problems = directions.distinctness_problems(same)

        assert any("the same slide" in problem for problem in problems)

    def test_structure_alone_can_make_two_directions_two_directions(self):
        pair = [_spec(id="a", name="A", ground="light", structure="stats"),
                _spec(id="b", name="B", ground="dark", structure="bands")]

        assert directions.distinctness_problems(pair) == []

    def test_the_default_set_passes_its_own_check(self):
        assert directions.distinctness_problems(directions.default_specs()) == []

    def test_the_default_set_shapes_its_points_three_different_ways(self):
        assert len({spec.structure for spec in directions.default_specs()}) == 3

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


def _payload(*specs, **extra):
    return {"directions": list(specs), **extra}


CONTENT = {
    "title": "Cold chain in 2026",
    "subtitle": "Where the next phase of margin comes from",
    "headline": "Three shifts decide the next twelve months",
    "points": [
        {"label": "Adoption", "figure": "24%", "prose": "of depots now run it",
         "icon": "adoption"},
        {"label": "Cost", "figure": "$1.8m", "prose": "of run-rate released",
         "icon": "cost"},
        {"label": "Speed", "figure": "6 weeks", "prose": "to a live deployment",
         "icon": "speed"},
    ],
    "evidenceHeadline": "Growth held in every measured year",
    "takeaway": "Both lines have compounded since the programme began",
    "chart": {"title": "Depots live, percent",
              "categories": ["2022", "2023", "2024"],
              "series": [{"name": "Core", "values": [18, 36, 54]}]},
}
COLOURS = [{"name": "Forest green", "hex": "#1B4332"},
           {"name": "Signal red", "hex": "#8B1E1E"}]

GOOD = _payload(
    {"id": "clean", "name": "Clean minimal", "rationale": "Airy.",
     "ground": "light", "displayFace": "Arial", "bodyFace": "Arial",
     "density": "airy", "motif": "rule", "structure": "stats"},
    {"id": "dark", "name": "Dark analyst", "rationale": "Dense.",
     "ground": "dark", "displayFace": "Arial", "bodyFace": "Arial",
     "density": "dense", "motif": "band", "structure": "bands"},
    {"id": "editorial", "name": "Editorial serif", "rationale": "Written.",
     "ground": "split", "displayFace": "Georgia", "bodyFace": "Arial",
     "density": "balanced", "motif": "none", "structure": "list"},
    content=CONTENT, colorOptions=COLOURS,
)
HUE_ONLY = _payload(
    *[{"id": f"d{n}", "name": f"D{n}", "rationale": "r", "ground": "light",
       "displayFace": "Arial", "bodyFace": "Arial", "density": "balanced",
       "motif": "rule", "structure": "list"} for n in range(3)]
)
ONE_SHAPE = _payload(
    {"id": "clean", "name": "Clean minimal", "rationale": "Airy.",
     "ground": "light", "displayFace": "Arial", "bodyFace": "Arial",
     "density": "airy", "motif": "rule", "structure": "list"},
    {"id": "dark", "name": "Dark analyst", "rationale": "Dense.",
     "ground": "dark", "displayFace": "Arial", "bodyFace": "Arial",
     "density": "dense", "motif": "band", "structure": "list"},
    {"id": "editorial", "name": "Editorial serif", "rationale": "Written.",
     "ground": "split", "displayFace": "Georgia", "bodyFace": "Arial",
     "density": "balanced", "motif": "none", "structure": "list"},
)


@pytest.fixture
def no_previews(monkeypatch):
    """The tool's own logic, without paying for LibreOffice three times."""
    from app.renderer import specimen

    rendered: dict = {}

    async def fake(directions_, session_id, output_dir=None, content=None):
        rendered["content"] = content
        rendered["structures"] = [
            (d.get("designSystem") or {}).get("structure") for d in directions_
        ]
        return {d["id"]: [f"/previews/{session_id}/direction/{d['id']}/1.png"]
                for d in directions_}

    monkeypatch.setattr(specimen, "render_directions", fake)
    import app.agent.tools.propose_design_directions as tool

    monkeypatch.setattr(tool.specimen, "render_directions", fake)
    return rendered


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
    async def test_a_set_that_shapes_every_slide_alike_is_re_asked(
            self, monkeypatch, no_previews):
        """Three different covers over three identical content slides is a
        comparison of colour, which is what #151 opened on."""
        llm = _StubLLM(ONE_SHAPE, GOOD)
        tool = _install(monkeypatch, llm)

        await tool.run(brief="a deck", session_id="s1")

        assert len(llm.prompts) == 2
        assert "the same slide" in llm.prompts[1]

    @pytest.mark.asyncio
    async def test_each_direction_previews_its_own_structure(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(GOOD))

        await tool.run(brief="a deck", session_id="s1")

        assert no_previews["structures"] == ["stats", "bands", "list"]

    @pytest.mark.asyncio
    async def test_one_set_of_copy_is_written_for_all_three(
            self, monkeypatch, no_previews):
        """Holding the words constant is what makes three previews a
        comparison of design; holding them at lorem ipsum is what made every
        run's previews identical to every other run's."""
        tool = _install(monkeypatch, _StubLLM(GOOD))

        await tool.run(brief="a deck on cold chain logistics", session_id="s1")

        content = no_previews["content"]
        assert content.title == "Cold chain in 2026"
        assert [p.figure for p in content.points] == ["24%", "$1.8m", "6 weeks"]

    @pytest.mark.asyncio
    async def test_copy_the_model_did_not_write_falls_back_rather_than_failing(
            self, monkeypatch, no_previews):
        """The specimen deck supplies its own copy when there is none, so a
        model that answered with directions alone still gets previews."""
        tool = _install(monkeypatch, _StubLLM(_payload(*GOOD["directions"])))

        result = await tool.run(brief="a deck", session_id="s1")

        assert no_previews["content"] is None
        assert all(d["previewUrls"] for d in result.data["directions"])

    @pytest.mark.asyncio
    async def test_the_colour_is_the_second_question_on_the_same_card(
            self, monkeypatch, no_previews):
        """It used to be asked in the consultation and then was not asked at
        all, which left every deck in the house navy."""
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", session_id="s1")

        questions = result.data["questions"]
        assert len(questions) == 2
        assert questions[1]["options"][0].startswith("As proposed")
        assert "Forest green (#1B4332)" in questions[1]["options"]

    @pytest.mark.asyncio
    async def test_a_colour_already_settled_is_not_asked_about_again(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", answers="Use #00704A",
                                session_id="s1")

        assert len(result.data["questions"]) == 1

    @pytest.mark.asyncio
    async def test_an_uploaded_template_is_not_asked_about_either(
            self, monkeypatch, no_previews):
        """Its own palette is used as-is."""
        uploaded = get_default_design_system("warm")
        uploaded["sourceType"] = "pptx"
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", session_id="s1",
                                design_system=uploaded)

        assert len(result.data["questions"]) == 1

    @pytest.mark.asyncio
    async def test_a_direction_is_proposed_in_its_own_colour(
            self, monkeypatch, no_previews):
        tool = _install(monkeypatch, _StubLLM(_payload(
            *[{**d, "paletteHex": hex_value} for d, hex_value in
              zip(GOOD["directions"], ("#1B4332", "#8B1E1E", "#2F3E9E"))],
        )))

        result = await tool.run(brief="a deck", session_id="s1")

        primaries = [
            {c["name"]: c["value"]
             for c in d["designSystem"]["palette"]["colors"]}["primary"]
            for d in result.data["directions"]
        ]
        assert primaries == ["#1B4332", "#8B1E1E", "#2F3E9E"]

    @pytest.mark.asyncio
    async def test_every_direction_carries_the_spec_it_was_compiled_from(
            self, monkeypatch, no_previews):
        """So the chosen one can be compiled again once the colour answer
        arrives - it lands after the specimens were drawn."""
        tool = _install(monkeypatch, _StubLLM(GOOD))

        result = await tool.run(brief="a deck", session_id="s1")

        for direction in result.data["directions"]:
            assert direction["spec"]["structure"]

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
