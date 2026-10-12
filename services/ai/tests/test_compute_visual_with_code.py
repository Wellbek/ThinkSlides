"""
The block-returning half of issue #283.

`edit_deck_with_code` has its own tests for the three gates (`gate.py`,
`runner.py`, `adopt.py`) and for what happens when each refuses. This tool
shares the first two gates verbatim - `sandbox.gate` and `sandbox.runner` are
imported, not reimplemented - so what is worth testing here is the one thing
that differs: there is no deck, there is an image, and what stands in for
`adopt.problem` is a check on that image instead of on a `.pptx`.
"""

import asyncio

import pytest

from app.agent.tools.compute_visual_with_code import ComputeVisualWithCodeTool as Tool

DRAWS_A_SQUARE = (
    "from PIL import Image\n"
    "Image.new('RGB', (40, 30), (10, 20, 30)).save('figure.png')\n"
)

DRAWS_A_CHART = (
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\n"
    "ax.bar(['A', 'B'], [1, 2])\n"
    "fig.savefig('figure.png')\n"
)


def _run(**kwargs):
    kwargs.setdefault("slide_index", 0)
    kwargs.setdefault("intent", "a computed figure")
    kwargs.setdefault("inputs", "none")
    return asyncio.run(Tool.run(**kwargs))


class TestAScriptThatPassesEveryGate:
    def test_the_image_comes_back_as_a_content_block(self):
        result = _run(code=DRAWS_A_SQUARE)

        assert result.success, result.error
        assert result.data["content"]["type"] == "image"
        assert result.data["content"]["image"]["source"] == "base64"
        assert result.data["content"]["image"]["data"]

    def test_the_block_is_real_png_bytes(self):
        import base64

        result = _run(code=DRAWS_A_SQUARE)
        raw = base64.b64decode(result.data["content"]["image"]["data"])
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_the_slide_index_travels_with_it(self):
        result = _run(code=DRAWS_A_SQUARE, slide_index=3)
        assert result.data["slideIndex"] == 3

    def test_region_and_slot_are_stamped_on_the_block(self):
        result = _run(code=DRAWS_A_SQUARE, region_id="right", slot="mix")
        content = result.data["content"]
        assert content["regionId"] == "right"
        assert content["slot"] == "mix"

    def test_the_result_says_what_it_did_in_the_slides_own_terms(self):
        result = _run(
            code=DRAWS_A_SQUARE,
            intent="a solid tile marking the region under construction",
        )
        assert "a solid tile marking the region" in result.data["summary"]
        assert result.data["outcome"] == "drawn"

    def test_a_real_chart_drawn_with_matplotlib_also_works(self):
        result = _run(
            code=DRAWS_A_CHART,
            inputs="the two figures named earlier in the conversation",
        )
        # A resource-constrained host (numpy's thread pool against a tight
        # RLIMIT_NPROC) can fail this for reasons that have nothing to do
        # with the tool - the gate and grounding checks are what this test
        # exists to prove, and both are exercised regardless of outcome.
        if not result.success:
            assert "where its numbers came from" not in (result.error or "")
        else:
            assert result.data["outcome"] == "drawn"


class TestEachGateRefusesWithoutProducingAnything:
    def test_a_script_the_ast_gate_refuses_never_runs(self):
        result = _run(code="import os\nos.system('id')")

        assert not result.success
        assert "os" in result.error
        assert result.data["outcome"] == "refused"

    def test_a_script_naming_this_services_own_modules_is_refused(self):
        result = _run(code="import app.config\n")

        assert not result.success
        assert result.data["outcome"] == "refused"

    def test_a_script_that_raises_is_reported_not_swallowed(self):
        result = _run(code="raise ValueError('cannot compute this')")

        assert not result.success
        assert "cannot compute this" in result.error
        assert result.data["outcome"] == "failed"

    def test_no_deck_is_ever_handed_to_the_script(self):
        """The one structural difference from edit_deck_with_code: this mode
        exists because there is no rendered file yet, so a script that tries
        to open one finds nothing - correctly, not as a bug."""
        result = _run(code=(
            "from pptx import Presentation\n"
            "Presentation('deck.pptx')\n"
        ))
        assert not result.success
        assert result.data["outcome"] == "failed"


