"""
Adding a slide to a deck that already exists must not destroy one.

`add_slide` is replace-or-append keyed on `slideIndex`, which is right when the
model is redesigning a slide it got wrong and wrong when the user asks for a
new one. "Add a slide about X after slide 2" designed a slide at index 2 and
overwrote whatever already held that index; nothing renumbered, so the deck
silently lost a page.

`slideIndex` is not a label - `ordered_slides` sorts by it, and the renderer,
the markdown fallback and the validator all read that order - so a tail that
keeps its old numbers is a deck with two slide 2s. These tests pin the
renumbering, and pin that everything else keyed by slide index moves with it.
A layout or a chart left behind does not fail loudly; it silently binds slide
3's chart to slide 4, which is worse than an error.
"""

from app.agent.loop import WorkingState


def _slide(index: int, title: str) -> dict:
    return {"slideIndex": index, "title": title, "contents": []}


def _deck(*titles: str) -> WorkingState:
    state = WorkingState()
    for index, title in enumerate(titles):
        state.add_slide(_slide(index, title))
    return state


def _titles(state: WorkingState) -> list[str]:
    return [slide["title"] for slide in state.ordered_slides()]


def _indices(state: WorkingState) -> list[int]:
    return [slide["slideIndex"] for slide in state.ordered_slides()]


def test_inserting_pushes_the_tail_down_instead_of_overwriting():
    state = _deck("Cover", "Context", "Findings")

    state.insert_slide(_slide(2, "Regional breakdown"))

    assert _titles(state) == ["Cover", "Context", "Regional breakdown", "Findings"]
    assert _indices(state) == [0, 1, 2, 3]


def test_redesigning_still_replaces_in_place():
    """The existing contract is untouched - insert is the new behaviour, not
    a change to how a slide gets rewritten."""
    state = _deck("Cover", "Context", "Findings")

    state.add_slide(_slide(1, "Context, rewritten"))

    assert _titles(state) == ["Cover", "Context, rewritten", "Findings"]
    assert len(state.accumulated_slides) == 3


def test_inserting_at_the_front_renumbers_the_whole_deck():
    state = _deck("Context", "Findings")

    state.insert_slide(_slide(0, "Cover"))

    assert _titles(state) == ["Cover", "Context", "Findings"]
    assert _indices(state) == [0, 1, 2]


def test_inserting_past_the_end_simply_appends():
    state = _deck("Cover", "Context")

    state.insert_slide(_slide(2, "Findings"))

    assert _titles(state) == ["Cover", "Context", "Findings"]
    assert _indices(state) == [0, 1, 2]


def test_inserting_moves_the_layout_and_visual_stores_with_the_deck():
    state = _deck("Cover", "Context", "Findings")
    state.selected_layouts = {0: "cover", 1: "body", 2: "body"}
    state.selected_images = {2: {"source": "url", "data": "findings.png"}}
    state.prepared_visuals = {2: [{"type": "chart"}]}
    state.prepared_regions = {2: [{"id": "left"}]}
    state.prepared_primitives = {2: [{"kind": "rule"}]}

    state.insert_slide(_slide(2, "Regional breakdown"))

    # "Findings" moved from 2 to 3, and so did everything bound to it.
    assert state.selected_layouts == {0: "cover", 1: "body", 3: "body"}
    assert state.selected_images == {3: {"source": "url", "data": "findings.png"}}
    assert state.prepared_visuals == {3: [{"type": "chart"}]}
    assert state.prepared_regions == {3: [{"id": "left"}]}
    assert state.prepared_primitives == {3: [{"kind": "rule"}]}


def test_inserting_marks_the_rendered_file_stale():
    """Any write invalidates the render - see render_is_current. An insert that
    did not would leave the user downloading a deck without the slide they
    just asked for."""
    state = _deck("Cover", "Context")
    before = state.design_serial

    state.insert_slide(_slide(1, "New"))

    assert state.design_serial > before


def test_removing_closes_the_gap():
    state = _deck("Cover", "Context", "Findings")

    assert state.remove_slide(1) is True

    assert _titles(state) == ["Cover", "Findings"]
    assert _indices(state) == [0, 1]


def test_removing_moves_the_stores_down_and_drops_the_slides_own():
    state = _deck("Cover", "Context", "Findings")
    state.selected_layouts = {0: "cover", 1: "body", 2: "body"}
    state.prepared_visuals = {1: [{"type": "table"}], 2: [{"type": "chart"}]}

    state.remove_slide(1)

    assert state.selected_layouts == {0: "cover", 1: "body"}
    # Slide 1's own table goes with slide 1; slide 2's chart follows it to 1.
    assert state.prepared_visuals == {1: [{"type": "chart"}]}


def test_removing_a_slide_that_is_not_there_changes_nothing():
    state = _deck("Cover", "Context")
    before = state.design_serial

    assert state.remove_slide(7) is False

    assert _titles(state) == ["Cover", "Context"]
    assert state.design_serial == before


def test_a_slide_with_no_index_is_appended_rather_than_dropped():
    state = _deck("Cover")

    state.insert_slide({"title": "Stray", "contents": []})

    assert len(state.accumulated_slides) == 2


def test_insert_then_remove_returns_the_deck_to_where_it_started():
    state = _deck("Cover", "Context", "Findings")

    state.insert_slide(_slide(1, "Interlude"))
    state.remove_slide(1)

    assert _titles(state) == ["Cover", "Context", "Findings"]
    assert _indices(state) == [0, 1, 2]


def test_resolve_and_add_slide_inserts_when_asked():
    """The path the agent actually takes: design_slide_content's result goes
    through resolve_and_add_slide, and `placement="insert"` is what the loop
    turns into this flag."""
    state = _deck("Cover", "Context", "Findings")

    state.resolve_and_add_slide(_slide(1, "Inserted"), insert=True)

    assert _titles(state) == ["Cover", "Inserted", "Context", "Findings"]


def test_resolve_and_add_slide_replaces_by_default():
    state = _deck("Cover", "Context", "Findings")

    state.resolve_and_add_slide(_slide(1, "Rewritten"))

    assert _titles(state) == ["Cover", "Rewritten", "Findings"]
