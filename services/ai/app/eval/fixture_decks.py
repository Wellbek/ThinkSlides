"""
The decks the harness scores, and the regression suite renders.

These are the slide JSON the agent actually produces, captured so the renderer
can be changed with confidence and so "the output got worse" is a measurable
claim rather than an impression. No LLM is involved in producing them; the
prompts they stand in for are listed in ``REPRESENTATIVE_PROMPTS`` for the
end-to-end runs that do use a provider.

They live in ``app`` rather than in ``tests`` because two callers need the same
decks - ``tests/test_regression_decks.py`` renders them to check the output is
sound, and ``python -m app.eval baseline`` renders them to score how close the
output sits to the reference deck. Two copies would drift.
"""

from __future__ import annotations

import os
import tempfile
from typing import Iterator

# The prompts these decks stand in for. Kept next to the fixtures so an
# end-to-end run and the offline checks stay aimed at the same cases.
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


def render_fixture_decks() -> Iterator[tuple[str, str, str]]:
    """
    Render every fixture deck, yielding ``(name, pptx_path, work_dir)``.

    The caller owns ``work_dir`` and is responsible for removing it - the
    rendered file has to outlive this function for the caller to convert and
    measure it.
    """
    from app.renderer.pptx_renderer import PPTXRenderer

    for name in sorted(DECKS):
        slides, theme = DECKS[name]
        work_dir = tempfile.mkdtemp(prefix=f"ts_eval_{name}_")
        pptx_path = os.path.join(work_dir, "deck.pptx")
        PPTXRenderer(template_path=None, color_theme=theme).render(
            slides=slides,
            metadata={"title": f"{name.title()} Deck"},
            output_path=pptx_path,
        )
        yield name, pptx_path, work_dir