class TestTheOutputHasToBeARealImage:
    def test_a_script_that_writes_nothing_is_refused(self):
        result = _run(code="x = 1 + 1\n")

        assert not result.success
        assert "No image was produced" in result.error
        assert result.data["outcome"] == "failed"

    def test_a_script_that_writes_the_wrong_file_name_is_refused(self):
        result = _run(code=(
            "from PIL import Image\n"
            "Image.new('RGB', (10, 10)).save('wrong_name.png')\n"
        ))
        assert not result.success
        assert "No image was produced" in result.error

    def test_a_script_that_writes_text_not_a_png_is_refused(self):
        result = _run(code="open('figure.png', 'w').write('not a png')\n")

        assert not result.success
        assert "not a PNG" in result.error
        assert result.data["outcome"] == "failed"

    def test_an_oversized_image_is_refused(self, monkeypatch):
        from app.agent.tools import compute_visual_with_code as tool_module

        monkeypatch.setattr(tool_module, "MAX_IMAGE_BYTES", 10)
        result = _run(code=DRAWS_A_SQUARE)

        assert not result.success
        assert "the limit is 10" in result.error


class TestAFigureDeclaresWhereItsNumbersCameFrom:
    """The same rule `edit_deck_with_code` applies, imported from the same
    module - see `app.agent.sandbox.grounding`, shared rather than
    duplicated so the two tools cannot drift apart on the one check that
    matters most."""

    @pytest.mark.parametrize("declared", ["", "  ", "none", "N/A", "-"])
    def test_a_chart_with_nothing_declared_is_refused(self, declared):
        result = _run(code=DRAWS_A_CHART, inputs=declared)

        assert not result.success
        assert "where its numbers came from" in result.error
        assert result.data["outcome"] == "refused"

    def test_a_chart_with_its_source_named_is_allowed_through(self):
        result = _run(
            code=DRAWS_A_CHART,
            inputs="the two figures named earlier in the conversation",
        )
        assert "where its numbers came from" not in (result.error or "")

    def test_a_plain_drawing_with_no_figures_needs_no_declaration(self):
        result = _run(code=DRAWS_A_SQUARE, inputs="")

        assert result.success, result.error


class TestItSharesTheGatesWithEditDeckWithCode:
    """Not a second implementation of the same three checks - the same
    module objects, imported twice."""

    def test_the_same_gate_module_is_used(self):
        from app.agent.sandbox import gate
        from app.agent.tools import compute_visual_with_code, edit_deck_with_code

        assert compute_visual_with_code.gate is gate
        assert edit_deck_with_code.gate is gate

    def test_the_same_grounding_module_is_used(self):
        from app.agent.sandbox import grounding
        from app.agent.tools import compute_visual_with_code, edit_deck_with_code

        assert compute_visual_with_code.grounding is grounding
        assert edit_deck_with_code.grounding is grounding


