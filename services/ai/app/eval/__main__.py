"""
Command line for the style-fidelity harness.

    python -m app.eval build-reference          # remeasure the reference deck
    python -m app.eval score deck.pptx          # score a rendered deck
    python -m app.eval baseline                 # score the built-in fixture decks

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
from app.eval.deck_metrics import profile_pdf

DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "eval_scorecards",
)


def _score_pptx(pptx_path: str, name: str, split: str) -> scorecard.Scorecard:
    from app.renderer.preview_generator import PreviewGenerator

    work_dir = tempfile.mkdtemp(prefix="thinkslides_eval_")
    try:
        pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
        if not pdf_path:
            raise RuntimeError(f"LibreOffice could not convert {pptx_path}")
        generated = profile_pdf(pdf_path, name)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return scorecard.score(generated, reference.load_reference_profile(split))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.eval")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-reference", help="remeasure the reference deck")
    build.add_argument("--pdf", default=None)

    score_cmd = sub.add_parser("score", help="score a rendered .pptx")
    score_cmd.add_argument("pptx")
    score_cmd.add_argument("--name", default=None)
    score_cmd.add_argument("--split", default=reference.VALIDATION_SPLIT,
                           choices=[reference.DESIGN_SPLIT, reference.VALIDATION_SPLIT])
    score_cmd.add_argument("--out", default=DEFAULT_OUTPUT_DIR)

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

    if args.command == "score":
        name = args.name or os.path.splitext(os.path.basename(args.pptx))[0]
        card = _score_pptx(args.pptx, name, args.split)
        json_path, md_path = scorecard.write(card, args.out)
        print(card.to_markdown())
        print(f"wrote {json_path}\nwrote {md_path}")
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
