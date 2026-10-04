"""
Deterministic checks on a finished research run, written to checks.json.

Everything here is read off what the run left behind - its manifest, its
span trees and its rendered PDF - so a run can be checked again later, after
the checks change, without running it again. Nothing calls a model: the
checklist a judge answers is a separate step (#315).

A check is ``blocking`` when a deck that fails it should not reach a user
(no deck, wrong slide count, text off the page), and advisory otherwise.
Thresholds are stated where they are defined, with the reason for each.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.research.task import Task

# A content-page headline above this is set at a cover's display size, and a
# sentence-length headline at that size wraps and clips (#302). 48pt is the
# top of the title range in Anthropic's pptx guidance (36-44pt) with margin.
MAX_HEADLINE_PT = 48.0
# Body copy below this is caption-sized on a projected 16:9 slide; the pptx
# guidance puts body at 14-16pt.
MIN_BODY_PT = 14.0


@dataclass
class Check:
    name: str
    passed: bool | None      # None: could not be decided from what the run left
    detail: str
    blocking: bool = False


def _spans(run_dir: Path) -> list[dict]:
    spans = []
    for part in sorted(run_dir.glob("trace-*.jsonl"), key=lambda p: int(p.stem.split("-")[1])):
        spans += [json.loads(line) for line in part.read_text().splitlines() if line]
    return spans


def _pdf(run_dir: Path) -> Path | None:
    found = sorted(run_dir.glob("png/**/deck.pdf"))
    return found[0] if found else None


def _checks(run_dir: Path, task: Task, manifest: dict, spans: list[dict], pdf: Path | None,
            pages: list) -> list[Check]:
    checks = [Check("delivered", bool(manifest.get("deck")),
                    f"deck: {manifest.get('deck') or 'none'}", blocking=True)]

    termination = manifest.get("termination") or "?"
    checks.append(Check("terminated_cleanly", termination == "complete",
                        f"termination: {termination}"))

    if task.slides:
        count = manifest.get("pages") or 0
        checks.append(Check("slide_count", count == task.slides,
                            f"{count} pages, task asks for {task.slides}", blocking=True))

    if pdf is None:
        checks.append(Check("text_within_the_page", None, "no rendered PDF"))
    else:
        from app.agent.tools.validate_rendered_output import (
            ValidateRenderedOutputTool,
            background_texts,
        )

        # The same exemption the run's own validation makes: a shape a script
        # set behind the copy on purpose - a watermark - overlaps it on
        # purpose. Counting it failed every watermarked deck (#376).
        deck = run_dir / str(manifest.get("deck") or "")
        behind = background_texts(str(deck)) if manifest.get("deck") else frozenset()
        issues = ValidateRenderedOutputTool._check_geometry(str(pdf), ignore_text=behind)
        checks.append(Check(
            "text_within_the_page", not issues,
            "; ".join(f"slide {i.get('slideIndex', 0) + 1}: {i.get('detail')}" for i in issues[:5])
            or "no overflow or overlap in the rendered geometry",
            blocking=True,
        ))

    if pages:
        content = pages[1:] or pages
        big = [(p.page_number, p.display_size_pt) for p in content
               if (p.display_size_pt or 0) > MAX_HEADLINE_PT]
        checks.append(Check(
            "headlines_within_the_ramp", not big,
            ", ".join(f"page {n}: {s:.0f}pt" for n, s in big)
            or f"every content headline at or below {MAX_HEADLINE_PT:.0f}pt",
        ))
        small = [(p.page_number, p.body_size_pt) for p in pages
                 if p.body_size_pt and p.body_size_pt < MIN_BODY_PT]
        checks.append(Check(
            "body_legible", not small,
            ", ".join(f"page {n}: {s:.1f}pt" for n, s in small)
            or f"all body copy at {MIN_BODY_PT:.0f}pt or larger",
        ))

    render_spans = [s for s in spans
                    if s["kind"] == "tool" and s["name"] == "finalize_and_render_pptx"
                    and s["status"] == "ok" and s["attrs"].get("renderPath")]
    renders = [s["attrs"]["renderPath"] for s in render_spans]
    if renders:
        # A page set on the plain fallback page is still in the design - its
        # grid, type and colours - so it passes, but it is named: it is a page
        # the design's own arrangements could not carry (#322).
        plain = render_spans[-1]["attrs"].get("plainPages") or []
        # A deck that fell out of its design language still renders and still
        # passes every geometry check, which is how the Kubernetes run shipped
        # on stock placeholders with nothing but a log line saying so (#351).
        checks.append(Check(
            "rendered_in_its_design", renders[-1] in ("composed", "template"),
            f"last render: {renders[-1]}"
            + (f"; slide(s) {', '.join(str(i + 1) for i in plain)} on the plain page"
               if plain else ""),
        ))

    validations = [s for s in spans if s["kind"] == "phase" and s["name"] == "validate"
                   and s["status"] != "skipped"]
    if validations:
        last = validations[-1]["attrs"]
        checks.append(Check(
            "validation_converged", bool(last.get("clean")),
            f"{len(validations)} round(s), {last.get('issues', '?')} issue(s) left",
        ))
        # Anything that changed the file after the last validation: a render,
        # or a script editing the rendered deck in the amend phase, which never
        # goes back through render or validate.
        validated_at = validations[-1]["startedAt"]
        changed_after = [
            s["name"] for s in spans
            if s["startedAt"] > validated_at and s["status"] == "ok" and (
                (s["kind"] == "phase" and s["name"] == "render")
                or (s["kind"] == "tool" and s["name"] == "edit_deck_with_code"))
        ]
        checks.append(Check(
            "shipped_what_was_validated", not changed_after,
            f"changed after the last validation by {', '.join(sorted(set(changed_after)))} (#299)"
            if changed_after else "the delivered file is the one validation saw",
        ))

    # "failed" is a tool that returned an error; "error" one that raised; and
    # "unfinished" one still open when the trace was written. A validator
    # turning a draft down is "rejected" - the tool working, not breaking -
    # and is counted in the metrics as draftsRejected instead (#345).
    failed = [s["name"] for s in spans if s["kind"] == "tool"
              and s["status"] in ("failed", "error", "unfinished")]
    checks.append(Check(
        "no_failed_tool_calls", not failed,
        ", ".join(f"{n} x{failed.count(n)}" for n in sorted(set(failed))) or "none failed",
    ))
    return checks


def _metrics(manifest: dict, spans: list[dict], pages: list) -> dict:
    calls = [s for s in spans if s["kind"] == "llm"]
    usage = [s["usage"] for s in calls]
    estimated = any(s["attrs"].get("tokensEstimated") for s in calls)
    return {
        "modelCalls": len(calls),
        "inputTokens": sum(u["inputTokens"] for u in usage),
        "cachedInputTokens": sum(u["cachedInputTokens"] for u in usage),
        "outputTokens": sum(u["outputTokens"] for u in usage),
        "tokensEstimated": estimated,
        "costUsd": (manifest.get("spend") or {}).get("costUsd"),
        "runSeconds": round(sum(s["durationMs"] for s in spans if s["kind"] == "run") / 1000, 1),
        "draftsRejected": sum(1 for s in spans if s["kind"] == "tool"
                              and s["status"] == "rejected"),
        "validationRounds": sum(1 for s in spans if s["kind"] == "phase" and s["name"] == "validate"
                                and s["status"] != "skipped"),
        "pages": manifest.get("pages") or 0,
        "wordsPerPage": [p.word_count for p in pages],
        "inkCoverage": [round(p.ink_coverage, 3) for p in pages],
    }


def check_run(run_dir: Path, task: Task | None = None) -> dict:
    """Check a run directory, write checks.json into it, and return what was written."""
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if task is None:
        if not manifest.get("taskFile"):
            raise ValueError(f"{run_dir} does not record its task file; pass the task explicitly")
        task = Task.load(Path(manifest["taskFile"]).expanduser())
    spans = _spans(run_dir)
    pdf = _pdf(run_dir)
    pages = []
    if pdf is not None:
        from app.measure.page_metrics import measure_pdf

        pages = measure_pdf(str(pdf))

    checks = _checks(run_dir, task, manifest, spans, pdf, pages)
    decided = [c for c in checks if c.passed is not None]
    result = {
        "task": task.id,
        "run": run_dir.name,
        "mode": manifest.get("mode"),
        "model": manifest.get("model"),
        "commit": manifest.get("commit"),
        "passed": sum(c.passed for c in decided),
        "decided": len(decided),
        "blockingFailures": [c.name for c in checks if c.blocking and c.passed is False],
        "checks": [asdict(c) for c in checks],
        "metrics": _metrics(manifest, spans, pages),
    }
    (run_dir / "checks.json").write_text(json.dumps(result, indent=2))
    return result


def render(result: dict) -> str:
    lines = [f"{result['task']} / {result['run']}: {result['passed']} of {result['decided']} checks passed"]
    for c in result["checks"]:
        mark = {True: "pass", False: "FAIL", None: "  ? "}[c["passed"]]
        lines.append(f"  {mark} {c['name']}{' (blocking)' if c['blocking'] else ''} - {c['detail']}")
    m = result["metrics"]
    cost = f"${m['costUsd']:.3f}" if m["costUsd"] is not None else "not billed"
    lines.append(f"  {m['modelCalls']} model calls, {m['inputTokens']} in ({m['cachedInputTokens']} cached)"
                 f"{' estimated' if m['tokensEstimated'] else ''}, {m['outputTokens']} out, {cost}")
    if m.get("draftsRejected"):
        lines.append(f"  {m['draftsRejected']} draft(s) rejected by validation")
    return "\n".join(lines)
