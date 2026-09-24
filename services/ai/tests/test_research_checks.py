"""
A finished run is checked by machine, not by reading its pages.

Each check reads only what the run left behind, so a run can be re-checked
after the checks change without spending anything to run it again.
"""

import json

import pytest

from app.research.checks import check_run, render
from app.research.task import Task

TASK = Task(id="t", category="technical", prompt="Three slides.", slides=3)


def _span(kind, name, *, status="ok", started="2026-09-24T10:00:00", attrs=None, usage=None,
          duration=1000):
    return {"kind": kind, "name": name, "status": status, "startedAt": started,
            "durationMs": duration, "attrs": attrs or {},
            "usage": usage or {"inputTokens": 0, "cachedInputTokens": 0, "outputTokens": 0}}


def _run(tmp_path, *, manifest=None, spans=()):
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"deck": "deck.pptx", "pages": 3, "termination": "complete", "mode": "live",
         **(manifest or {})}))
    (tmp_path / "trace-0.jsonl").write_text("\n".join(json.dumps(s) for s in spans))
    return tmp_path


def _by_name(result):
    return {c["name"]: c for c in result["checks"]}


class TestWhatTheRunSaysAboutItself:
    def test_the_wrong_number_of_slides_is_a_blocking_failure(self, tmp_path):
        result = check_run(_run(tmp_path, manifest={"pages": 5}), TASK)
        assert _by_name(result)["slide_count"]["passed"] is False
        assert "slide_count" in result["blockingFailures"]

    def test_a_render_after_the_last_validation_is_caught(self, tmp_path):
        """The deck that shipped must be the one validation saw (#299)."""
        spans = [
            _span("phase", "render", started="2026-09-24T10:00:01"),
            _span("phase", "validate", started="2026-09-24T10:00:02",
                  attrs={"clean": False, "issues": 2}),
            _span("phase", "render", started="2026-09-24T10:00:03"),
        ]
        checks = _by_name(check_run(_run(tmp_path, spans=spans), TASK))
        assert checks["shipped_what_was_validated"]["passed"] is False
        assert checks["validation_converged"]["passed"] is False
        assert "1 round(s), 2 issue(s) left" in checks["validation_converged"]["detail"]

    def test_failed_tool_calls_are_named_and_counted(self, tmp_path):
        spans = [_span("tool", "build_table", status="failed")] * 2 + [_span("tool", "build_chart")]
        detail = _by_name(check_run(_run(tmp_path, spans=spans), TASK))["no_failed_tool_calls"]["detail"]
        assert detail == "build_table x2"

    def test_spend_and_tokens_are_summed_from_the_model_calls(self, tmp_path):
        spans = [
            _span("run", "run", duration=90_000),
            _span("llm", "chat", usage={"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 50}),
            _span("llm", "chat", usage={"inputTokens": 500, "cachedInputTokens": 0, "outputTokens": 20}),
        ]
        metrics = check_run(_run(tmp_path, manifest={"spend": {"costUsd": 0.012}}, spans=spans),
                            TASK)["metrics"]
        assert (metrics["modelCalls"], metrics["inputTokens"], metrics["cachedInputTokens"]) == (2, 1500, 400)
        assert metrics["costUsd"] == 0.012 and metrics["runSeconds"] == 90.0

    def test_a_run_without_a_deck_cannot_be_checked_for_geometry(self, tmp_path):
        result = check_run(_run(tmp_path, manifest={"deck": None, "pages": 0,
                                                    "termination": "stalled"}), TASK)
        checks = _by_name(result)
        assert checks["delivered"]["passed"] is False
        assert checks["text_within_the_page"]["passed"] is None
        assert result["decided"] == len(result["checks"]) - 1

    def test_the_result_is_written_next_to_the_run(self, tmp_path):
        check_run(_run(tmp_path), TASK)
        assert json.loads((tmp_path / "checks.json").read_text())["task"] == "t"
        assert "checks passed" in render(json.loads((tmp_path / "checks.json").read_text()))


class TestWhatTheRenderedPagesSay:
    @pytest.fixture
    def deck_pdf(self, tmp_path):
        import fitz

        doc = fitz.open()
        for body in (18, 18, 10):   # the last page's body is caption-sized
            page = doc.new_page(width=960, height=540)
            page.insert_text((60, 90), "A headline that states the finding", fontsize=36)
            y = 160
            for _ in range(6):
                page.insert_text((60, y), "Body copy that carries the page's argument, set at one size.",
                                 fontsize=body)
                y += body * 2
        (tmp_path / "png" / "pages").mkdir(parents=True)
        doc.save(str(tmp_path / "png" / "pages" / "deck.pdf"))
        return tmp_path

    def test_caption_sized_body_copy_is_flagged_by_page(self, deck_pdf):
        checks = _by_name(check_run(_run(deck_pdf), TASK))
        assert checks["body_legible"]["passed"] is False
        assert checks["body_legible"]["detail"].startswith("page 3: 10")
        assert checks["headlines_within_the_ramp"]["passed"] is True
        assert checks["text_within_the_page"]["passed"] is True


class TestReviewedGaps:
    def test_a_tool_that_raised_or_never_finished_counts_as_failed(self, tmp_path):
        spans = [_span("tool", "build_table", status="error"),
                 _span("tool", "render", status="unfinished")]
        check = _by_name(check_run(_run(tmp_path, spans=spans), TASK))["no_failed_tool_calls"]
        assert check["passed"] is False and "build_table x1" in check["detail"]

    def test_a_script_edit_after_validation_is_an_unvalidated_deck(self, tmp_path):
        spans = [
            _span("phase", "validate", started="2026-09-24T10:00:02", attrs={"clean": False}),
            _span("tool", "edit_deck_with_code", started="2026-09-24T10:00:05"),
        ]
        check = _by_name(check_run(_run(tmp_path, spans=spans), TASK))["shipped_what_was_validated"]
        assert check["passed"] is False and "edit_deck_with_code" in check["detail"]

    def test_a_run_without_its_task_file_says_so(self, tmp_path):
        with pytest.raises(ValueError, match="pass the task explicitly"):
            check_run(_run(tmp_path, manifest={"taskFile": None}))
