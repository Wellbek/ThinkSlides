"""
The output checks that geometry cannot see.

Issue #56's G11: the geometric pass reads real LibreOffice output and is
strong at what it does, but a deck can pass it cleanly while carrying a
bullet reading "[insert figure]", the same point on three slides, a chart
with its own numbers listed beside it, or a package only python-pptx is
lenient enough to open.
"""

import os
import tempfile
import zipfile

import pytest

from app.agent.tools import output_qa
from app.agent.tools.output_qa import check_content, check_density, check_package


def _slide(index, texts, extra=None):
    contents = [
        {"type": "text", "runs": [{"text": t} for t in texts]},
    ]
    if extra:
        contents.append(extra)
    return {"slideIndex": index, "contents": contents}


def _details(issues):
    return " | ".join(i["detail"] for i in issues)


class TestPlaceholderCopy:
    @pytest.mark.parametrize("text", [
        "Lorem ipsum dolor sit amet",
        "Revenue was [insert figure] last year",
        "Click to add text",
        "TBD",
        "Growth of <your number here>",
        "Margin reached XXX%",
    ])
    def test_a_hole_left_in_the_copy_is_flagged(self, text):
        issues = check_content([_slide(0, ["Title", text])])

        assert issues
        assert issues[0]["slideIndex"] == 0

    def test_real_copy_is_not_flagged(self):
        issues = check_content([
            _slide(0, ["Where the business stands",
                       "Revenue reached $36.2B in FY2025, up 3% year over year"]),
        ])

        assert issues == []


class TestRepeatedPoints:
    def test_the_same_point_twice_on_one_slide_is_flagged(self):
        point = "North America remains the profit engine at 74% of operating income"
        issues = check_content([_slide(0, ["Title", point, point])])

        assert "twice on this slide" in _details(issues)

    def test_a_point_repeated_on_a_later_slide_names_the_first(self):
        point = "China comparable sales returned to growth in the fourth quarter"
        issues = check_content([
            _slide(0, ["First", point]),
            _slide(1, ["Second", point]),
        ])

        assert "already appears on slide 1" in _details(issues)
        assert issues[0]["slideIndex"] == 1

    def test_short_shared_labels_are_not_duplicates(self):
        """"Revenue" appearing on two slides is a label, not a repeated point."""
        issues = check_content([
            _slide(0, ["Revenue", "Revenue"]),
            _slide(1, ["Revenue"]),
        ])

        assert issues == []


class TestCopyRestatingItsVisual:
    CHART = {
        "type": "chart",
        "chart": {
            "chartType": "line",
            "categories": ["2023", "2024", "2025"],
            "series": [{"name": "Revenue ($B)", "values": [36.0, 36.2, 37.4]}],
        },
    }

    def test_bullets_listing_the_charts_own_numbers_are_flagged(self):
        issues = check_content([_slide(0, [
            "Revenue trajectory",
            "Revenue was 36.0 in 2023",
            "Revenue was 36.2 in 2024",
        ], extra=self.CHART)])

        assert "restate figures" in _details(issues)

    def test_headlining_a_single_figure_is_fine(self):
        """One number pulled out as the headline is good writing, not
        duplication - only a list of the chart's own values is."""
        issues = check_content([_slide(0, [
            "Revenue trajectory",
            "Growth flattened after 2023, ending the year at 37.4",
        ], extra=self.CHART)])

        assert issues == []

    def test_a_tables_own_figures_count_too(self):
        table = {
            "type": "table",
            "table": {"headerRow": True,
                      "rows": [["Segment", "Revenue"], ["NA", "26.8"], ["Intl", "7.6"]]},
        }
        issues = check_content([_slide(0, [
            "Segments", "North America made 26.8", "International made 7.6",
        ], extra=table)])

        assert "restate figures" in _details(issues)


class TestPackageIntegrity:
    def _pptx(self, entries):
        path = tempfile.mktemp(suffix=".pptx")
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return path

    def test_a_well_formed_package_passes(self):
        path = self._pptx([("[Content_Types].xml", "<x/>"),
                           ("ppt/presentation.xml", "<x/>")])
        try:
            assert check_package(path) == []
        finally:
            os.unlink(path)

    def test_a_duplicate_entry_is_flagged(self):
        """An earlier default-template generator really did produce two
        ppt/slides/slide1.xml entries; LibreOffice rejected the file and no
        preview was ever generated."""
        path = self._pptx([("[Content_Types].xml", "<x/>"),
                           ("ppt/presentation.xml", "<x/>"),
                           ("ppt/slides/slide1.xml", "<a/>"),
                           ("ppt/slides/slide1.xml", "<b/>")])
        try:
            assert "duplicate package entries" in _details(check_package(path))
        finally:
            os.unlink(path)

    def test_a_missing_required_part_is_flagged(self):
        path = self._pptx([("[Content_Types].xml", "<x/>")])
        try:
            assert "ppt/presentation.xml" in _details(check_package(path))
        finally:
            os.unlink(path)

    def test_a_file_that_is_not_a_zip_is_flagged_rather_than_raising(self):
        path = tempfile.mktemp(suffix=".pptx")
        with open(path, "w") as handle:
            handle.write("not a pptx")
        try:
            assert "valid package" in _details(check_package(path))
        finally:
            os.unlink(path)


class TestDensity:
    def test_a_real_deck_is_measured_without_error(self):
        """Density is an advisory over the rendered PDF - it must survive a
        real file and stay quiet about a normally-filled slide."""
        from app.renderer.pptx_renderer import PPTXRenderer
        from app.renderer.preview_generator import PreviewGenerator

        slides = [{
            "slideIndex": 0, "layoutId": "content",
            "contents": [
                {"type": "text", "runs": [{"text": "A well-filled slide", "bold": True}]},
                {"type": "text", "runs": [
                    {"text": f"Point number {i} carries a full sentence of real content",
                     "bullet": True, "level": 0}
                    for i in range(6)
                ]},
            ],
        }]
        work_dir = tempfile.mkdtemp(prefix="ts_density_")
        pptx_path = os.path.join(work_dir, "deck.pptx")
        try:
            PPTXRenderer(template_path=None).render(
                slides=slides, metadata={"title": "T"}, output_path=pptx_path
            )
            pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
            assert pdf_path, "LibreOffice could not convert the deck"

            assert check_density(pdf_path) == []
        finally:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_the_sparse_threshold_stays_conservative(self):
        """A validator that sends a deck back over whitespace would spend the
        revision budget on taste."""
        assert output_qa.SPARSE_COVERAGE_RATIO <= 0.1
