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
    assert (len(verdicts), len(refused)) == (28, 5)
    assert all(why.startswith("no content page gives a table room") for why in refused)


def test_a_derived_arrangement_that_does_not_fit_is_dropped_not_the_language():
    """The #376 probes: 'Ruled Report' authored a `figures` page of a heading,
    two statistics and a caption side by side. Its derived stacked variant ran
    the caption off the bottom of the page, and the gate refused the whole
    language for a page it never wrote."""
    from app.design.authored import to_pack
    from app.design.vocabulary import ensure

    fmt = deck_format.by_id(None)
    ruled = [language for where, language in authored_languages(EVIDENCE)
             if language.name == "Ruled Report"
             and "probe-footer-not-cover" in str(where)]
    assert ruled
    pack = ensure(to_pack(ruled[0], fmt.canvas, floor_pt=fmt.floor_pt(fmt.canvas)))
    ids = {a.id for a in pack.archetypes}
    assert any(i.endswith("figures") for i in ids)
    assert not any(i.endswith("figures-stacked") for i in ids)
    assert not author_run._problem(ruled[0], fmt.canvas, fmt.floor_pt(fmt.canvas))
