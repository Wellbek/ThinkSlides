"""
A deck's archetypes, and the chapter it repeats them in.

The reference is not 184 individually designed pages. It is one chapter run
fourteen times, each technology trend getting the same slide series with the
same interrogative titles, verbatim. That repetition is most of what makes it
read as one document rather than a pile of slides, and the planner had no way
to express it - so a fourteen-subject deck came out as fourteen unrelated runs
of invention.

The detection rules are checked against a synthetic document whose chapters are
stated rather than inferred: five subjects, the same four questions each, with
deliberate deviations (a chapter missing a position, a chapter with an extra
one, a title reworded) so that "it survives deviation" is a test and not a
hope. The acceptance criterion - fourteen chapters in the reference - is
checked from the committed fixture, so the third-party PDF is never needed.

Nothing here calls a model, which is the last of #121's criteria.
"""

import fitz
import pytest

from app.eval import reference
from app.measure import archetypes as arch
from app.measure.clusters import PageCluster, TemplateSurvey

WIDTH, HEIGHT = 960.0, 540.0

QUESTIONS = [
    "What is the trend about?",
    "Why should leaders pay attention?",
    "What industries are most affected by the trend?",
    "Additional resources",
]
SUBJECTS = ["Applied AI", "Cloud and edge", "Bioengineering",
            "Clean energy", "Quantum"]


def _synthetic():
    """A deck of five chapters, with deviations, and what is in it."""
    headlines: dict[int, str] = {}
    labels: dict[int, str] = {}
    page = 0

    def add(title, label):
        nonlocal page
        page += 1
        headlines[page] = title
        labels[page] = label

    add("A deck about several trends", "cover")
    for index, subject in enumerate(SUBJECTS):
        add(f"Chapter: {subject}", "divider")
        for position, question in enumerate(QUESTIONS):
            # Chapter 3 skips a position; chapter 4 rewords one; chapter 5
            # runs a continuation page. A pattern nothing may deviate from is
            # not a pattern, it is a template.
            if index == 2 and position == 2:
                continue
            title = question
            if index == 3 and position == 1:
                title = "Why should leaders pay attention to this?"
            add(title, ["exhibit", "card row", "row bands", "text page"][position])
            if index == 4 and position == 1:
                add(f"{question} (continued)", "card row")
    return headlines, labels


