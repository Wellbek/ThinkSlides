"""
Measuring text the way the renderer draws it.

The estimator this replaces multiplied character count by a fixed 0.52 of
the point size. That is not merely imprecise, it is imprecise *unevenly*:
"Illinois" and "WMWMWM" are the same number of characters and nearly twice
apart in width, so narrow copy was reported as needing more room than it
does and wide copy less. Fitting, overflow detection and the density budget
all read that number.

Against 128 blocks rendered through the same LibreOffice path the previews
use (``tools/measure_typesetting.py``), the character estimate got 49.2% of
line counts exactly right and was out by 0.70 lines on average. Reading the
font's own advance widths is exact on all 128.
"""
import pytest

from app.renderer import text_fit
from app.renderer.typeset.fonts import metrics_for, resolve_path, width_pt
from app.renderer.typeset.shape import layout, wrap

pytestmark = pytest.mark.skipif(
    resolve_path("Arial") is None,
    reason="no fonts resolvable on this machine",
)


class TestRealAdvanceWidths:
    def test_narrow_and_wide_strings_of_equal_length_differ(self):
        """The whole failure of a character-count estimate, in one assertion."""
        narrow = width_pt("lllllllll", "Arial", 20.0)
        wide = width_pt("WWWWWWWWW", "Arial", 20.0)
        assert wide > narrow * 2.5

    def test_width_scales_with_size(self):
        assert width_pt("hello", "Arial", 24.0) == pytest.approx(
            2 * width_pt("hello", "Arial", 12.0), rel=1e-6
        )

    def test_bold_is_wider_than_regular(self):
        """Bold Liberation Sans is about 5% wider, which is the difference
        between a headline that sets on one line and one that sets on two -
        and measuring the regular weight for a bold headline was exactly how
        a title ended up rendering off the top of a slide."""
        regular = metrics_for("Arial", bold=False)
        bold = metrics_for("Arial", bold=True)
        assert regular and bold
        text = "What disruptions could the trend enable?"
        assert bold.width_pt(text, 37.7) > regular.width_pt(text, 37.7)

    def test_an_unresolvable_family_degrades_rather_than_raises(self):
        """A missing font should cost accuracy, not fail the render."""
        assert width_pt("hello", "NoSuchFontAnywhere", 12.0) > 0


class TestWrapping:
    def test_it_breaks_where_the_text_no_longer_fits(self):
        lines = wrap("one two three four five six seven eight", 60.0,
                     "Arial", 12.0)
        assert len(lines) > 1
        for line in lines:
            assert width_pt(line, "Arial", 12.0) <= 60.5

    def test_a_word_wider_than_its_column_breaks_inside_itself(self):
        """Both renderers do this. Reporting such a word as one line and
        drawing it as three is how a block that measured as fitting lands on
        whatever was placed beneath it."""
        lines = wrap("unprecedented", 40.0, "Arial", 24.0)
        assert len(lines) > 1
        assert "".join(lines) == "unprecedented"

    def test_a_trailing_space_does_not_wrap_the_line_early(self):
        text = "aaa bbb"
        exact = width_pt(text, "Arial", 12.0)
        assert wrap(text, exact + 0.5, "Arial", 12.0) == [text]

    def test_explicit_newlines_are_honoured(self):
        assert len(wrap("first\nsecond\nthird", 500.0, "Arial", 12.0)) == 3


class TestLayoutReportsOverflowRatherThanAbsorbingIt:
    def test_it_says_by_how_much_the_text_does_not_fit(self):
        result = layout("word " * 200, 200.0, 40.0, "Arial", 14.0)
        assert not result.fits
        assert result.overflow_pt > 0

    def test_text_that_fits_reports_no_overflow(self):
        result = layout("short", 400.0, 200.0, "Arial", 14.0)
        assert result.fits
        assert result.overflow_pt == 0

    def test_lines_carry_their_own_baselines(self):
        result = layout("one two three four five", 60.0, 300.0, "Arial", 12.0,
                        line_height_pt=20.0)
        baselines = [line.baseline_pt for line in result.lines]
        assert len(baselines) > 1
        gaps = [b - a for a, b in zip(baselines, baselines[1:])]
        assert all(gap == pytest.approx(20.0) for gap in gaps)


class TestTheOldApiStillWorks:
    """Callers were not asked to change; only the internals were replaced."""

    def test_estimate_wrapped_lines_agrees_with_the_shaper(self):
        text = "The results of our inaugural survey demonstrate the risks."
        assert text_fit.estimate_wrapped_lines(text, 12.0, 200.0) == len(
            wrap(text, 200.0, text_fit.DEFAULT_MEASURE_FAMILY, 12.0)
        )

    def test_the_character_count_fallback_is_still_there(self):
        """It is what a machine with no resolvable fonts falls back to."""
        assert text_fit._estimate_wrapped_lines_by_char_count(
            "a b c d e f g h", 12.0, 40.0
        ) > 1

    def test_blocks_carry_their_face_and_weight(self):
        wide = text_fit.TextBlock("WWWWWWWWWWWW", 20.0, "Arial", True)
        narrow = text_fit.TextBlock("llllllllllll", 20.0, "Arial", False)
        assert text_fit.estimate_blocks_height_pt([wide], 100.0) >= \
            text_fit.estimate_blocks_height_pt([narrow], 100.0)


class TestLegibilityIsAPropertyOfThePack:
    """The global MIN_BODY_PT = 16 was above the body size of every one of
    the four publications the system is measured against. It did not enforce
    legibility - it forbade the registers being aimed at."""

    def _pack(self, pack_id: str):
        import json
        import os

        from app.design.pack import Pack

        path = os.path.join("app", "design", "packs", f"{pack_id}.json")
        with open(path) as handle:
            return Pack.model_validate(json.load(handle))

    @pytest.mark.parametrize("pack_id", ("ey-iif-risk", "bxsl-investor",
                                         "wti-2025"))
    def test_each_pack_permits_its_own_body_copy(self, pack_id):
        from app.renderer.typeset.policy import check

        pack = self._pack(pack_id)
        assert check(pack, "body", pack.role("body").size_pt) is None

    def test_the_old_global_floor_would_have_refused_two_of_them(self):
        for pack_id in ("bxsl-investor", "wti-2025"):
            assert self._pack(pack_id).role("body").size_pt < 16.0

    def test_a_size_below_the_floor_is_explained_not_just_rejected(self):
        from app.renderer.typeset.policy import check

        pack = self._pack("ey-iif-risk")
        message = check(pack, "body", 4.0)
        assert message and "below this pack's floor" in message

    def test_floors_differ_between_packs(self):
        from app.renderer.typeset.policy import floor_for

        floors = {
            pack_id: floor_for(self._pack(pack_id), "body")
            for pack_id in ("ey-iif-risk", "bxsl-investor", "wti-2025")
        }
        assert len(set(round(f, 1) for f in floors.values())) > 1
