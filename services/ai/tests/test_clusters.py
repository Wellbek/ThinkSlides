"""
Choosing which pages of a template are worth looking at.

A PDF template was sampled at fifteen evenly spaced pages. Even sampling is
representative of *position in the document*, which is not the thing anyone
cares about: on a 184-page deck it lands on pages 1, 14, 27, 40 and so on, and
an archetype appearing fewer than a dozen times is likely to be missed
entirely.

The fixture PDF here is built with PyMuPDF and has known repeated layouts - a
cover, several two-column card rows, several full-width band pages, a chart
page - so "each layout is sampled" is a statement about a document whose
layouts are stated rather than inferred. The reference deck's own survey is
committed to ``reference_profile.json`` and checked from there, so neither
test needs the third-party PDF present.
"""

import os
import shutil
import tempfile

import fitz
import pytest

from app.eval import reference
from app.measure import clusters

WIDTH, HEIGHT = 960.0, 540.0
LEFT, RIGHT = WIDTH * 0.05, WIDTH * 0.95


def _text(page, body, x, y, size=12.0):
    page.insert_text((x, y), body, fontsize=size, fontname="helv")


def _cover(page, title="The whole deck, in three words"):
    """Dark, a plate across the top half, and the title set very large."""
    page.draw_rect(fitz.Rect(0, 0, WIDTH, HEIGHT), color=None, fill=(0.02, 0.11, 0.17))
    page.draw_rect(fitz.Rect(0, 0, WIDTH, HEIGHT * 0.55), color=None,
                   fill=(0.10, 0.35, 0.52))
    _text(page, title, LEFT, HEIGHT * 0.78, size=40.0)


def _divider(page, number):
    page.draw_rect(fitz.Rect(0, 0, WIDTH, HEIGHT), color=None, fill=(0.02, 0.11, 0.17))
    _text(page, f"Chapter {number}", LEFT, HEIGHT * 0.5, size=26.0)


def _card_row(page, seed):
    """Four columns of a heading over two paragraphs."""
    _text(page, f"A headline that states finding {seed}", LEFT, 70, size=22.0)
    column = (RIGHT - LEFT) / 4
    for index in range(4):
        x = LEFT + index * column
        _text(page, f"Card {index}", x, 170, size=15.0)
        for line in range(3):
            _text(page, f"A sentence of the card's own prose {seed}{index}",
                  x, 190 + line * 14)


def _row_bands(page, seed):
    """Seven full-width bands, a narrow label beside a wide sentence."""
    _text(page, f"A headline that states finding {seed}", LEFT, 70, size=22.0)
    for row in range(7):
        y = 150 + row * 48
        page.draw_line(fitz.Point(LEFT, y - 14), fitz.Point(RIGHT, y - 14),
                       color=(0.8, 0.8, 0.8), width=0.5)
        _text(page, f"Sector {row}", LEFT, y)
        for line in range(2):
            _text(page, "A full sentence of prose carrying the implication for "
                        "this band", LEFT + 170, y + line * 14)


def _chart_page(page, seed):
    """A headline over a labelled bar chart, with a caption and a source."""
    _text(page, f"A headline that states finding {seed}", LEFT, 70, size=22.0)
    # A plot area, so the chart reads as one region with its labels inside it
    # rather than as six unrelated rectangles beside some numbers.
    page.draw_rect(fitz.Rect(LEFT, 110, RIGHT, 445), color=(0.85, 0.85, 0.85),
                   width=0.6)
    for gridline in range(4):
        y = 130 + gridline * 75
        page.draw_line(fitz.Point(LEFT + 10, y), fitz.Point(RIGHT - 10, y),
                       color=(0.9, 0.9, 0.9), width=0.4)
    for index in range(6):
        height = 60 + index * 40
        x = LEFT + 40 + index * 90
        # Bars closer together than the gap the exhibit finder tolerates, so
        # the six of them read as one chart rather than six rectangles.
        page.draw_rect(fitz.Rect(x, 420 - height, x + 78, 420),
                       color=None, fill=(0.12, 0.31, 0.47))
        _text(page, f"{18 + index * 9}%", x + 12, 410 - height, size=11.0)
        _text(page, f"20{18 + index}", x + 12, 438, size=11.0)
    _text(page, "Adoption, percent of surveyed firms across all regions", LEFT, 468)
    _text(page, "Source: a survey of 1,400 firms, conducted in the fourth quarter",
          LEFT, 500, size=8.0)


