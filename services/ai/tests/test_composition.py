"""
Vertical balance for body text.

Issue #56's G8: body placeholders kept their stock height and their text was
top-anchored, with nothing measuring the block afterwards, so a slide with
two or three bullets left the bottom 40-60% of the page blank. `_split_region_vertically`
already measured text for the shared-region case; the same measurement was
never used to balance a slide that was simply short.
"""


from app.renderer import composition
from app.renderer.composition import balance_body

BAND_W, BAND_H = 11.63, 4.55

SHORT = ["Labour cost inflation", "Slower China recovery"]
FULL = [
    "Revenue reached $36.2B in FY2025, up 3% year over year",
    "North America remains the profit engine at 74% of operating income",
    "China comparable sales returned to growth in the fourth quarter",
    "Operating margin compressed 220 basis points on labour reinvestment",
    "Loyalty membership grew to 34.6 million active members in the US",
    "Cold beverages now account for three quarters of all drinks sold",
    "Store count passed 40,000 for the first time in the company's history",
    "Digital orders reached 31% of US company-operated transactions",
    "The new equipment rollout covers 10,000 stores by the end of FY2026",
    "Cost savings of $500 million were reinvested into store labour hours",
]


def _sizes(texts, size=20.0):
    return [size] * len(texts)


class TestAShortBlockIsBalanced:
    def test_it_is_set_larger(self):
        balance = balance_body(SHORT, _sizes(SHORT), BAND_W, BAND_H)

        assert balance.size_scale > 1.0

    def test_its_points_are_led_apart(self):
        balance = balance_body(SHORT, _sizes(SHORT), BAND_W, BAND_H)

        assert balance.paragraph_gap_pt > 0

    def test_it_starts_below_the_top_of_the_band(self):
        balance = balance_body(SHORT, _sizes(SHORT), BAND_W, BAND_H)

        assert balance.top_offset_in > 0

    def test_more_room_is_left_below_the_block_than_above_it(self):
        """A block centred exactly reads as having floated loose; one hanging
        from the title is what a reader expects."""
        balance = balance_body(SHORT, _sizes(SHORT), BAND_W, BAND_H)

        assert balance.top_offset_in < BAND_H / 2


class TestABlockThatAlreadyFitsIsLeftAlone:
    def test_a_full_slide_is_untouched(self):
        balance = balance_body(FULL, _sizes(FULL), BAND_W, BAND_H)

        assert balance.is_noop

    def test_no_text_is_untouched(self):
        assert balance_body([], [], BAND_W, BAND_H).is_noop

    def test_a_zero_sized_band_is_untouched(self):
        assert balance_body(SHORT, _sizes(SHORT), 0.0, 0.0).is_noop


class TestTheAdjustmentsAreBounded:
    def test_type_never_grows_past_the_cap(self):
        """Body copy that varies wildly slide to slide is its own design
        smell - the deck should read as one type scale."""
        balance = balance_body(["One point"], [20.0], BAND_W, BAND_H)

        assert balance.size_scale <= composition.MAX_GROWTH

    def test_leading_never_grows_past_the_cap(self):
        balance = balance_body(["A", "B"], [20.0, 20.0], BAND_W, BAND_H)

        assert balance.paragraph_gap_pt <= composition.MAX_PARAGRAPH_GAP_PT

    def test_the_balanced_block_still_fits_its_band(self):
        for texts in (SHORT, ["One point"], FULL[:4]):
            balance = balance_body(texts, _sizes(texts), BAND_W, BAND_H)
            grown = composition._measure_in(
                texts, _sizes(texts), BAND_W * 72.0, balance.size_scale
            )
            gaps = balance.paragraph_gap_pt * max(0, len(texts) - 1) / 72.0

            assert balance.top_offset_in + grown + gaps <= BAND_H + 1e-6

    def test_growth_accounts_for_the_rewrap_it_causes(self):
        """Bigger type rewraps, which can add a line and eat the gain - so
        the height has to be re-measured, not scaled linearly."""
        texts = ["A single line that very nearly fills the whole column width here"]
        balance = balance_body(texts, [20.0], 4.0, 1.6)
        grown = composition._measure_in(texts, [20.0], 4.0 * 72.0, balance.size_scale)

        assert grown <= 1.6