def _survey(labels: dict[int, str]) -> TemplateSurvey:
    """A survey shaped the way ``clusters`` would produce one."""
    by_label: dict[str, list[int]] = {}
    for page, label in labels.items():
        by_label.setdefault(label, []).append(page)
    clusters = tuple(
        PageCluster(id=index, label=label, pages=tuple(sorted(pages)),
                    representative=min(pages))
        for index, (label, pages) in enumerate(
            sorted(by_label.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        )
    )
    return TemplateSurvey(page_count=len(labels),
                          considered=tuple(sorted(labels)), clusters=clusters)


@pytest.fixture(scope="module")
def synthetic():
    headlines, labels = _synthetic()
    return arch.build(_survey(labels), headlines=headlines), headlines, labels


# ──────────────────────────────────────────────────────────────────────
# Titles
# ──────────────────────────────────────────────────────────────────────

class TestATitleIsTheQuestionItAsks:
    @pytest.mark.parametrize("printed,expected", [
        ("What is the trend about?", "what trend about"),
        ("What is the trend about? (continued)", "what trend about"),
        ("WHAT IS THE TREND ABOUT", "what trend about"),
        ("  What  is the trend   about?  ", "what trend about"),
    ])
    def test_case_punctuation_and_continuations_do_not_change_it(
            self, printed, expected):
        assert arch.normalise_title(printed) == expected

    @pytest.mark.parametrize("a,b", [
        ("What is the trend about?", "What is this trend about?"),
        ("What is the trend about?", "What is the tech trend about?"),
        ("What industries are most affected by the trend?",
         "What industries could be most affected by the trend?"),
    ])
    def test_the_same_question_asked_differently_still_matches(self, a, b):
        assert arch.titles_match(arch.normalise_title(a), arch.normalise_title(b))

    @pytest.mark.parametrize("a,b", [
        ("What is the trend about?", "Additional resources"),
        ("Why should leaders pay attention?",
         "What industries are most affected by the trend?"),
    ])
    def test_different_questions_do_not(self, a, b):
        assert not arch.titles_match(arch.normalise_title(a),
                                     arch.normalise_title(b))

    def test_an_empty_title_matches_nothing(self):
        assert not arch.titles_match("", "what trend about")

    def test_a_headline_is_every_line_of_the_largest_type(self):
        doc = fitz.open()
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        page.insert_text((60, 80), "Why should leaders", fontsize=25)
        page.insert_text((60, 110), "pay attention?", fontsize=25)
        page.insert_text((60, 200), "A body sentence", fontsize=12)

        headline = arch.page_headline(page)
        doc.close()

        assert headline == "Why should leaders pay attention?"

    def test_a_page_with_no_text_has_no_headline(self):
        doc = fitz.open()
        assert arch.page_headline(doc.new_page(width=WIDTH, height=HEIGHT)) == ""
        doc.close()


# ──────────────────────────────────────────────────────────────────────
# Archetypes
# ──────────────────────────────────────────────────────────────────────

class TestClustersBecomeNamedArchetypes:
    def test_every_cluster_becomes_one(self, synthetic):
        built, _, labels = synthetic

        assert len(built.archetypes) == len(set(labels.values()))

    def test_each_carries_its_pages_and_what_they_hold(self, synthetic):
        built, _, labels = synthetic

        for archetype in built.archetypes:
            assert archetype.pages
            assert archetype.representative in archetype.pages
            assert archetype.size == len(archetype.pages)

    def test_names_are_unique_even_when_labels_repeat(self):
        survey = TemplateSurvey(
            page_count=6, considered=tuple(range(1, 7)),
            clusters=(
                PageCluster(id=0, label="card row", pages=(1, 2), representative=1),
                PageCluster(id=1, label="card row", pages=(3, 4), representative=3),
                PageCluster(id=2, label="card row", pages=(5, 6), representative=5),
            ),
        )

        names = [a.name for a in arch.build(survey).archetypes]

        assert names == ["card row", "card row 2", "card row 3"]
        assert len(set(names)) == 3

    def test_an_archetype_knows_what_register_its_titles_are_in(self, synthetic):
        built, _, _ = synthetic

        questions = [a for a in built.archetypes if a.title_register == "question"]

        assert questions, "a deck of questions has no question archetype"

    def test_a_divider_is_not_a_question(self, synthetic):
        built, _, _ = synthetic

        divider = built.named("divider")

        assert divider is not None
        assert divider.title_register == "label"

    def test_the_grammar_of_its_representative_travels_with_it(self):
        from app.measure.grammar import LayoutGrammar, TemplateGrammar

        survey = TemplateSurvey(
            page_count=2, considered=(1, 2),
            clusters=(PageCluster(id=0, label="split canvas", pages=(1, 2),
                                  representative=1),),
        )
        grammar = TemplateGrammar(layouts=[LayoutGrammar(
            page=1, features=("rail",),
            regions=({"id": "rail", "kind": "rail", "x": 0.0, "y": 0.0,
                      "w": 0.33, "h": 1.0},),
        )])

        archetype = arch.build(survey, grammar).archetypes[0]

        assert archetype.features == ("rail",)
        assert archetype.regions[0]["kind"] == "rail"

    def test_an_empty_survey_yields_nothing_rather_than_failing(self):
        built = arch.build(TemplateSurvey(page_count=0, considered=()))

        assert built.archetypes == ()
        assert built.chapter is None


# ──────────────────────────────────────────────────────────────────────
# The chapter
# ──────────────────────────────────────────────────────────────────────

class TestFindingTheRepeatingChapter:
    def test_it_finds_one_chapter_per_subject(self, synthetic):
        built, _, _ = synthetic

        assert built.chapter is not None
        assert built.chapter.count == len(SUBJECTS)

    def test_the_positions_are_the_questions_in_order(self, synthetic):
        built, _, _ = synthetic

        asked = [arch.normalise_title(p.title) for p in built.chapter.positions]

        for question in QUESTIONS:
            assert any(arch.titles_match(arch.normalise_title(question), a)
                       for a in asked), question

    def test_each_position_records_what_layout_answers_it(self, synthetic):
        built, _, _ = synthetic

        assert all(position.archetype for position in built.chapter.positions)

    def test_a_reworded_title_still_counts_as_the_same_position(self, synthetic):
        """Chapter 4 asks "Why should leaders pay attention to this?"."""
        built, _, _ = synthetic

        attention = next(
            p for p in built.chapter.positions
            if arch.titles_match(arch.normalise_title(p.title),
                                 "why should leaders pay attention")
        )

        assert attention.support >= len(SUBJECTS) - 1

    def test_a_chapter_missing_a_position_is_still_a_chapter(self, synthetic):
        """Chapter 3 skips one. Deviation is expected, not fatal."""
        built, _, _ = synthetic

        assert built.chapter.count == len(SUBJECTS)

    def test_the_pattern_is_described_in_the_words_a_planner_works_in(
            self, synthetic):
        built, _, _ = synthetic

        described = built.chapter.describe()

        assert f"repeats one chapter {len(SUBJECTS)} times" in described
        assert QUESTIONS[0] in described

    def test_a_deck_with_no_repetition_has_no_chapter(self):
        headlines = {n: f"A one-off headline number {n}" for n in range(1, 13)}
        labels = {n: "text page" for n in headlines}

        built = arch.build(_survey(labels), headlines=headlines)

        assert built.chapter is None

    def test_a_deck_too_short_to_repeat_anything_has_no_chapter(self):
        headlines = {1: "One", 2: "Two", 3: "Three"}
        labels = {n: "text page" for n in headlines}

        assert arch.build(_survey(labels), headlines=headlines).chapter is None

    def test_the_front_matter_is_not_counted_as_a_chapter(self, synthetic):
        """A deck's opening pages start with the same title page a chapter
        does and then go their own way."""
        built, _, _ = synthetic

        assert 1 not in built.chapter.occurrences

    def test_it_round_trips_through_the_bundle(self, synthetic):
        built, _, _ = synthetic

        again = arch.TemplateArchetypes.from_dict(built.to_dict())

        assert again.chapter == built.chapter
        assert [a.name for a in again.archetypes] == \
            [a.name for a in built.archetypes]


class TestFindingItCostsNoModelCall:
    def test_the_module_never_reaches_for_a_provider(self):
        import inspect

        source = inspect.getsource(arch)

        for forbidden in ("llm", "provider", "openai", "zhipu", "chat_with"):
            assert forbidden not in source.lower(), forbidden


# ──────────────────────────────────────────────────────────────────────
# Holding a plan to the pattern
# ──────────────────────────────────────────────────────────────────────

def _planned(subjects, questions):
    return [
        {"slideIndex": i, "title": question, "chapterOf": subject}
        for i, (subject, question) in enumerate(
            (s, q) for s in subjects for q in questions
        )
    ]


class TestAPlanIsCheckedAgainstItsOwnPattern:
    def test_a_consistent_deck_reports_nothing(self):
        slides = _planned(["Applied AI", "Cloud"], QUESTIONS)

        assert arch.check_consistency(slides) == []

    def test_a_reworded_question_is_tolerated(self):
        slides = _planned(["Applied AI"], QUESTIONS)
        slides += [{"slideIndex": 9, "title": q, "chapterOf": "Cloud"}
                   for q in ["What is this trend about?"] + QUESTIONS[1:]]

        assert arch.check_consistency(slides) == []

    def test_a_different_question_in_the_same_slot_is_reported(self):
        slides = _planned(["Applied AI"], QUESTIONS)
        slides += [{"slideIndex": 9, "title": q, "chapterOf": "Cloud"}
                   for q in ["Something else entirely"] + QUESTIONS[1:]]

        problems = arch.check_consistency(slides)

        assert len(problems) == 1
        assert "different questions" in problems[0]

    def test_chapters_of_different_lengths_are_reported(self):
        slides = _planned(["Applied AI"], QUESTIONS)
        slides += _planned(["Cloud"], QUESTIONS[:2])

        problems = arch.check_consistency(slides)

        assert any("different lengths" in p for p in problems)

    def test_a_deck_that_is_not_chaptered_is_not_held_to_a_pattern(self):
        slides = [{"slideIndex": i, "title": f"Slide {i}"} for i in range(6)]

        assert arch.check_consistency(slides) == []

    def test_one_chapter_alone_is_not_a_pattern(self):
        assert arch.check_consistency(_planned(["Applied AI"], QUESTIONS)) == []

    def test_junk_does_not_crash_the_check(self):
        assert arch.check_consistency([None, "a string", 7, {}]) == []


class TestThePlannerIsOfferedThePattern:
    @staticmethod
    def _guidance(design_system):
        from app.agent.tools.plan_presentation_structure import _chapter_guidance

        return _chapter_guidance(design_system)

    def test_it_states_the_series_and_how_to_instantiate_it(self):
        design_system = {"archetypes":
                         reference.load_archetypes().to_dict()}

        guidance = self._guidance(design_system)

        assert "repeats one chapter" in guidance
        assert "once per subject" in guidance
        assert "chapterOf" in guidance

    def test_it_says_deviation_is_a_decision_not_the_default(self):
        design_system = {"archetypes": reference.load_archetypes().to_dict()}

        guidance = self._guidance(design_system)

        assert "Deviation should be a decision, not the default" in guidance
        assert "inventing data to fill the shape is not" in guidance

    def test_a_template_with_no_chapter_offers_nothing(self):
        assert self._guidance({}) == ""
        assert self._guidance({"archetypes": {"archetypes": []}}) == ""


# ──────────────────────────────────────────────────────────────────────
# The reference deck
# ──────────────────────────────────────────────────────────────────────

class TestTheReferenceChapterIsCommitted:
    """#121's acceptance criterion: given the reference deck, chapter
    detection identifies a repeating series and its title templates, and the
    fourteen trend chapters are the test case."""

    def test_it_found_fourteen_chapters(self):
        chapter = reference.load_archetypes().chapter

        assert chapter is not None
        assert chapter.count == 14

    def test_the_chapters_are_evenly_spread_through_the_deck(self):
        occurrences = reference.load_archetypes().chapter.occurrences
        gaps = [b - a for a, b in zip(occurrences, occurrences[1:])]

        assert min(gaps) >= 8
        assert max(gaps) <= 18

    def test_the_series_is_long_enough_to_be_a_chapter(self):
        assert reference.load_archetypes().chapter.length >= 8

    @pytest.mark.parametrize("question", [
        "What is the trend about?",
        "Why should leaders pay attention?",
        "What industries are most affected by the trend?",
        "What should leaders consider when engaging with the trend?",
        "Additional resources",
    ])
    def test_the_questions_the_epic_names_are_in_the_pattern(self, question):
        positions = reference.load_archetypes().chapter.positions
        wanted = arch.normalise_title(question)

        assert any(arch.titles_match(wanted, arch.normalise_title(p.title))
                   for p in positions), question

    def test_most_positions_are_asked_the_same_way_in_most_chapters(self):
        chapter = reference.load_archetypes().chapter
        strong = [p for p in chapter.positions if p.support >= chapter.count * 0.5]

        assert len(strong) >= 4

    def test_every_position_names_the_layout_that_answers_it(self):
        for position in reference.load_archetypes().chapter.positions:
            assert position.archetype

    def test_the_archetypes_carry_grammar_and_density(self):
        archetypes = reference.load_archetypes().archetypes

        assert len(archetypes) >= 5
        assert any(a.features for a in archetypes)
        assert any(a.words_median > 100 for a in archetypes)

    def test_the_bundle_has_somewhere_to_keep_them(self):
        from app.db.models import DesignSystemBundle

        assert "archetypes" in DesignSystemBundle.__table__.columns
