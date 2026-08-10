"""
Representative decks, rendered for real and put through the same checks the
agent's validation step runs.

Everything else in this suite tests one layer. This renders whole decks -
the shapes a generated presentation actually takes - through PPTXRenderer
and LibreOffice, then applies the geometric, content and package passes. It
is the regression net for "the output got worse", which is the failure that
unit tests cannot see and that issue #56 was opened about.

No LLM is involved: the decks below are the slide JSON the agent produces,
captured so the renderer can be changed with confidence. The prompts that
produce shapes like these are listed in ``REPRESENTATIVE_PROMPTS`` for the
end-to-end runs that do use a provider.
"""

import os
import shutil
import tempfile

import pytest

from app.agent.tools import output_qa
from app.agent.tools.validate_rendered_output import ValidateRenderedOutputTool
from app.renderer.preview_generator import PreviewGenerator
from app.renderer.pptx_renderer import PPTXRenderer

# The prompts these decks stand in for. Kept next to the fixtures so an
# end-to-end run and this offline check stay aimed at the same cases.
REPRESENTATIVE_PROMPTS = (
    "Build a 7-slide board update on a company's last fiscal year: revenue "
    "trend over five years, revenue split by segment, a segment comparison "
    "table, and two slides on what we should do next.",
    "Create a 6-slide explainer on a technical topic for a non-technical "
    "audience, with no numbers in it.",
    "Make a 5-slide deck of this quarter's KPIs: the headline figures, how "
    "each moved, and where we are off plan.",
)


def _text(*runs, bullet=True):
    return {"type": "text",
            "runs": [{"text": t, "bullet": bullet, "level": 0} for t in runs]}


def _title(text):
    return {"type": "text", "runs": [{"text": text, "bold": True, "bullet": False}]}


NUMBERS_DECK = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("Acme FY2025 Board Update"),
        _text("Revenue, margin and the road to FY2026", bullet=False)]},
    {"slideIndex": 1, "layoutId": "chart", "contents": [
        _title("Revenue trajectory"),
        _text("Growth flattened after 2023 as new-customer traffic softened"),
        {"type": "chart", "chart": {
            "chartType": "line",
            "categories": ["2021", "2022", "2023", "2024", "2025"],
            "series": [{"name": "Revenue ($B)", "values": [29.1, 32.3, 36.0, 36.2, 37.4]}],
            "yAxisTitle": "Revenue (US$ billions)", "xAxisTitle": "Fiscal year"}}]},
    {"slideIndex": 2, "layoutId": "chart", "contents": [
        _title("Where revenue comes from"),
        _text("Three quarters of the business is still one region"),
        {"type": "chart", "chart": {
            "chartType": "pie",
            "categories": ["North America", "International", "Channel Development"],
            "series": [{"name": "Share of revenue", "values": [74.0, 21.0, 5.0]}],
            "valueFormat": "percent"}}]},
    {"slideIndex": 3, "layoutId": "table", "contents": [
        _title("Segment performance"),
        {"type": "table", "table": {"headerRow": True, "rows": [
            ["Segment", "Revenue ($B)", "Op. margin", "Stores"],
            ["North America", "26.8", "18.4%", "18,400"],
            ["International", "7.6", "11.2%", "21,900"],
            ["Channel Development", "1.8", "42.1%", "-"],
            ["Total", "36.2", "14.9%", "40,300"]]}}]},
    {"slideIndex": 4, "layoutId": "twoColumn", "contents": [
        _title("Headwinds and tailwinds"),
        _text("Labour cost inflation", "Slower recovery in China"),
        _text("Loyalty programme scale", "Cold beverage mix shift")]},
    {"slideIndex": 5, "layoutId": "conclusion", "contents": [
        _title("What we recommend"),
        _text("Hold new openings flat and reinvest in throughput",
              "Protect the loyalty base with targeted pricing")]},
]

QUALITATIVE_DECK = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("How retrieval-augmented generation works"),
        _text("A plain-language explainer", bullet=False)]},
    {"slideIndex": 1, "layoutId": "content", "contents": [
        _title("The problem it solves"),
        _text("A language model only knows what it was trained on",
              "Anything newer, private or niche is simply absent",
              "Retraining for every change is slow and expensive")]},
    {"slideIndex": 2, "layoutId": "section", "contents": [
        _title("How it works")]},
    {"slideIndex": 3, "layoutId": "content", "contents": [
        _title("Two short bullets"),
        _text("Find the passages that answer the question",
              "Hand them to the model as part of the prompt")]},
    {"slideIndex": 4, "layoutId": "conclusion", "contents": [
        _title("What to take away"),
        _text("Retrieval makes a model current without retraining it")]},
]

