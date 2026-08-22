"""
Command line for the style-fidelity harness.

    python -m app.eval build-reference          # remeasure the reference deck
    python -m app.eval build-exemplars          # remeasure the exemplar library
    python -m app.eval score deck.pptx          # score a rendered deck
    python -m app.eval score deck.pptx --library  # ...against every exemplar
    python -m app.eval baseline                 # score the built-in fixture decks
    python -m app.eval judge deck.pptx          # look at it, and adjudicate

``baseline`` is the one that matters for the epic: run it on ``main`` before
any of the work lands, keep the scorecard, and every later run is a diff
against it rather than an assertion that things got better.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

from app.eval import corpus, reference, scorecard
from app.measure.deck_metrics import profile_pdf

DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "eval_scorecards",
)


def _score_pptx(pptx_path: str, name: str, split: str) -> scorecard.Scorecard:
    generated = _profile_pptx(pptx_path, name)
    return scorecard.score(generated, reference.load_reference_profile(split))


def _profile_pptx(pptx_path: str, name: str):
    """Render a .pptx to PDF and measure it. Shared by both score paths."""
    from app.renderer.preview_generator import PreviewGenerator

    work_dir = tempfile.mkdtemp(prefix="thinkslides_eval_")
    try:
        pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
        if not pdf_path:
            raise RuntimeError(f"LibreOffice could not convert {pptx_path}")
        return profile_pdf(pdf_path, name)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _score_pptx_library(pptx_path: str, name: str,
                        split: str) -> scorecard.LibraryResult:
    generated = _profile_pptx(pptx_path, name)
    return scorecard.score_library(generated, reference.load_exemplar_library(split))


def _judgement_markdown(call) -> str:
    verdict = call.verdict
    lines = [
        "## The eye",
        "",
        f"- **Grade:** {verdict.grade}"
        + (" (confident)" if verdict.confident else " (not confident)"),
        f"- **Verdict:** {'accepted' if call.accepted else 'not good enough'}",
        f"- **Why:** {call.reason}",
    ]
    if call.metric_is_suspect:
        lines.append(
            "- **The metric is suspect here.** The eye and the number "
            "disagree, and the number is the one that has to be fixed - "
            "optimising against it would make the deck worse while the "
            "scorecard improved."
        )
    if verdict.compared_against:
        lines.append(f"- **Compared against:** {', '.join(verdict.compared_against)}")
    if verdict.summary:
        lines += ["", verdict.summary]
    for heading, items in (("Strengths", verdict.strengths), ("Faults", verdict.faults)):
        if items:
            lines += ["", f"### {heading}", ""]
            lines += [f"- {item}" for item in items]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.eval")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-reference", help="remeasure the reference deck")
    build.add_argument("--pdf", default=None)

    build_ex = sub.add_parser("build-exemplars",
                              help="remeasure the exemplar library")
    build_ex.add_argument("--only", default=None,
                          help="one exemplar id, instead of all of them")

    score_cmd = sub.add_parser("score", help="score a rendered .pptx")
    score_cmd.add_argument("pptx")
    score_cmd.add_argument("--name", default=None)
    score_cmd.add_argument(
        "--library", action="store_true",
        help="score against every exemplar and report the nearest, rather "
             "than against the primary reference alone",
    )
    score_cmd.add_argument("--split", default=reference.VALIDATION_SPLIT,
                           choices=[reference.DESIGN_SPLIT, reference.VALIDATION_SPLIT])
    score_cmd.add_argument("--out", default=DEFAULT_OUTPUT_DIR)

    judge_cmd = sub.add_parser(
        "judge",
        help="render the deck, look at it next to the exemplars, and "
             "reconcile the verdict with the score",
    )
    judge_cmd.add_argument("pptx")
    judge_cmd.add_argument("--name", default=None)
    judge_cmd.add_argument("--split", default=reference.VALIDATION_SPLIT,
                           choices=[reference.DESIGN_SPLIT, reference.VALIDATION_SPLIT])
    judge_cmd.add_argument("--pages", type=int, default=None,
                           help="how many pages of the deck to look at")
    judge_cmd.add_argument(
        "--org", default=None,
        help="organisation UUID whose configured model should do the looking. "
             "Settings is the source of the model, same as for the agent; "
             "without this the process singleton is used instead.",
    )

    baseline = sub.add_parser("baseline", help="score the fixture decks")
    baseline.add_argument("--out", default=DEFAULT_OUTPUT_DIR)
    baseline.add_argument("--split", default=reference.VALIDATION_SPLIT,
                          choices=[reference.DESIGN_SPLIT, reference.VALIDATION_SPLIT])

    args = parser.parse_args(argv)

    if args.command == "build-reference":
        corpus.assert_split_is_disjoint()
        path = reference.build_reference_profile(args.pdf)
        print(f"wrote {path}")
        return 0

    if args.command == "build-exemplars":
        corpus.assert_exemplar_splits_are_disjoint()
        ids = [args.only] if args.only else [e.id for e in corpus.EXEMPLARS]
        for exemplar_id in ids:
            print(f"wrote {reference.build_exemplar_profile(exemplar_id)}")
        return 0

    if args.command == "score":
        name = args.name or os.path.splitext(os.path.basename(args.pptx))[0]
        if args.library:
            result = _score_pptx_library(args.pptx, name, args.split)
            print(result.to_markdown())
            if result.best:
                json_path, md_path = scorecard.write(result.best, args.out)
                print(f"wrote {json_path}\nwrote {md_path}")
            return 0
        card = _score_pptx(args.pptx, name, args.split)
        json_path, md_path = scorecard.write(card, args.out)
        print(card.to_markdown())
        print(f"wrote {json_path}\nwrote {md_path}")
        return 0

    if args.command == "judge":
        import asyncio

        from app.eval import visual_judge

        name = args.name or os.path.splitext(os.path.basename(args.pptx))[0]
        work_dir = tempfile.mkdtemp(prefix="thinkslides_judge_cli_")
        try:
            from app.renderer.preview_generator import PreviewGenerator

            pdf_path = PreviewGenerator.convert_to_pdf(args.pptx, work_dir)
            if not pdf_path:
                raise RuntimeError(f"LibreOffice could not convert {args.pptx}")
            generated = profile_pdf(pdf_path, name)
            result = scorecard.score_library(
                generated, reference.load_exemplar_library(args.split)
            )
            best = result.best
            import uuid as _uuid

            verdict = asyncio.run(visual_judge.judge_pdf(
                pdf_path,
                org_id=_uuid.UUID(args.org) if args.org else None,
                **({"max_pages": args.pages} if args.pages else {}),
            ))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        call = visual_judge.adjudicate(verdict, best.style_score if best else None)
        print(result.to_markdown())
        print(_judgement_markdown(call))
        return 0

    if args.command == "baseline":
        from app.eval.fixture_decks import render_fixture_decks

        written: list[str] = []
        for name, pptx_path, work_dir in render_fixture_decks():
            try:
                card = _score_pptx(pptx_path, name, args.split)
                json_path, md_path = scorecard.write(card, args.out)
                written.extend([json_path, md_path])
                print(f"{name}: style={card.style_score} substance={card.substance_score}")
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
        for path in written:
            print(f"wrote {path}")
        return 0

    return 1


if __name__ == "__main__":                                    # pragma: no cover
    sys.exit(main())
