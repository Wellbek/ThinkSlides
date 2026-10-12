"""
#371: three parallel points of about the same length, set alike.

On several authored languages one of three points went to a caption or
footnote slot and was set a rung smaller than the other two. Checked on every
language the evidence runs authored.
"""

from pathlib import Path

import pytest

from app.design import author_run, deck_format
from app.design import run as design_run
from app.design.schema import TextBlock
from app.research.gate_audit import authored_languages

EVIDENCE = Path(__file__).resolve().parents[3] / "benchmark" / "evidence"
FMT = deck_format.by_id(None)
LANGUAGES = [language for _, language in authored_languages(EVIDENCE)
             if not author_run._problem(language, FMT.canvas, FMT.floor_pt(FMT.canvas))]

POINTS = [
    "Adoption - teams that tried the new pipeline kept it, and most moved their main service within a quarter.",
    "Cost - the shared runners cost less than the per-team machines they replaced, once idle hours were counted.",
    "Speed - median build time fell by a third, mostly from cache hits on dependencies that rarely change.",
]
SLIDE = {"slideIndex": 0, "role": "content", "contents": [
    {"type": "text", "runs": [{"text": "The new pipeline paid for itself in its first quarter"}]},
    {"type": "text", "runs": [{"text": point, "bullet": False} for point in POINTS]},
]}


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda language: language.name)
def test_parallel_points_are_set_in_one_rung(language):
    (pack,) = author_run.packs_for([language], FMT.canvas)
    doc = design_run.compose([SLIDE], pack)
    rungs = {}
    for page in doc.pages:
        for frame in page.all_frames():
            for block in frame.content:
                if not isinstance(block, TextBlock):
                    continue
                for point in POINTS:
                    if point[:20] in block.text:
                        rungs[point[:8]] = block.type_role
    assert len(rungs) == 3, rungs
    assert len(set(rungs.values())) == 1, rungs
