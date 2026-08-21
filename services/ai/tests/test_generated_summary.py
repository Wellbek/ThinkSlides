"""
What a finished run says it produced.

A deck is rendered once per validation attempt, and every render appends
its own row to ``generated_files`` plus a fresh copy of every chart it
drew. Listing them produced, verbatim, on one real run:

    Presentation generated: Overview....pptx, slide-2-chart-1.png,
    slide-4-chart-1.png, Overview....pptx, slide-2-chart-1.png,
    slide-2-chart-2.png, slide-4-chart-1.png
"""

from app.api.generate import _generated_summary


def _deck(name: str) -> dict:
    return {"fileName": name, "fileType": "pptx"}


def _image(name: str) -> dict:
    return {"fileName": name, "fileType": "image"}


def test_a_deck_rendered_four_times_is_named_once():
    files = [_deck("gcf.pptx"), _image("slide-2-chart-1.png"),
             _deck("gcf.pptx"), _image("slide-2-chart-1.png"),
             _image("slide-2-chart-2.png"), _image("slide-4-chart-1.png")]
    assert _generated_summary(files) == (
        "Presentation generated: gcf.pptx - with 3 generated images"
    )


def test_the_deck_alone_needs_no_tally():
    assert _generated_summary([_deck("night-trains.pptx")]) == (
        "Presentation generated: night-trains.pptx"
    )


def test_one_image_is_singular():
    summary = _generated_summary([_deck("d.pptx"), _image("a.png")])
    assert summary.endswith("with 1 generated image")


def test_the_last_deck_wins_when_a_rename_happened_mid_run():
    files = [_deck("draft.pptx"), _deck("final.pptx")]
    assert "final.pptx" in _generated_summary(files)


def test_a_run_that_wrote_only_images_does_not_claim_a_deck():
    assert "no deck" in _generated_summary([_image("chart.png")])
