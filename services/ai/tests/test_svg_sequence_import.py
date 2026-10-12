"""
#350: a Mermaid sequence diagram imported without its arrows, with a black
mark in its corner, its notes unboxed, and shrunk until it could not be read.

Read from the recorded output in fixtures/mermaid/sequence.svg, so it runs
with no browser.
"""

import math
import pathlib

from app.renderer.primitives import PositionConfig
from app.renderer.svg.importer import _colour, legible_height_in, to_primitives

SEQUENCE = (pathlib.Path(__file__).parent / "fixtures" / "mermaid"
            / "sequence.svg").read_text()
PALETTE = {"accent": "#1E3A5F", "ink": "#1B2430", "ground": "#F7F5F0"}


def _paths():
    return [p for p in to_primitives(SEQUENCE, PositionConfig(left=0, top=0, width=4, height=4),
                                     palette=PALETTE) if p.kind == "path"]


def test_a_line_coloured_by_its_class_is_drawn_although_its_attribute_says_none():
    # A stylesheet rule outranks a presentation attribute, as in CSS.
    messages = [p for p in _paths() if p.stroke and len(p.subpaths[0]) == 2
                and p.subpaths[0][0][1] == p.subpaths[0][1][1]]
    assert len(messages) >= 4


def test_every_message_ends_in_an_arrowhead_and_no_definition_is_drawn():
    paths = _paths()
    assert sum("head" in p.id for p in paths) == 4
    # The marker definitions were drawn as unpainted, therefore black,
    # shapes at the diagram's origin.
    assert not [p for p in paths if p.fill == "#000000" and not p.stroke
                and "head" not in p.id]


def test_a_note_keeps_the_border_mermaid_gives_it_in_hsl():
    assert _colour("hsl(52.68, 60%, 73.92%)") == "#E4DB95"
    assert _colour("hsl(120deg 100% 25%)") == "#008000"
    boxed = [p for p in _paths() if p.stroke and p.closed and p.closed[0]
             and p.fill and p.fill != "#1E3A5F"]
    assert boxed, "the note is drawn with a border"


def test_a_slot_must_be_tall_enough_for_the_smallest_label_at_the_floor():
    # 14px labels in a 398-unit-high document reach 10pt at about 3.45in.
    assert math.isclose(legible_height_in(SEQUENCE, 12.0, 10.0), 3.45, abs_tol=0.01)
    # At that height the document is wider than a narrow column, so no
    # height of that column reads.
    assert math.isinf(legible_height_in(SEQUENCE, 3.0, 10.0))


def test_the_selector_will_not_set_it_in_a_short_band():
    from app.design.material import MaterialItem
    from app.design.select import exhibit_floor_in

    item = MaterialItem(id="d", kind="diagram",
                        payload={"type": "diagram",
                                 "diagram": {"format": "svg", "svg": SEQUENCE}})
    # The pack is not read for an imported diagram: its floor is its own.
    assert exhibit_floor_in(item, None, 12.0) > 3.4