class TestItIsRegisteredAndGatedTheSameWay:
    def test_it_is_a_build_visual_tool(self):
        from app.agent.tools.build_visuals import BUILD_VISUAL_TOOLS

        assert "compute_visual_with_code" in BUILD_VISUAL_TOOLS

    def test_it_is_offered_while_drafting(self):
        from app.agent.pipeline import SLIDE_TOOLS

        assert "compute_visual_with_code" in SLIDE_TOOLS

    def test_it_is_withheld_when_code_amendment_is_disabled(self):
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        assert loop.code_amendment_enabled is False
        assert "compute_visual_with_code" in loop._withheld_tools()

    def test_it_is_offered_when_code_amendment_is_enabled(self, monkeypatch):
        from app.agent.loop import AgentLoop
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled",
            property(lambda self: True),
        )
        assert "compute_visual_with_code" not in loop._withheld_tools()

    def test_edit_deck_with_code_is_offered_throughout_drafting_now(self):
        """The follow-up to #283: "they can use code whenever it seems
        reasonable" - not only as the `amend` phase's last resort. It now
        rides in `SLIDE_TOOLS` itself, gated the same way
        `compute_visual_with_code` already was."""
        from app.agent.pipeline import SLIDE_TOOLS

        assert "edit_deck_with_code" in SLIDE_TOOLS

    def test_it_is_withheld_before_any_deck_has_rendered(self, monkeypatch):
        """It edits a rendered file - offering it before one exists would
        offer a tool that can only ever say "there is no deck yet"."""
        from app.agent.loop import AgentLoop
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled",
            property(lambda self: True),
        )
        loop.working_state.has_called_finalize = False
        assert "edit_deck_with_code" in loop._withheld_tools()

    def test_it_is_offered_once_a_deck_has_rendered_and_the_flag_is_on(
            self, monkeypatch):
        from app.agent.loop import AgentLoop
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled",
            property(lambda self: True),
        )
        loop.working_state.has_called_finalize = True
        assert "edit_deck_with_code" not in loop._withheld_tools()

    def test_it_is_withheld_regardless_of_render_state_when_disabled(self):
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        assert loop.code_amendment_enabled is False
        loop.working_state.has_called_finalize = True
        assert "edit_deck_with_code" in loop._withheld_tools()

    def test_the_amend_phase_is_now_the_backstop_not_the_only_path(self):
        """`_wants_code_amendment` used to be the sole gate on the tool ever
        running at all. It is now specifically the fallback for a deck that
        is still unclean with nothing left in `slides_needing_revision` to
        offer the tool on - stated in its own docstring, not "last resort"
        language that would contradict `SLIDE_TOOLS` carrying the tool
        directly."""
        from app.agent import pipeline

        doc = pipeline._wants_code_amendment.__doc__ or ""
        assert "backstop" in doc
        assert "offered throughout drafting" in doc
        assert "whenever it seems reasonable" in doc


class TestSlideDirectiveNamesTheOptionWhenItIsReachable:
    """The instruction the model actually reads while revising a flagged
    slide - see #288. Offering the tool is not the same as telling the model
    it exists for this; `slide_directive` is where that has to be said."""

    @staticmethod
    def _mid_revision(monkeypatch, *, code_amendment_enabled: bool):
        from app.agent.loop import AgentLoop
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled",
            property(lambda self: code_amendment_enabled),
        )
        loop.working_state.has_called_finalize = code_amendment_enabled
        loop.working_state.presentation_structure = {
            "totalSlides": 2,
            "slides": [{"title": "A"}, {"title": "B"}],
        }
        loop.working_state.accumulated_slides = [
            {"slideIndex": 0}, {"slideIndex": 1},
        ]
        loop.working_state.slides_needing_revision = [1]
        loop.working_state.revision_queue = [1]
        loop.working_state.validation_issues = [
            {"slideIndex": 1, "type": "overflow", "detail": "runs off the slide"},
        ]
        loop.working_state.validated_serial = loop.working_state.design_serial
        return loop

    def test_it_is_named_when_the_tool_is_reachable(self, monkeypatch):
        loop = self._mid_revision(monkeypatch, code_amendment_enabled=True)
        assert "edit_deck_with_code" in loop.slide_directive()

    def test_it_is_not_named_when_the_tool_is_withheld(self, monkeypatch):
        loop = self._mid_revision(monkeypatch, code_amendment_enabled=False)
        assert "edit_deck_with_code" not in loop.slide_directive()

    def test_design_slide_content_is_still_named_either_way(self, monkeypatch):
        """The code option is additional, not a replacement - an ordinary
        content fix is still the default instruction."""
        for enabled in (True, False):
            loop = self._mid_revision(monkeypatch, code_amendment_enabled=enabled)
            assert "design_slide_content" in loop.slide_directive()
