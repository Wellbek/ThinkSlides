"""
Whether a deck's headlines add up to an argument.

`app/titles.py` decides what register one headline is in. Nothing looked at
them together, so a deck could pass every per-page check and still be "Market
Analysis Presentation" followed by "Key Aspects of the Market" - which is the
property `titles.py` has always stated and nothing enforced: *a reader who
reads only the headlines has read the argument*.

What is checked is the **shape** of an argument, not its truth. Whether a deck
is right is not something code decides, and a number pretending otherwise is a
number people would quote.
"""
import pytest

from app import narrative

ARGUED = [
    {"title": "Insurance risk in 2026", "contentType": "title"},
    {"title": "Cyber is now the most cited near-term risk for insurers",
     "contentType": "content"},
    {"title": "Cyber exposure has outgrown most insurers response programmes",
     "contentType": "content"},
    {"title": "Regulators now demand evidence of controls, not statements of intent",
     "contentType": "content"},
    {"title": "Insurers that treat regulation as evidence work will absorb the shift",
     "contentType": "conclusion"},
]

TABLE_OF_CONTENTS = [
    {"title": "Market Analysis Presentation", "contentType": "title"},
    {"title": "Key Aspects of the Market", "contentType": "content"},
    {"title": "Overview of Risks", "contentType": "content"},
    {"title": "Conclusion", "contentType": "conclusion"},
]

UNRELATED = [
    {"title": "A deck", "contentType": "title"},
    {"title": "The history of the Roman aqueduct system", "contentType": "content"},
    {"title": "Why sourdough starters fail in winter", "contentType": "content"},
    {"title": "Quarterly returns in emerging market debt", "contentType": "conclusion"},
]


class TestItSeparatesAnArgumentFromAListOfTopics:
    def test_a_deck_that_argues_passes(self):
        assert narrative.read_arc(ARGUED).argues

    def test_a_table_of_contents_does_not(self):
        report = narrative.read_arc(TABLE_OF_CONTENTS)
        assert not report.argues
        assert "claims" in {b.property for b in report.breaks}

    def test_unrelated_pages_are_caught(self):
        report = narrative.read_arc(UNRELATED)
        assert "continuity" in {b.property for b in report.breaks}

    def test_the_check_earns_its_place(self):
        """Every property this reports has to rank the argued deck above one
        of the others, or it is measuring nothing. The discipline the
        composition metrics are held to, applied here."""
        argued = narrative.read_arc(ARGUED)
        for other in (TABLE_OF_CONTENTS, UNRELATED):
            assert len(argued.breaks) < len(narrative.read_arc(other).breaks)


class TestEachBreakNamesAPageAndAFix:
    def test_a_break_names_the_page(self):
        for brk in narrative.read_arc(TABLE_OF_CONTENTS).breaks:
            assert 0 <= brk.slide_index < len(TABLE_OF_CONTENTS)

    def test_a_break_says_what_was_expected(self):
        for brk in narrative.read_arc(TABLE_OF_CONTENTS).breaks:
            assert brk.observed and brk.expected
            assert "slide" in brk.describe()

    def test_the_guidance_is_a_next_move_not_a_grade(self):
        text = narrative.guidance(narrative.read_arc(TABLE_OF_CONTENTS))
        assert "slide" in text
        assert "Rewrite" in text

    def test_a_deck_that_argues_gets_no_guidance(self):
        assert narrative.guidance(narrative.read_arc(ARGUED)) == ""


class TestWhatIsAndIsNotHeldToTheStandard:
    def test_a_cover_and_a_divider_are_labels_on_purpose(self):
        """Holding them to the claim standard would report a defect in every
        well-made deck."""
        assert not narrative._is_body({"contentType": "title"})
        assert not narrative._is_body({"role": "section"})
        assert not narrative._is_body({"role": "cover"})
        assert narrative._is_body({"contentType": "content"})

    def test_the_deck_s_last_page_is_where_it_arrives(self):
        """A conclusion is excluded from the body on purpose - it is not where
        the argument is made - and checking the body's last page for a
        conclusion then contradicts that."""
        report = narrative.read_arc(ARGUED)
        assert "closing" not in {b.property for b in report.breaks}

    def test_a_deck_ending_on_a_label_is_flagged(self):
        report = narrative.read_arc(TABLE_OF_CONTENTS)
        assert "closing" in {b.property for b in report.breaks}

    def test_an_argument_is_allowed_to_change_subject(self):
        """A page is read against everything the deck has said, not against
        the page before it. This deck pivots from cyber exposure to what
        regulators demand, and those two headlines share *nothing* - a
        pairwise check calls that a break while calling a deck that repeats
        itself coherent.

        Measured across a well-argued deck and one of unrelated topics, the
        best overlap per page came out at 0.27, 0.07, 0.08 against 0.00, 0.00,
        0.00. The signal is zero versus non-zero, and a bar above that flags
        an argument for the crime of developing.
        """
        pivot = "Regulators now demand evidence of controls, not statements of intent"
        previous = "Cyber exposure has outgrown most insurers response programmes"
        assert narrative.continuity(previous, pivot) == 0.0

        earlier = [s["title"] for s in ARGUED[1:3]]
        assert max(narrative.continuity(pivot, e) for e in earlier) > 0.0
        assert "continuity" not in {
            b.property for b in narrative.read_arc(ARGUED).breaks
        }

    def test_a_deck_that_repeats_itself_is_flagged(self):
        same = [
            {"title": "A deck", "contentType": "title"},
            {"title": "Cyber risk is rising fast for insurers", "contentType": "content"},
            {"title": "Cyber risk is rising fast for insurers", "contentType": "content"},
            {"title": "Cyber risk is rising fast for insurers", "contentType": "conclusion"},
        ]
        assert "repetition" in {b.property for b in narrative.read_arc(same).breaks}


class TestItSurvivesWhatARealRunProduces:
    @pytest.mark.parametrize("deck", [[], [{}], ["not a slide"],
                                      [{"title": ""}]])
    def test_a_thin_or_malformed_plan_does_not_raise(self, deck):
        narrative.read_arc(deck)

    def test_a_drafted_slide_is_read_the_same_way_as_a_plan(self):
        drafted = {"slideIndex": 1, "contents": [{"type": "text", "runs": [
            {"text": "Cyber is now the most cited near-term risk"}]}]}
        assert narrative.headline_of(drafted).startswith("Cyber is now")

    def test_it_runs_in_code_at_planning_time(self):
        import inspect

        from app.agent.loop import AgentLoop

        assert "_read_the_arc" in inspect.getsource(AgentLoop._dispatch_tool_body)

    def test_a_flagged_page_is_told_what_to_fix(self):
        """A grade has no next move in it; "slide 4 ends on a topic label
        where a position belongs" has exactly one."""
        import inspect

        from app.agent.loop import AgentLoop

        source = inspect.getsource(AgentLoop._arc_directive)
        assert "slideIndex" in source and "expected" in source
