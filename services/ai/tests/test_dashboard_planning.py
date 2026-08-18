"""
The planner can ask for a dashboard, and has to say what each exhibit is for.

The archetype is only safe because it is gated. Half that gate lives in
``budget`` and reads material and exhibit count at design time. The other half
is here, and it is the earlier and cheaper one: a dashboard is a promise that
this page answers several *different* questions about one claim, and the
exhibit plan is where the promise is checked - at the point the page is
specified, rather than at render time when the only remaining option is to
redesign it.

The failure this prevents is not "no dashboards". It is four exhibits saying
the same thing, and exhibits built from figures the material does not have.
"""

from app import budget
from app.agent.tools.plan_presentation_structure import (
    MAX_EXHIBIT_QUESTIONS, demote_unearned_dashboards,
)

REAL_PLAN = [
    "What are the headline figures?",
    "Where did the movement come from?",
    "What changed underneath the flat total?",
    "Does it hold across regions?",
]


def _slide(**kwargs):
    return {"slideIndex": 3, "title": "Revenue held flat but the mix moved",
            "contentType": "dashboard", **kwargs}


class TestAPlannedDashboardReachesTheBudget:
    def test_the_content_type_maps_to_the_archetype(self):
        assert budget.archetype_for(
            {"contentType": "dashboard"}
        ) == budget.DASHBOARD

    def test_a_real_plan_survives(self):
        slides = [_slide(exhibitPlan=REAL_PLAN)]

        problems = demote_unearned_dashboards(slides)

        assert problems == []
        assert slides[0]["contentType"] == "dashboard"
        assert slides[0]["exhibitPlan"] == REAL_PLAN


class TestAnUnearnedDashboardIsDemoted:
    def test_no_exhibit_plan_at_all(self):
        """Four exhibits asked for, nothing said about what they are for."""
        slides = [_slide()]

        problems = demote_unearned_dashboards(slides)

        assert slides[0]["contentType"] == "chart"
        assert "at least 2" in problems[0]

    def test_one_question_is_an_exhibit_slide(self):
        slides = [_slide(exhibitPlan=["Where did the movement come from?"])]

        demote_unearned_dashboards(slides)

        assert slides[0]["contentType"] == "chart"

    def test_the_same_question_twice(self):
        """Two exhibits saying one thing read worse than one saying it once."""
        slides = [_slide(exhibitPlan=[
            "Where did the movement come from?",
            "where did the movement come from?",
            "What changed underneath?",
        ])]

        problems = demote_unearned_dashboards(slides)

        assert slides[0]["contentType"] == "chart"
        assert "the same" in problems[0]

    def test_too_many_questions(self):
        slides = [_slide(exhibitPlan=[f"Question {n}?" for n in range(7)])]

        problems = demote_unearned_dashboards(slides)

        assert slides[0]["contentType"] == "chart"
        assert str(MAX_EXHIBIT_QUESTIONS) in problems[0]

    def test_the_stale_plan_is_removed_with_the_archetype(self):
        """A chart slide carrying a four-exhibit plan would be read as one."""
        slides = [_slide(exhibitPlan=["Only one question?"])]

        demote_unearned_dashboards(slides)

        assert "exhibitPlan" not in slides[0]

    def test_the_reason_says_not_to_invent_more_questions(self):
        """
        The obvious way to satisfy this check is to make questions up, which
        is exactly the outcome the whole gate exists to prevent.
        """
        problems = demote_unearned_dashboards([_slide()])

        assert "Do not re-plan it as a dashboard by inventing more" in problems[0]


class TestEverythingElseIsLeftAlone:
    def test_other_slides_are_untouched(self):
        slides = [
            {"slideIndex": 0, "contentType": "title"},
            {"slideIndex": 1, "contentType": "chart"},
            {"slideIndex": 2, "contentType": "content",
             "exhibitPlan": ["stray", "field"]},
        ]

        assert demote_unearned_dashboards(slides) == []
        assert [s["contentType"] for s in slides] == ["title", "chart", "content"]

    def test_a_malformed_entry_does_not_break_the_plan(self):
        assert demote_unearned_dashboards([None, "x", {}]) == []

    def test_the_whole_plan_is_not_rejected_over_one_slide(self):
        """Failing fifteen good slides over one is not a fix."""
        slides = [
            {"slideIndex": 0, "contentType": "content"},
            _slide(slideIndex=1),
            {"slideIndex": 2, "contentType": "chart"},
        ]

        demote_unearned_dashboards(slides)

        assert len(slides) == 3
        assert slides[0]["contentType"] == "content"