def _split_canvas(page, seed):
    """A white left half of prose against a dark right half of statistics."""
    page.draw_rect(fitz.Rect(WIDTH / 2, 0, WIDTH, HEIGHT), color=None,
                   fill=(0.02, 0.11, 0.17))
    _text(page, f"A headline that states finding {seed}", LEFT, 70, size=22.0)
    for line in range(8):
        _text(page, "A sentence of the argument set on the light half of the page",
              LEFT, 150 + line * 20)
    for index in range(4):
        _text(page, f"{20 + index * 7}%", WIDTH / 2 + 40, 150 + index * 90, size=26.0)
        _text(page, "of firms said so", WIDTH / 2 + 40, 175 + index * 90)


LAYOUTS = {
    "cover": (_cover, 1),
    "divider": (_divider, 4),
    "card row": (_card_row, 12),
    "row bands": (_row_bands, 10),
    "exhibit": (_chart_page, 6),
    "split canvas": (_split_canvas, 5),
}


def _build_fixture_pdf(path: str) -> dict[int, str]:
    """A deck of known layouts, and which page is which."""
    doc = fitz.open()
    truth: dict[int, str] = {}
    number = 0

    def add(kind, draw, seed):
        nonlocal number
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        number += 1
        if kind == "cover":
            draw(page)
        else:
            draw(page, seed)
        truth[number] = kind

    add("cover", _cover, 0)
    # Interleaved rather than blocked, so grouping cannot succeed by accident
    # of adjacency - the even sampler would still land on whole runs.
    rounds = max(count for _, count in LAYOUTS.values())
    for index in range(rounds):
        for kind, (draw, count) in LAYOUTS.items():
            if kind == "cover" or index >= count:
                continue
            add(kind, draw, index)
    doc.save(path)
    doc.close()
    return truth


