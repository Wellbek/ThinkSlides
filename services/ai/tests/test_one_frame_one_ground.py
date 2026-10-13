"""One frame, one ground (#396).

A frame inked for the ground under it cannot sit across two grounds: on the
WTI split-canvas page a chart straddled the navy panel's edge and was drawn
throughout in the white half's dark ink. Packs are seated as they load, and
the renderer inks each chart and block for the ground under its own frame.
"""
import pytest

from app import colors
from app.design import run as design_run
from app.design.grounds import MIN_KEPT_SHARE, STRADDLE_SHARE, seated, straddling
from app.design.schema import FracPlace, Frame, GridSpec
from app.renderer.chart_renderer import (SERIES_CONTRAST, SERIES_SEPARATION,
                                         _adapt_to_ground, _distance, _series_for)
from app.renderer.table_style import resolve_chart_style

GRID = GridSpec(columns=4, gutter_in=0.33)
WHITE, NAVY = "#FFFFFF", "#2A4570"


@pytest.fixture(scope="module")
def base():
    pack = design_run.load("wti-2025")
    return pack.model_copy(update={"grid": GRID})


def _frac(left, top, right, bottom):
    return FracPlace(x=left / 17, y=top / 11, w=(right - left) / 17, h=(bottom - top) / 11)


def _halves():
    return (
        Frame(id="left", role="panel", place=_frac(0, 0, 8.5, 11), ground=WHITE),
        Frame(id="right", role="panel", place=_frac(8.5, 0, 17, 11), ground=NAVY),
    )


def _ids(found):
    return [straddle.frame.id for straddle in found]


def _seated(base, *frames):
    archetype = base.archetypes[0].model_copy(update={"id": "probe", "frames": frames, "slots": ()})
    pack = seated(base.model_copy(update={"archetypes": (archetype,)}))
    return {frame.id: frame for frame in pack.archetypes[0].frames}


# ── finding a frame on two grounds ─────────────────────────────────────────


def test_a_frame_across_the_panel_edge_mostly_sits_on_the_navy(base):
    figure = Frame(id="figure", role="figure", z=1, place=_frac(7.96, 0.42, 14.59, 10.58))
    (found,) = straddling(_halves() + (figure,), base, GRID)
    assert found.colour == NAVY
    assert found.keep.left == pytest.approx(8.5)


def test_a_hairline_over_the_edge_is_not_a_straddle(base):
    width = 6.0
    over = STRADDLE_SHARE / 2 * width
    figure = Frame(id="figure", role="figure", z=1, place=_frac(8.5 - over, 1, 8.5 - over + width, 10))
    assert straddling(_halves() + (figure,), base, GRID) == []


def test_a_frame_with_its_own_ground_may_cross_the_edge(base):
    """A pull-quote on its own card, laid across the panel edge."""
    card = Frame(id="quote", role="pullquote", z=1, ground="#F4F1EA", place=_frac(6, 4, 11, 7))
    assert straddling(_halves() + (card,), base, GRID) == []


def test_a_scrim_is_not_a_ground_of_its_own(base):
    """A wash at under half opacity: white and navy still differ beneath it."""
    from app.design.schema import Scrim

    washed = Frame(id="caption", role="caption", z=1, scrim=Scrim(), place=_frac(6, 8, 11, 9))
    assert _ids(straddling(_halves() + (washed,), base, GRID)) == ["caption"]


def test_what_counts_is_the_ground_that_shows(base):
    """A navy panel stacked on a full-page white one hides it: a figure wholly
    on the navy is on one ground."""
    frames = (Frame(id="page", role="panel", place=_frac(0, 0, 17, 11), ground=WHITE, z=0),
              Frame(id="navy", role="panel", place=_frac(8.5, 0, 17, 11), ground=NAVY, z=1),
              Frame(id="figure", role="figure", z=2, place=_frac(9, 1, 16, 10)))
    assert straddling(frames, base, GRID) == []


def test_the_bare_page_is_a_ground(base):
    """No white panel is painted: the page's own ground shows beside the navy."""
    frames = (Frame(id="navy", role="panel", place=_frac(8.5, 0, 17, 11), ground=NAVY),
              Frame(id="figure", role="figure", z=1, place=_frac(4, 1, 13, 10)))
    assert _ids(straddling(frames, base, GRID)) == ["figure"]


def test_a_role_and_the_hex_it_names_are_one_ground(base):
    accent = base.colors.accent
    frames = (Frame(id="a", role="panel", place=_frac(0, 0, 8.5, 11), ground="accent"),
              Frame(id="b", role="panel", place=_frac(8.5, 0, 17, 11), ground=accent),
              Frame(id="figure", role="figure", z=1, place=_frac(4, 1, 13, 10)))
    assert straddling(frames, base, GRID) == []