METRICS_DECK = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("Q3 KPI review"), _text("Where we are against plan", bullet=False)]},
    {"slideIndex": 1, "layoutId": "content", "contents": [
        _title("The quarter in four numbers"),
        _text("Growth held on price rather than volume"),
        {"type": "metrics", "metrics": {"items": [
            {"value": "$36.2B", "label": "Revenue"},
            {"value": "+3%", "label": "Year over year"},
            {"value": "40,300", "label": "Stores"},
            {"value": "14.9%", "label": "Operating margin"}]}}]},
    {"slideIndex": 2, "layoutId": "chart", "contents": [
        _title("Against plan by quarter"),
        {"type": "chart", "chart": {
            "chartType": "bar",
            "categories": ["Q1", "Q2", "Q3"],
            "series": [{"name": "Plan ($B)", "values": [8.6, 8.9, 9.1]},
                       {"name": "Actual ($B)", "values": [8.7, 8.8, 8.9]}]}}]},
    {"slideIndex": 3, "layoutId": "conclusion", "contents": [
        _title("Where we act"), _text("Close the Q3 shortfall on volume, not price")]},
]

DECKS = {
    "numbers": (NUMBERS_DECK, "#00704A"),
    "qualitative": (QUALITATIVE_DECK, "professional"),
    "metrics": (METRICS_DECK, "#1428A0"),
}


@pytest.fixture(scope="module", params=sorted(DECKS))
def rendered(request):
    """Render a deck for real and convert it the way the preview path does."""
    slides, theme = DECKS[request.param]
    work_dir = tempfile.mkdtemp(prefix=f"ts_regress_{request.param}_")
    pptx_path = os.path.join(work_dir, "deck.pptx")
    try:
        PPTXRenderer(template_path=None, color_theme=theme).render(
            slides=slides,
            metadata={"title": f"{request.param.title()} Deck"},
            output_path=pptx_path,
        )
        pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
        assert pdf_path, f"LibreOffice could not convert the {request.param} deck"
        yield {"name": request.param, "slides": slides,
               "pptx": pptx_path, "pdf": pdf_path}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _describe(issues):
    return "\n".join(f"  slide {i.get('slideIndex', '?')}: {i['detail']}" for i in issues)


class TestEveryRepresentativeDeckRendersCleanly:
    def test_the_geometry_has_no_overflow_or_overlap(self, rendered):
        issues = ValidateRenderedOutputTool._check_geometry(rendered["pdf"])

        assert issues == [], f"{rendered['name']} deck:\n{_describe(issues)}"

    def test_the_copy_passes_the_content_checks(self, rendered):
        issues = output_qa.check_content(rendered["slides"])

        assert issues == [], f"{rendered['name']} deck:\n{_describe(issues)}"

    def test_the_package_is_valid(self, rendered):
        issues = output_qa.check_package(rendered["pptx"])

        assert issues == [], f"{rendered['name']} deck:\n{_describe(issues)}"

    def test_no_slide_renders_nearly_empty(self, rendered):
        advisories = output_qa.check_density(rendered["pdf"])

        assert advisories == [], f"{rendered['name']} deck:\n{_describe(advisories)}"

    def test_every_slide_made_it_into_the_file(self, rendered):
        import fitz

        doc = fitz.open(rendered["pdf"])
        try:
            assert len(doc) == len(rendered["slides"])
        finally:
            doc.close()


class TestTheDeckReadsAsOneDesignedArtifact:
    def test_the_light_dark_rhythm_is_present(self, rendered):
        """A deck of identically white pages is what issue #56 opened on."""
        from app import colors
        from pptx import Presentation

        prs = Presentation(rendered["pptx"])
        backgrounds = {
            colors.to_hex(tuple(slide.background.fill.fore_color.rgb))
            for slide in prs.slides
        }

        assert len(backgrounds) > 1, f"{rendered['name']} deck is all one background"
        assert any(colors.is_dark(b) for b in backgrounds)

    def test_every_slide_carries_the_motif(self, rendered):
        from pptx import Presentation
        from app.renderer.pptx_renderer import CHROME_ACCENT_RULE

        prs = Presentation(rendered["pptx"])
        for index, slide in enumerate(prs.slides):
            assert any((s.name or "") == CHROME_ACCENT_RULE for s in slide.shapes), (
                f"{rendered['name']} slide {index + 1} has no accent rule"
            )

    def test_body_text_is_never_left_at_the_inherited_colour(self, rendered):
        """The defect that put black subtitles on a brand-dark cover."""
        from pptx import Presentation
        from app.renderer.pptx_renderer import is_chrome_shape

        prs = Presentation(rendered["pptx"])
        for index, slide in enumerate(prs.slides):
            for shape in slide.shapes:
                if not shape.has_text_frame or is_chrome_shape(shape):
                    continue
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        assert run.font.color.rgb is not None, (
                            f"{rendered['name']} slide {index + 1}: "
                            f"{run.text[:40]!r} has no explicit colour"
                        )
