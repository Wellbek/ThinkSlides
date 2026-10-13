"""One frame, one ground (#396).

A frame inked for the ground under it cannot sit across two grounds: on the
WTI split-canvas page a chart straddled the navy panel's edge and was drawn
throughout in the white half's dark ink. Packs are seated as they load, and
the renderer inks each chart and block for the ground under its own frame.
"""
import pytest

from app import colors
from app.design import run as design_run
from app.design.grounds import STRADDLE_SHARE, seated, straddling
from app.design.pack import Pack
from app.design.schema import Canvas, FracPlace, Frame, GridSpec
from app.renderer.chart_renderer import SERIES_CONTRAST, _adapt_to_ground, _lifted
from app.renderer.table_style import resolve_chart_style

CANVAS = Canvas(w_in=17.0, h_in=11.0)
GRID = GridSpec(columns=4, gutter_in=0.33)
WHITE, NAVY = "#FFFFFF", "#2A4570"


def _halves():
    return (
        Frame(id="left", role="panel", place=FracPlace(x=0, y=0, w=0.5, h=1), ground=WHITE),
        Frame(id="right", role="panel", place=FracPlace(x=0.5, y=0, w=0.5, h=1), ground=NAVY),
    )


def _frac(left, top, right, bottom):
    return FracPlace(x=left / 17, y=top / 11, w=(right - left) / 17, h=(bottom - top) / 11)


def test_a_frame_across_the_panel_edge_is_found_with_the_ground_it_mostly_sits_on():
    figure = Frame(id="figure", role="figure", z=1, place=_frac(7.96, 0.42, 14.59, 10.58))
    ((found, ground),) = straddling(_halves() + (figure,), CANVAS, GRID)
    assert (found.id, ground.id) == ("figure", "right")


def test_a_hairline_over_the_edge_is_not_a_straddle():
    share = STRADDLE_SHARE / 2
    width = 6.0
    figure = Frame(id="figure", role="figure", z=1,
                   place=_frac(8.5 - share * width, 1, 8.5 + (1 - share) * width, 10))
    assert straddling(_halves() + (figure,), CANVAS, GRID) == []


def test_a_frame_with_its_own_ground_may_cross_the_edge():
    """A pull-quote on its own card, laid across the panel edge."""
    card = Frame(id="quote", role="pullquote", z=1, ground="#F4F1EA", place=_frac(6, 4, 11, 7))
    assert straddling(_halves() + (card,), CANVAS, GRID) == []


def test_a_scrim_is_not_a_ground_of_its_own():
    """A wash at under half opacity: white and navy still differ beneath it."""
    from app.design.schema import Scrim

    washed = Frame(id="caption", role="caption", z=1, scrim=Scrim(), place=_frac(6, 8, 11, 9))
    assert [frame.id for frame, _ in straddling(_halves() + (washed,), CANVAS, GRID)] == ["caption"]


def _pack_with(*frames) -> Pack:
    base = design_run.load("wti-2025")
    archetype = base.archetypes[0].model_copy(update={"id": "probe", "frames": frames, "slots": ()})
    return base.model_copy(update={"archetypes": (archetype,), "grid": GRID})


def test_seating_clips_to_the_ground_a_gutter_in_and_keeps_the_bleed_it_was_not_cut_at():
    figure = Frame(id="figure", role="figure", z=1, bleed=("top", "right", "bottom"),
                   place=_frac(7.96, 0.42, 14.59, 10.58))
    pack = seated(_pack_with(*_halves(), figure))
    moved = next(f for f in pack.archetypes[0].frames if f.id == "figure")
    left = moved.place.x * 17
    assert left == pytest.approx(8.5 + GRID.gutter_in)
    # The place keeps its margined top, bottom and right: a chart bound to
    # it drops the bleed and stays inside the margins.
    assert (moved.place.x + moved.place.w) * 17 == pytest.approx(14.59)
    assert moved.place.y * 11 == pytest.approx(0.42)
    assert moved.bleed == ("top", "right", "bottom")
    assert straddling(pack.archetypes[0].frames, pack.canvas, GRID) == []


def test_a_line_across_both_halves_goes_to_one_and_stops_short_of_the_edge():
    line = Frame(id="foot", role="column", z=2, place=_frac(1.01, 10.6, 15.99, 10.78))
    pack = seated(_pack_with(*_halves(), line))
    moved = next(f for f in pack.archetypes[0].frames if f.id == "foot")
    # Half on each: the tie goes to the ground painted first, the left.
    assert moved.place.x * 17 == pytest.approx(1.01)
    assert (moved.place.x + moved.place.w) * 17 == pytest.approx(8.5 - GRID.gutter_in)


@pytest.mark.parametrize("pack_id", design_run.available())
def test_no_shipped_pack_inks_a_frame_for_two_grounds(pack_id):
    pack = design_run.load(pack_id)
    for archetype in pack.archetypes:
        found = straddling(archetype.frames, pack.canvas, archetype.grid or pack.grid)
        assert found == [], (archetype.id, [frame.id for frame, _ in found])


# ── inked for the ground it lands on ───────────────────────────────────────


def test_a_series_the_dark_ground_would_swallow_is_lifted_in_its_own_hue():
    lifted = _lifted("#1F4E79", NAVY)
    assert colors.contrast_ratio(lifted, NAVY) >= SERIES_CONTRAST
    # Lighter, not another colour: still bluer than it is red.
    red, _, blue = colors.parse_hex(lifted)
    assert blue > red
    # A series that already reads is left as it is.
    assert _lifted("#F2C14E", NAVY) == "#F2C14E"


def test_on_a_light_ground_a_chart_keeps_its_palette():
    style = resolve_chart_style({})
    assert _adapt_to_ground(style, WHITE).color_sequence == style.color_sequence


def test_a_chart_on_the_navy_half_is_drawn_for_the_navy(monkeypatch):
    """The specimen deck's split-canvas chart: rendered for the panel it sits
    on, and every block compiled for its own ground."""
    import asyncio
    import json

    from app.agent import run_context
    from app.agent.tools import finalize_and_render_pptx as finalize
    from app.renderer import chart_renderer
    from app.renderer.specimen import language_specimen_slides

    grounds = []
    real = chart_renderer.render_chart_to_png

    def spy(*args, **kwargs):
        grounds.append(kwargs.get("ground"))
        return real(*args, **kwargs)

    monkeypatch.setattr(chart_renderer, "render_chart_to_png", spy)
    monkeypatch.setattr(finalize, "PreviewGenerator",
                        type("NoPreviews", (), {"generate_previews": lambda self, *a: []}))
    token = run_context.set_pack(design_run.load("wti-2025"))
    try:
        result = asyncio.run(finalize.FinalizeAndRenderPptxTool.run(
            slides_json=json.dumps(language_specimen_slides()), session_id="grounds"))
    finally:
        run_context.reset_pack(token)
    assert result.success, result.error
    assert NAVY in [ground.upper() for ground in grounds if ground]
