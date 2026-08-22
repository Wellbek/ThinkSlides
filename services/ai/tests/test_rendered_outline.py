"""
Every page of the rendered file has to be accounted for, not just the pages
the agent designed.

`finalize_and_render_pptx` appends a Sources appendix after the model has
finished designing, and `PreviewGenerator` rasterises every page of the
result. The agent's own record of the deck stops at the designed slides, so
those trailing pages arrived at the UI as a preview image with nothing to
attach it to - and the UI dropped them. The deck downloaded with N+1 pages
and previewed with N.

`renderedOutline` is the fix: one entry per page of the file that was actually
written, carrying the headline needed to label a page that has no design
result behind it, and a flag saying it was injected rather than authored.
"""

from app.agent.tools.finalize_and_render_pptx import FinalizeAndRenderPptxTool


def _slide(index: int, title: str) -> dict:
    return {
        "slideIndex": index,
        "contents": [
            {"type": "text", "placeholderIdx": 0, "runs": [{"text": title}]},
            {"type": "text", "placeholderIdx": 1, "runs": [{"text": "body"}]},
        ],
    }


def _outline(slides: list[dict], designed_count: int) -> list[dict]:
    return FinalizeAndRenderPptxTool._rendered_outline(slides, designed_count)


def test_an_outline_covers_every_page_that_was_rendered():
    slides = [_slide(0, "The 2026 Picture"), _slide(1, "Why It Matters")]

    outline = _outline(slides, designed_count=2)

    assert [entry["slideIndex"] for entry in outline] == [0, 1]
    assert [entry["title"] for entry in outline] == ["The 2026 Picture", "Why It Matters"]


def test_a_page_the_renderer_appended_is_marked_injected():
    slides = [_slide(0, "The 2026 Picture"), _slide(1, "Sources")]

    outline = _outline(slides, designed_count=1)

    assert outline[0]["injected"] is False
    assert outline[1]["injected"] is True
    # The appendix still gets a real headline, which is the whole reason the
    # UI can label a page it has no design record for.
    assert outline[1]["title"] == "Sources"


def test_several_appended_pages_are_all_marked():
    slides = [_slide(i, f"Slide {i}") for i in range(2)] + [
        _slide(2, "Sources"),
        _slide(3, "Sources (continued)"),
    ]

    outline = _outline(slides, designed_count=2)

    assert [entry["injected"] for entry in outline] == [False, False, True, True]


def test_the_outline_carries_no_slide_bodies():
    """
    Deliberately just index, headline and provenance.

    This rides back inside the tool result, which is one of the payloads that
    reaches the model. Shipping whole slide bodies here would put a second copy
    of the deck - image bytes included - into the conversation, which is the
    exact failure #237 was filed for.
    """
    slides = [
        {
            "slideIndex": 0,
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Chart page"}]},
                {"type": "image", "image": {"source": "generated", "data": "A" * 4096}},
            ],
        }
    ]

    outline = _outline(slides, designed_count=1)

    assert set(outline[0]) == {"slideIndex", "title", "injected"}
    assert "A" * 64 not in repr(outline)


def test_a_page_with_no_headline_still_gets_an_entry():
    """A page must never be silently dropped just because it has no title."""
    slides = [{"slideIndex": 0, "contents": []}]

    outline = _outline(slides, designed_count=1)

    assert len(outline) == 1
    assert outline[0]["title"] == ""


def test_position_stands_in_for_a_missing_slide_index():
    """
    Ordering comes from the rendered list, not from a field a slide may not
    carry. Preview page N is the Nth page of the file either way, so an entry
    that fell back to a missing slideIndex would point the UI at the wrong
    page.
    """
    slides = [{"contents": []}, {"contents": []}, {"slideIndex": 9, "contents": []}]

    outline = _outline(slides, designed_count=3)

    assert [entry["slideIndex"] for entry in outline] == [0, 1, 9]


def test_an_empty_deck_produces_an_empty_outline():
    assert _outline([], designed_count=0) == []
