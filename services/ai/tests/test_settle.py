"""
#378: short material set at its own size, the space it frees balanced round it.

On that run's own language: two sentences and a three-step flow on the
stacked arrangement kept the archetype's heights, so the flow floated in a
frame several times its height under a band of empty page.
"""


from app.design import run as design_run
from app.design.bind import _band
from app.design.repair import overflow_findings
from app.design.settle import (LEAD_GAP, LEAD_ROLE, MIN_SLACK, OPTICAL_SHARE,
                                SHORT_PROSE_SHARE)
from tests.test_replace_holds_copy import pack, slide  # noqa: F401 - fixtures

STACKED = "claim-evidence-stacked"


def _composed(language, material, archetype):
    others = frozenset(a.id for a in language.archetypes if a.id != archetype)
    doc = design_run.compose([material], language, avoid={0: others})
    (page,) = doc.pages
    assert page.archetype == archetype
    return doc, page


def test_a_short_page_is_cut_to_its_material_and_set_at_the_optical_centre(pack, slide):  # noqa: F811
    doc, page = _composed(pack, slide, STACKED)
    archetype = pack.archetype(STACKED)
    frames = {f.id: f for f in page.frames if _band(f)}
    stated = {f.id: f for f in archetype.frames if _band(f)}
    heading, copy, figure = sorted(frames, key=lambda i: _band(frames[i])[0])

    # The heading stays where the archetype puts it.
    assert _band(frames[heading]) == _band(stated[heading])
    # The flow's frame is cut to the flow, not left at the archetype's height.
    assert frames[figure].place.h < stated[figure].place.h * 0.6
    # What is freed sits above the group as well as below it, more below.
    above = _band(frames[copy])[0] - _band(frames[heading])[1]
    below = max(_band(stated[f])[1] for f in stated) - _band(frames[figure])[1]
    stated_above = _band(stated[copy])[0] - _band(stated[heading])[1]
    assert above > stated_above
    assert below > above
    assert not overflow_findings(doc, page, pack)


def test_a_page_with_nothing_to_free_is_left_as_its_archetype_states(pack, slide):  # noqa: F811
    chart = {"type": "chart", "chart": {"chartType": "bar", "title": "Hits (%)",
                                        "categories": ["a", "b"],
                                        "series": [{"name": "Hits", "values": [1, 2]}]}}
    filled = {**slide, "contents": [*slide["contents"][:2], chart]}
    _, page = _composed(pack, filled, STACKED)
    stated = {f.id: _band(f) for f in pack.archetype(STACKED).frames if _band(f)}

    # A chart fills its frame and has no height of its own, so its row keeps
    # the archetype's; the copy's row is all that could be freed, and that
    # alone is under the threshold or moves the copy only.
    figure = next(f for f in page.frames if f.role == "figure")
    assert _band(figure) == stated[figure.id]


def test_the_parameters_are_stated():
    assert 0 < MIN_SLACK < 0.25
    assert 0 < OPTICAL_SHARE < 0.5


def test_a_short_exhibit_beside_copy_is_centred_on_it(pack, slide):  # noqa: F811
    _, page = _composed(pack, slide, "claim-evidence")
    copy = next(f for f in page.frames if f.content and f.role != "figure"
                and _band(f) and _band(f)[0] > 0.2)
    figure = next(f for f in page.frames if f.role == "figure")
    copy_middle = sum(_band(copy)) / 2
    figure_middle = sum(_band(figure)) / 2
    assert abs(copy_middle - figure_middle) < 0.02


def _prose_only(slide, sentences):  # noqa: F811
    heading = slide["contents"][0]
    return {**slide, "contents": [heading, {"type": "text", "runs": [
        {"text": s, "bullet": False} for s in sentences]}]}


SHORT = ["Every runner read from and wrote to the same shared disk.",
         "When one job saturated that disk, unrelated builds queued behind it.",
         "A full or slow volume failed builds that had nothing else in common."]


def test_short_prose_is_set_as_the_headings_lead_not_floated(pack, slide):  # noqa: F811
    """#392: three sentences under a heading were centred in the space below
    it, an island apart from their heading. Short prose alone is set a rung
    up, LEAD_GAP under the heading."""
    doc = design_run.compose([_prose_only(slide, SHORT)], pack)
    (page,) = doc.pages
    heading = next(f for f in page.frames if f.id == page.provenance.heading_frame)
    copy = next(f for f in page.frames if f.content and f.id != heading.id)
    assert {block.type_role for block in copy.content} == {LEAD_ROLE}
    gap = _band(copy)[0] - _band(heading)[1]
    assert gap <= LEAD_GAP + 1e-6
    assert not overflow_findings(doc, page, pack)


def test_prose_that_fills_its_page_keeps_the_body_rung(pack, slide):  # noqa: F811
    doc = design_run.compose([_prose_only(slide, SHORT * 4)], pack)
    rungs = {block.type_role for page in doc.pages for f in page.frames
             if f.id != page.provenance.heading_frame for block in f.content
             if hasattr(block, "type_role")}
    assert LEAD_ROLE not in rungs


def test_prose_beside_an_exhibit_is_not_a_lead(pack, slide):  # noqa: F811
    _, page = _composed(pack, slide, STACKED)
    rungs = {block.type_role for f in page.frames for block in f.content
             if hasattr(block, "type_role")}
    assert LEAD_ROLE not in rungs


def test_the_lead_parameters_are_stated():
    assert 0 < LEAD_GAP < 0.25
    assert 0 < SHORT_PROSE_SHARE <= 0.5
