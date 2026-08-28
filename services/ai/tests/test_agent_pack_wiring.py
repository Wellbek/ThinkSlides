"""
Whether a real run can reach the design language engine.

Everything in `app/design` passed its own gates and delivered nothing, because
nothing under `app/agent` imported it. `grep -rn "app\\.design" app/agent/`
returned nothing, so every generation - including the ones on the strong model
- went through `select_layout`, a keyword match over eight hardcoded layouts,
and rendered title-plus-bullets.

These pin the four decisions that wiring it in required:

* an uploaded template keeps the template-faithful path, because that is the
  promise made to the customer who supplied one;
* the pack is chosen once, from the plan, and a resumed run keeps it;
* the tools that decide *where things go* are withheld on the pack path, or
  two systems are deciding the same thing;
* the render routes to the composed path without the model being asked.
"""
import pytest

from app.agent import pipeline, run_context
from app.design import run as design_run


class _Loop:
    """What `_resolve_pack` reads, and nothing else.

    Every method it calls is bound explicitly rather than inherited, so
    adding one to the real loop shows up here as a failure rather than as a
    silently disabled feature: `_resolve_pack` catches everything and logs
    `pack_selection_failed`, so a missing method would have turned the whole
    design-language path off with a warning nobody was reading.
    """

    def __init__(self):
        from app.agent.loop import AgentLoop, WorkingState

        self.working_state = WorkingState()
        self._pack_token = None
        self._resolve_pack = AgentLoop._resolve_pack.__get__(self)
        self._install_pack = AgentLoop._install_pack.__get__(self)
        self._authored_pack = AgentLoop._authored_pack.__get__(self)


PLAN = {"totalSlides": 2, "slides": [
    {"slideIndex": 0, "title": "Insurance risk in 2026",
     "contentType": "title", "keyPoints": []},
    {"slideIndex": 1, "title": "What the CROs told us",
     "contentType": "content", "keyPoints": [
         "The results of our inaugural survey of insurance industry CROs "
         "demonstrate the expanding and diversifying risks insurers face.",
         "Many of the most pressing risks relate to the profound shifts "
         "reshaping the industry and to rising regulatory requirements.",
     ]},
]}

DEFAULT_DS = {"sourceType": "default"}
UPLOADED_DS = {"sourceType": "pptx"}


@pytest.fixture
def enabled(monkeypatch):
    """The path is opt-in until the packs pass their own gate (#275)."""
    from app.config import settings

    monkeypatch.setattr(settings, "design_language_enabled", True)


@pytest.fixture
def loop(enabled):
    instance = _Loop()
    yield instance
    if instance._pack_token is not None:
        run_context.reset_pack(instance._pack_token)


class TestWhoGetsAPack:
    def test_a_run_with_no_template_gets_one(self, loop):
        loop._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        assert loop.working_state.pack_id in design_run.available()

    def test_an_uploaded_template_does_not(self, loop):
        """That customer was promised their own template, and the XML-clone
        renderer is what keeps the promise."""
        loop._resolve_pack(PLAN, {"design_system": UPLOADED_DS})
        assert loop.working_state.pack_id is None
        assert run_context.get_pack() is None

    def test_a_run_with_no_design_system_yet_gets_one(self, loop):
        loop._resolve_pack(PLAN, {})
        assert loop.working_state.pack_id is not None

    def test_an_empty_plan_leaves_the_run_where_it_was(self, loop):
        loop._resolve_pack({"slides": []}, {"design_system": DEFAULT_DS})
        assert loop.working_state.pack_id is None