def test_a_bleed_that_alone_reaches_another_ground_is_cut(base):
    """Placed on the navy, the frame reaches the bare page only by bleeding
    off the left edge: the bleed goes, and it is set a gutter in."""
    frames = (Frame(id="navy", role="panel", place=_frac(1.0, 0, 17, 11), ground=NAVY),
              Frame(id="figure", role="figure", z=1, bleed=("left",),
                    place=_frac(1, 1, 10, 10)))
    pack_frames = _seated(base, *frames)
    figure = pack_frames["figure"]
    assert figure.bleed == ()
    assert figure.place.x * 17 == pytest.approx(1.0 + GRID.gutter_in)
    assert straddling(tuple(pack_frames.values()), base, GRID) == []


# ── seating ────────────────────────────────────────────────────────────────


def test_seating_cuts_to_the_ground_a_gutter_in_and_keeps_the_bleed_it_was_not_cut_at(base):
    figure = Frame(id="figure", role="figure", z=1, bleed=("top", "right", "bottom"),
                   place=_frac(7.96, 0.42, 14.59, 10.58))
    frames = _seated(base, *_halves(), figure)
    moved = frames["figure"]
    assert moved.place.x * 17 == pytest.approx(8.5 + GRID.gutter_in)
    # The place keeps its margined top, bottom and right: a chart bound to
    # it drops the bleed and stays inside the margins.
    assert (moved.place.x + moved.place.w) * 17 == pytest.approx(14.59)
    assert moved.place.y * 11 == pytest.approx(0.42)
    assert moved.bleed == ("top", "right", "bottom")
    assert straddling(tuple(frames.values()), base, GRID) == []


def test_a_line_across_both_halves_goes_to_the_half_met_first(base):
    line = Frame(id="foot", role="column", z=2, place=_frac(1.01, 10.6, 15.99, 10.78))
    moved = _seated(base, *_halves(), line)["foot"]
    assert moved.place.x * 17 == pytest.approx(1.01)
    assert (moved.place.x + moved.place.w) * 17 == pytest.approx(8.5 - GRID.gutter_in)


def test_a_seat_that_would_leave_a_sliver_is_refused(base):
    """Stripes: the frame is mostly on navy, but no one piece of navy holds
    more than a fifth of it."""
    stripes = tuple(Frame(id=f"s{i}", role="panel", place=_frac(i * 3.4, 0, (i + 1) * 3.4, 11),
                          ground=NAVY if i % 2 == 0 else WHITE) for i in range(5))
    figure = Frame(id="figure", role="figure", z=1, place=_frac(0, 1, 17, 10))
    (found,) = straddling(stripes + (figure,), base, GRID)
    assert found.colour == NAVY and found.kept_share < MIN_KEPT_SHARE
    assert _seated(base, *stripes, figure)["figure"].place == figure.place


@pytest.mark.parametrize("pack_id", design_run.available())
def test_no_shipped_pack_inks_a_frame_for_two_grounds(pack_id):
    pack = design_run.load(pack_id)
    for archetype in pack.archetypes:
        found = straddling(archetype.frames, pack, archetype.grid or pack.grid)
        assert found == [], (archetype.id, _ids(found))


# ── inked for the ground it lands on ───────────────────────────────────────


def test_a_series_the_dark_ground_would_swallow_is_lifted_in_its_own_hue():
    (lifted,) = _series_for(["#1F4E79"], NAVY)
    assert colors.contrast_ratio(lifted, NAVY) >= SERIES_CONTRAST
    red, _, blue = colors.parse_hex(lifted)
    assert blue > red
    # A series that already reads is left as it is.
    assert _series_for(["#F2C14E"], NAVY) == ["#F2C14E"]


def test_dark_series_lifted_together_stay_apart():
    lifted = _series_for(["#1F2A44", "#1F3044", "#22283F"], NAVY)
    assert all(colors.contrast_ratio(colour, NAVY) >= SERIES_CONTRAST for colour in lifted)
    for i, first in enumerate(lifted):
        for second in lifted[i + 1:]:
            assert _distance(first, second) >= SERIES_SEPARATION


def test_on_a_light_ground_the_decks_palette_is_its_own():
    """Chosen for that ground: a brand's coral on white is left as it is."""
    style = resolve_chart_style({})
    assert _adapt_to_ground(style, WHITE).color_sequence == style.color_sequence


def test_a_chart_on_the_navy_half_is_drawn_for_the_navy(monkeypatch):
    """The specimen deck's split-canvas chart: rendered for the panel it sits on."""
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
