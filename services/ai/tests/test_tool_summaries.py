"""
Tests for the plain-language step summaries shown in the activity feed.

The feed used to lead with the tool's raw JSON result, which buried the one
interesting sentence in a hundred lines of braces. These check that the
sentence is actually informative - it names what changed and by how much - and
that a tool returning an unexpected shape degrades to a shorter sentence rather
than raising into the agent loop.
"""

import pytest

from app.agent.summaries import summarize_tool_result


def _summary(tool: str, data, args: dict | None = None, success: bool = True) -> str:
    return summarize_tool_result(tool, args or {}, data, success)


class TestResearchAndAnalysis:
    def test_a_document_is_described_by_name_and_size(self):
        summary = _summary(
            "analyze_document",
            {"content_length": 12400, "images": [{"id": "1"}, {"id": "2"}]},
            {"document_path": "/uploads/pitch deck.pdf"},
        )

        assert summary == "Read pitch deck.pdf - 12,400 characters of text and 2 embedded images."

    def test_truncation_is_disclosed(self):
        summary = _summary(
            "analyze_document",
            {"content_length": 900000, "truncated": True},
            {"document_path": "/uploads/report.docx"},
        )

        assert "truncated to fit" in summary

    def test_key_data_counts_what_it_found(self):
        summary = _summary(
            "extract_key_data",
            {"metrics": [1, 2, 3], "key_points": ["a"], "topics": [], "dates": [{}, {}]},
        )

        assert summary == "Pulled out 3 metrics, 1 key point and 2 dates."

    def test_a_search_names_the_sites_it_came_back_with(self):
        summary = _summary("web_search", {
            "query": "global coffee market size 2026",
            "results": [
                {"url": "https://www.statista.com/a", "title": "A"},
                {"url": "https://statista.com/b", "title": "B"},
                {"url": "https://ico.org/c", "title": "C"},
            ],
        })

        assert summary == (
            'Searched for "global coffee market size 2026" - 3 results '
            "from statista.com and ico.org."
        )

    def test_a_search_that_found_nothing_says_so(self):
        summary = _summary("web_search", {"query": "nothing at all", "results": []})

        assert summary == 'Searched for "nothing at all" - no results.'


class TestPlanningAndDrafting:
    def test_a_plan_lists_the_slides_it_planned(self):
        summary = _summary("plan_presentation_structure", {
            "totalSlides": 3,
            "slides": [
                {"title": "Cover"},
                {"title": "The market today"},
                {"title": "Outlook"},
            ],
        })

        assert summary == "Planned 3 slides: Cover, The market today, Outlook."

    def test_a_long_plan_is_abbreviated(self):
        summary = _summary("plan_presentation_structure", {
            "totalSlides": 8,
            "slides": [{"title": f"Slide {i}"} for i in range(8)],
        })

        assert summary.endswith("+3 more.")

    def test_a_layout_choice_names_the_slide_and_the_layout(self):
        summary = _summary(
            "select_layout",
            {"layoutName": "Title and Content", "placeholders": [{}, {}]},
            {"slide_index": 2},
        )

        assert summary == 'Slide 3 will use the "Title and Content" layout (2 placeholders).'

    def test_a_designed_slide_reports_what_is_on_it(self):
        """The real shape: the renderer's placeholders, with the title as just
        another text item and the copy carried as runs."""
        summary = _summary(
            "design_slide_content",
            {
                "slideIndex": 1,
                "contents": [
                    {"type": "text", "placeholderIdx": 0, "runs": [{"text": "The market today"}]},
                    {"type": "text", "placeholderIdx": 1, "runs": [
                        {"text": "one", "bullet": True},
                        {"text": "two", "bullet": True},
                        {"text": "three", "bullet": True},
                    ]},
                    {"type": "chart"},
                ],
            },
            {"slide_outline": '{"slideIndex": 1, "title": "The market today"}'},
        )

        # The title placeholder is named, not counted again as body copy.
        assert summary == 'Slide 2, "The market today": 3 bullets and 1 chart.'

    def test_unbulleted_copy_is_counted_as_lines(self):
        summary = _summary(
            "design_slide_content",
            {
                "slideIndex": 0,
                "contents": [
                    {"type": "text", "placeholderIdx": 1, "runs": [
                        {"text": "A cover subtitle"},
                        {"text": "and a second line"},
                    ]},
                ],
            },
            {"slide_outline": '{"title": "Cover"}'},
        )

        assert summary == 'Slide 1, "Cover": 2 lines of copy.'

    def test_a_built_visual_reuses_the_tool_own_summary(self):
        summary = _summary("build_chart", {
            "slideIndex": 2,
            "summary": "bar chart with 2 series over 5 categories, attached to slide 3",
        })

        assert summary == (
            "Bar chart with 2 series over 5 categories, attached to slide 3."
        )