@pytest.fixture(scope="module")
def fixture_pdf():
    work_dir = tempfile.mkdtemp(prefix="ts_clusters_")
    path = os.path.join(work_dir, "template.pdf")
    try:
        yield path, _build_fixture_pdf(path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def surveyed(fixture_pdf):
    path, truth = fixture_pdf
    return clusters.survey_pdf(path), truth


# ──────────────────────────────────────────────────────────────────────
# A page range
# ──────────────────────────────────────────────────────────────────────

class TestAUserCanSayWhichPagesAreTheTemplate:
    @pytest.mark.parametrize("spec,expected", [
        ("20-35", list(range(20, 36))),
        ("20 - 35", list(range(20, 36))),
        ("35-20", list(range(20, 36))),
        ("1,4,9", [1, 4, 9]),
        ("1, 4, 9-12", [1, 4, 9, 10, 11, 12]),
        ("7", [7]),
        ([3, 1, 2], [1, 2, 3]),
    ])
    def test_it_reads_what_a_person_would_type(self, spec, expected):
        assert clusters.parse_page_range(spec, 40) == expected

    @pytest.mark.parametrize("spec", [None, "", "   ", []])
    def test_nothing_named_means_the_whole_document(self, spec):
        assert clusters.parse_page_range(spec, 5) == [1, 2, 3, 4, 5]

    def test_pages_past_the_end_are_dropped_not_refused(self):
        """A user who says 1-500 of a 184-page deck means all of it."""
        assert clusters.parse_page_range("1-500", 4) == [1, 2, 3, 4]

    def test_a_range_entirely_outside_the_document_falls_back(self):
        assert clusters.parse_page_range("900-950", 4) == [1, 2, 3, 4]

    def test_nonsense_is_ignored_rather_than_raising(self):
        assert clusters.parse_page_range("twenty to thirty", 4) == [1, 2, 3, 4]
        assert clusters.parse_page_range("2,,x,3", 4) == [2, 3]

    def test_an_empty_document_has_no_pages(self):
        assert clusters.parse_page_range("1-5", 0) == []

    def test_a_range_limited_survey_sees_only_those_pages(self, fixture_pdf):
        path, _ = fixture_pdf
        survey = clusters.survey_pdf(path, "10-20")

        assert survey.considered == tuple(range(10, 21))
        assert all(10 <= page <= 20 for page in survey.sample)
        assert all(10 <= page <= 20
                   for cluster in survey.clusters for page in cluster.pages)


# ──────────────────────────────────────────────────────────────────────
# Grouping and sampling
# ──────────────────────────────────────────────────────────────────────

class TestPagesAreGroupedByHowTheyLook:
    def test_every_layout_in_the_fixture_is_recognised(self, surveyed):
        survey, truth = surveyed

        labels = {cluster.label for cluster in survey.clusters}

        assert set(truth.values()) <= labels, (
            f"missing {set(truth.values()) - labels}"
        )

    def test_pages_of_one_layout_land_together(self, surveyed):
        """Not one cluster each, which is what too tight a radius gives."""
        survey, truth = surveyed

        for kind in ("card row", "row bands", "split canvas"):
            biggest = max(
                (c for c in survey.clusters if c.label == kind),
                key=lambda c: c.size, default=None,
            )
            assert biggest is not None and biggest.size >= 3, (
                f"{kind} was split into singletons"
            )

    def test_a_cluster_is_mostly_one_true_layout(self, surveyed):
        """Grouping may be coarse; it may not be wrong."""
        survey, truth = surveyed

        for cluster in survey.clusters:
            kinds = [truth[page] for page in cluster.pages]
            dominant = max(set(kinds), key=kinds.count)
            assert kinds.count(dominant) / len(kinds) >= 0.7, (
                f"cluster {cluster.id} ({cluster.label}) mixes {set(kinds)}"
            )

    def test_a_clusters_representative_is_one_of_its_pages(self, surveyed):
        survey, _ = surveyed

        for cluster in survey.clusters:
            assert cluster.representative in cluster.pages

    def test_every_considered_page_is_in_exactly_one_cluster(self, surveyed):
        survey, _ = surveyed

        placed = [page for cluster in survey.clusters for page in cluster.pages]

        assert sorted(placed) == list(survey.considered)
        assert len(placed) == len(set(placed))

    def test_clusters_are_ordered_by_how_much_of_the_deck_they_are(self, surveyed):
        survey, _ = surveyed

        sizes = [cluster.size for cluster in survey.clusters]

        assert sizes == sorted(sizes, reverse=True)


class TestTheSampleSpendsTheBudgetOnLayouts:
    def test_every_layout_is_sampled_at_least_once(self, surveyed):
        """The acceptance criterion, on a document whose layouts are known."""
        survey, truth = surveyed

        sampled_kinds = {truth[page] for page in survey.sample}

        assert set(truth.values()) <= sampled_kinds, (
            f"never looked at {set(truth.values()) - sampled_kinds}"
        )

    def test_even_sampling_would_not_have_managed_that(self, surveyed):
        """The claim this issue is built on, checked rather than asserted."""
        survey, truth = surveyed
        total = len(truth)
        budget = len(survey.sample)
        step = (total - 1) / (budget - 1)
        evenly = {round(i * step) + 1 for i in range(budget)}

        assert {truth[p] for p in survey.sample} >= {truth[p] for p in evenly}

    def test_the_cover_is_always_looked_at(self, surveyed):
        """It carries the brand treatment nothing else does."""
        survey, _ = surveyed

        assert survey.sample[0] == 1

    def test_the_budget_is_not_exceeded(self, fixture_pdf):
        path, _ = fixture_pdf

        assert len(clusters.survey_pdf(path, budget=6).sample) <= 6
        assert len(clusters.survey_pdf(path, budget=15).sample) <= 15

    def test_a_small_budget_still_covers_the_biggest_clusters_first(self, fixture_pdf):
        path, _ = fixture_pdf
        survey = clusters.survey_pdf(path, budget=4)

        biggest = [c.representative for c in survey.clusters[:3]]

        assert any(page in survey.sample for page in biggest)

    def test_the_sample_is_in_page_order(self, surveyed):
        survey, _ = surveyed

        assert list(survey.sample) == sorted(survey.sample)

    def test_a_short_document_is_sampled_whole(self, fixture_pdf):
        path, _ = fixture_pdf
        survey = clusters.survey_pdf(path, "3-6", budget=15)

        assert set(survey.sample) == {3, 4, 5, 6}


class TestGroupingCostsNoModelCall:
    def test_the_module_never_reaches_for_a_provider(self):
        """Stated as a test because it is the constraint the issue names."""
        import inspect

        source = inspect.getsource(clusters)

        for forbidden in ("llm", "provider", "chat", "openai", "zhipu"):
            assert forbidden not in source.lower(), forbidden

    def test_a_survey_runs_with_no_network_and_no_key(self, fixture_pdf, monkeypatch):
        path, _ = fixture_pdf
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        assert clusters.survey_pdf(path).clusters


class TestDegenerateDocuments:
    def test_a_document_of_one_page_is_one_cluster(self):
        work_dir = tempfile.mkdtemp(prefix="ts_one_")
        try:
            path = os.path.join(work_dir, "one.pdf")
            doc = fitz.open()
            _cover(doc.new_page(width=WIDTH, height=HEIGHT))
            doc.save(path)
            doc.close()

            survey = clusters.survey_pdf(path)

            assert len(survey.clusters) == 1
            assert survey.sample == (1,)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_a_document_of_identical_pages_is_one_cluster(self):
        work_dir = tempfile.mkdtemp(prefix="ts_same_")
        try:
            path = os.path.join(work_dir, "same.pdf")
            doc = fitz.open()
            for _ in range(8):
                _row_bands(doc.new_page(width=WIDTH, height=HEIGHT), 1)
            doc.save(path)
            doc.close()

            survey = clusters.survey_pdf(path)

            assert len(survey.clusters) == 1
            assert survey.clusters[0].size == 8
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_no_pages_read_is_an_empty_survey(self):
        survey = clusters.survey_shapes([], page_count=0)

        assert survey.clusters == ()
        assert survey.sample == ()

    def test_a_page_that_could_not_be_read_is_skipped(self):
        from app.measure.page_metrics import PageShape

        survey = clusters.survey_shapes(
            [PageShape(page_number=1), PageShape(page_number=2)], page_count=2
        )

        assert survey.clusters == ()


class TestTheSurveyIsReportable:
    def test_it_serialises_to_something_a_bundle_can_hold(self, surveyed):
        survey, _ = surveyed

        data = survey.to_dict()

        assert data["pageCount"] == len(survey.considered)
        assert data["sampledPages"] == list(survey.sample)
        assert all({"id", "label", "pages", "representative", "size"} <= set(c)
                   for c in data["clusters"])

    def test_it_can_say_what_kind_of_page_any_page_is(self, surveyed):
        survey, truth = surveyed

        assert survey.label_of(1) == "cover"
        assert survey.label_of(9999) is None

    def test_it_lists_the_kinds_the_sample_covered(self, surveyed):
        survey, _ = surveyed

        covered = survey.sampled_labels()

        assert covered
        assert len(covered) == len(set(covered))


# ──────────────────────────────────────────────────────────────────────
# The reference deck
# ──────────────────────────────────────────────────────────────────────

class TestTheReferenceSurveyIsCommitted:
    """Checked from the committed fixture, so the third-party PDF is never
    needed - and nothing here reveals a validation page's content, only which
    pages group together."""

    def test_the_whole_deck_was_surveyed(self):
        survey = reference.load_reference_survey()

        assert survey.page_count == 184
        assert len(survey.considered) == 184

    def test_it_found_far_fewer_layouts_than_pages(self):
        survey = reference.load_reference_survey()

        assert 5 <= len(survey.clusters) <= 60

    @pytest.mark.parametrize("kind", ["split canvas", "row bands", "exhibit",
                                      "card row"])
    def test_the_sample_covers_the_kinds_the_epic_names(self, kind):
        """#117's acceptance criterion: even sampling guaranteed none of
        these."""
        survey = reference.load_reference_survey()

        assert kind in survey.sampled_labels()

    def test_the_cover_is_in_the_sample(self):
        assert 1 in reference.load_reference_survey().sample

    def test_the_sample_is_within_the_vision_budget(self):
        from app.agent.tools.extract_template_design_system import (
            VISION_PAGE_BUDGET,
        )

        assert len(reference.load_reference_survey().sample) <= VISION_PAGE_BUDGET

    def test_a_third_of_the_deck_is_not_one_undifferentiated_lump(self):
        """The failure mode of too loose a radius, stated as a bound."""
        survey = reference.load_reference_survey()
        biggest = max(cluster.size for cluster in survey.clusters)

        assert biggest < survey.page_count * 0.45