class TestTheChoiceIsMadeOnceAndKept:
    def test_a_second_plan_does_not_change_house_style(self, loop):
        loop._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        first = loop.working_state.pack_id
        loop._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        assert loop.working_state.pack_id == first

    def test_a_resumed_run_reinstalls_the_pack_it_chose(self, loop):
        """A ContextVar does not survive the process, so a resumed run knows
        which pack it is on and has nothing installed."""
        loop.working_state.pack_id = "wti-2025"
        assert run_context.get_pack() is None
        loop._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        installed = run_context.get_pack()
        assert installed is not None and installed.id == "wti-2025"

    def test_the_pack_reaches_the_tools(self, loop):
        loop._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        assert run_context.get_pack() is not None

    def test_the_chosen_direction_is_on_the_installed_pack(self, loop):
        loop._resolve_pack(PLAN, {"design_system": {
            "sourceType": "default",
            "palette": {"colors": [{"name": "primary", "value": "#1E8449"}]},
        }})
        assert run_context.get_pack().colors.accent == "#1E8449"

    def test_a_pack_failure_never_fails_the_run(self, loop, monkeypatch):
        monkeypatch.setattr(design_run, "for_plan",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        loop._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        assert loop.working_state.pack_id is None


class TestItIsOnNowThatThePacksPassTheirGates:
    """It was opt-in while the packs were not fit to put in front of a user:
    they carried chart axis labels as content slots (#275), described pages
    the renderer refuses to place (#276), and were traces of five specific
    pages rather than recovered patterns (#268). All three pass now, asserted
    against the shipped packs in test_pack_quality.py."""

    def test_it_is_on_by_default(self):
        from app.config import Settings

        assert Settings().design_language_enabled is True

    def test_it_can_still_be_turned_off(self, monkeypatch):
        """A switch that cannot be thrown back is not a switch, and this one
        is the difference between a composed deck and the legacy path."""
        from app.config import settings

        monkeypatch.setattr(settings, "design_language_enabled", False)
        instance = _Loop()
        instance._resolve_pack(PLAN, {"design_system": DEFAULT_DS})
        assert instance.working_state.pack_id is None


class TestGeometryToolsAreWithheldOnThePackPath:
    def test_they_are_named_rather_than_guessed_at(self):
        assert "select_layout" in pipeline.TEMPLATE_GEOMETRY_TOOLS
        assert "compose_slide_regions" in pipeline.TEMPLATE_GEOMETRY_TOOLS

    def test_they_are_part_of_the_template_path_toolset(self):
        assert pipeline.TEMPLATE_GEOMETRY_TOOLS <= pipeline.SLIDE_TOOLS

    def test_what_remains_still_builds_a_slide(self):
        """Withholding the two must not leave the drafting phase unable to
        write a slide at all."""
        remaining = pipeline.SLIDE_TOOLS - pipeline.TEMPLATE_GEOMETRY_TOOLS
        assert "design_slide_content" in remaining
        assert any(name.startswith("build_") for name in remaining)


class TestTheRenderRoutesWithoutBeingAsked:
    def test_the_composed_path_produces_a_file(self, tmp_path):
        from app.agent.tools.finalize_and_render_pptx import FinalizeAndRenderPptxTool

        pack = design_run.load("ey-iif-risk")
        slides = [
            {"slideIndex": 0, "contents": [
                {"type": "text", "runs": [{"text": "Insurance risk in 2026"}]}]},
            {"slideIndex": 1, "contents": [
                {"type": "text", "runs": [{"text": "What the CROs told us"}]},
                {"type": "text", "runs": [
                    {"text": "Cyber remains the single most cited near-term "
                             "risk, named by 61% of respondents."},
                    {"text": "Regulatory change follows at 48%, up nine "
                             "points on last year."},
                ]},
            ]},
        ]
        out = str(tmp_path / "composed.pptx")
        written = FinalizeAndRenderPptxTool._render_composed(
            slides, pack, {"title": "Deck"}, out, {}
        )
        import os
        assert os.path.getsize(written) > 0

    def test_it_refuses_a_document_that_cannot_be_placed(self, tmp_path):
        """Validation runs in code here. The check it replaces was a tool the
        model could decline to call, and it was called zero times across every
        observed run."""
        from app.renderer.designdoc_renderer import InvalidDesignDoc, render_doc
        from app.design.schema import (
            DesignDoc, FracPlace, Frame, Page, PackRef,
        )

        pack = design_run.load("ey-iif-risk")
        page = Page(id="p", archetype="test", frames=(
            Frame(id="a", role="panel",
                  place=FracPlace(x=0.0, y=0.0, w=1.0, h=1.0), z=0),
            Frame(id="b", role="panel",
                  place=FracPlace(x=0.1, y=0.1, w=0.8, h=0.8), z=0),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        with pytest.raises(InvalidDesignDoc):
            render_doc(doc, pack, str(tmp_path / "out.pptx"))


class TestTheChoiceSurvivesARestart:
    def test_it_is_written_into_the_saved_state(self):
        import inspect

        from app.agent.loop import AgentLoop

        source = inspect.getsource(AgentLoop)
        assert '"pack_id": self.working_state.pack_id' in source
        assert 'state_dict.get("pack_id")' in source
