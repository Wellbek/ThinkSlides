"""
The gate statistic `docs/DESIGN_DECISIONS.md` quotes, reproduced from the
committed evidence. If the gate changes, this fails, and the document has to
change with it - which is the point: the record is meant to be cited.
"""

from pathlib import Path

from app.design import author_run, deck_format
from app.research.gate_audit import authored_languages

EVIDENCE = Path(__file__).resolve().parents[3] / "benchmark" / "evidence"


def test_the_recorded_languages_and_what_the_gate_refuses():
    fmt = deck_format.by_id(None)
    verdicts = [author_run._problem(language, fmt.canvas, fmt.floor_pt(fmt.canvas))
                for _, language in authored_languages(EVIDENCE)]
    refused = [why for why in verdicts if why]
    assert (len(verdicts), len(refused)) == (17, 2)
    assert all(why.startswith("no content page gives a table room") for why in refused)
