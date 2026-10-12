"""
#388: a pull quote is offered, not owed.

On the HTTP caching run's own language, a heading and six points of about 150
characters was split in two: the pull quote drawn from the body took the
prose column, the body had nowhere to go, and every arrangement was refused.
"""

from app.design import run as design_run
from app.design.select import fit
from tests.test_replace_holds_copy import pack  # noqa: F401 - fixture

POINT = ("a cache that stores a response must decide on every later request "
         "whether that copy is still fresh enough to serve without asking the origin again.")


def _slide(points: int) -> dict:
    return {"slideIndex": 0, "role": "content", "contents": [
        {"type": "text", "runs": [{"text": "What every cache decides on each request"}]},
        {"type": "text", "runs": [{"text": f"Point {i}: {POINT}", "bullet": False}
                                  for i in range(points)]},
    ]}


def test_a_page_with_room_in_its_column_is_not_split_for_its_quote(pack):  # noqa: F811
    shape = design_run.material_from(_slide(6), index=0, total=1)
    assert shape.quote, "the body is long enough to offer a quote"

    selection = fit(shape, pack.archetype("claim-evidence"), pack)

    assert selection is not None
    assert not selection.unplaced
    assert len(design_run.compose([_slide(6)], pack).pages) == 1


def test_the_quote_is_kept_where_it_has_a_slot_of_its_own(pack):  # noqa: F811
    shape = design_run.material_from(_slide(6), index=0, total=1)
    for archetype in pack.archetypes:
        selection = fit(shape, archetype, pack)
        if selection is not None and any(p.item.kind == "quote" for p in selection.placements):
            # Wherever the quote is placed, nothing else lost its slot to it.
            assert not selection.unplaced