class TestValidationAndOutput:
    def test_a_clean_render_check_says_what_was_checked(self):
        summary = _summary("validate_rendered_output", {"valid": True, "issues": []})

        assert "nothing overflows" in summary

    def test_problems_are_named_not_counted_alone(self):
        summary = _summary("validate_rendered_output", {
            "issues": [
                {"slideIndex": 3, "type": "overflow", "detail": "body text runs past its placeholder"},
                {"slideIndex": 5, "type": "overlap", "detail": "chart covers the title"},
            ],
        })

        assert summary == (
            "Found 2 problems in the rendered file - Slide 4: body text runs past "
            "its placeholder, and others."
        )

    def test_a_rendered_file_is_described_by_size_and_slide_count(self):
        summary = _summary("finalize_and_render_pptx", {
            "filePath": "/out/deck.pptx",
            "fileName": "Coffee.pptx",
            "slideCount": 8,
            "fileSizeBytes": 2_400_000,
        })

        assert summary == "Rendered Coffee.pptx - 8 slides, 2.4 MB."

    def test_a_fallback_render_is_not_reported_as_a_deck(self):
        summary = _summary("finalize_and_render_pptx", {"fallbackMarkdown": "# deck"})

        assert "markdown fallback" in summary

    def test_a_schema_rejection_leads_with_the_first_error(self):
        summary = _summary("validate_slide_json", {
            "errors": ["Missing required field: layoutId"],
            "warnings": [],
        })

        assert "rejected - 1 error" in summary
        assert "layoutId" in summary

    def test_validation_is_labelled_by_slide_like_every_other_per_slide_tool(self):
        """Ten validation calls in a row were indistinguishable from each
        other - _design_slide and _select_image both name the slide they
        acted on, this did not."""
        summary = _summary("validate_slide_json", {
            "slideIndex": 3,
            "errors": ["Missing required field: layoutId"],
            "warnings": [],
        })

        assert summary.startswith("Slide 4: rejected")


class TestConsultation:
    def test_several_questions_are_listed(self):
        summary = _summary("ask_user_question", {
            "questions": [
                {"question": "Who is the audience?"},
                {"question": "How many slides?"},
            ],
        })

        assert summary == "Asked the user 2 questions: Who is the audience? and How many slides?"

    def test_one_question_is_quoted(self):
        summary = _summary("ask_user_question", {
            "questions": [{"question": "Which colour scheme?"}],
        })

        assert summary == 'Asked: "Which colour scheme?"'


class TestDegradesQuietly:
    @pytest.mark.parametrize(
        "data",
        [None, "a string", [], {}, {"slides": "not a list"}],
    )
    def test_an_unexpected_shape_never_raises(self, data):
        assert isinstance(_summary("plan_presentation_structure", data), str)

    def test_a_failed_call_has_no_summary(self):
        """The feed shows the error itself, in full."""
        assert _summary("web_search", {"results": []}, success=False) == ""

    def test_an_unknown_tool_has_no_summary(self):
        assert _summary("some_future_tool", {"anything": 1}) == ""
