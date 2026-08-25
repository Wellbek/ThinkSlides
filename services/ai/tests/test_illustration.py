"""
One illustration style, read by every path that puts an image on a page.

Illustration was decided per slide: `generate_image` composed a prompt from
the run's design direction, `select_source_image` chose from a catalogue with
no style at all, and an MCP server returned whatever it returned. Two pages of
one deck could come back in unrelated treatments and nothing said they should
not - a deck's imagery is as much part of how it reads as its palette, and the
palette has lived in the pack since the beginning.

The property that keeps this honest is the same one the grid, the rhythm and
the spacing ladder follow: a publication that shows us almost no imagery is
telling us it has no illustration system worth reproducing, and a treatment
recovered from one photograph is a treatment invented.
"""
import pytest

from app.design import run
from app.measure.illustration import ImageReading, read_image, recover
from app.renderer import illustration as ill


class TestATreatmentIsReadFromTheImageryOrNotAtAll:
    def test_near_grey_imagery_reads_as_monochrome(self):
        reading = recover([ImageReading(0.03, 0, False) for _ in range(6)])
        assert reading.treatment == "mono"

    def test_two_hues_at_low_saturation_read_as_duotone(self):
        reading = recover([ImageReading(0.18, 2, False) for _ in range(6)])
        assert reading.treatment == "duotone"

    def test_full_colour_photography_reads_as_itself(self):
        reading = recover([ImageReading(0.70, 5, False) for _ in range(6)])
        assert reading.treatment == "full"

    def test_bleeding_is_the_house_rule_only_when_most_images_bleed(self):
        mostly = [ImageReading(0.5, 4, i < 5) for i in range(6)]
        rarely = [ImageReading(0.5, 4, i < 1) for i in range(6)]
        assert recover(mostly).placement == "bleed"
        assert recover(rarely).placement == "inset"

    def test_too_few_images_is_reported_rather_than_answered(self):
        """A treatment recovered from three pixels is a treatment invented."""
        reading = recover([ImageReading(0.03, 0, True), ImageReading(0.02, 0, True)])
        assert reading.confidence == 0.0
        assert "too few" in reading.note

    def test_imagery_that_disagrees_with_itself_is_unconfident(self):
        """A publication whose images scatter across every treatment has no
        treatment, however many of them there are."""
        scattered = recover([ImageReading(v, 3, False)
                             for v in (0.02, 0.75, 0.1, 0.9, 0.3, 0.6)])
        consistent = recover([ImageReading(0.5, 3, False) for _ in range(6)])
        assert scattered.confidence < consistent.confidence

    def test_a_sampled_image_is_read_from_its_pixels(self):
        grey = read_image([(120, 122, 121)] * 20, bleeds=False)
        colour = read_image([(220, 30, 30)] * 20, bleeds=True)
        assert grey.saturation < colour.saturation
        assert colour.bleeds

    def test_an_image_that_would_not_open_reads_as_nothing(self):
        assert read_image([], bleeds=False).saturation == 0.0


class TestEveryShippedPackDeclaresWhatItMeasured:
    @pytest.fixture(params=run.available())
    def pack(self, request):
        return run.load(request.param)

    def test_the_pack_carries_an_illustration_block(self, pack):
        assert pack.illustration is not None
        assert pack.illustration.treatment in ("full", "duotone", "mono", "muted")

    def test_a_confident_reading_says_what_it_saw(self, pack):
        note = pack.illustration.confidence.note
        assert note

    def test_a_pack_with_no_imagery_refuses_rather_than_inventing(self):
        """BXSL is an investor deck: it has charts, not photographs."""
        pack = run.load("bxsl-investor")
        assert pack.illustration.confidence.value == 0.0
        assert "too few" in pack.illustration.confidence.note

    def test_a_publication_with_a_real_treatment_records_it(self):
        """WTI is the magazine register - full-bleed monochrome imagery."""
        pack = run.load("wti-2025")
        assert pack.illustration.treatment == "mono"
        assert pack.illustration.placement == "bleed"
        assert pack.illustration.confidence.value > 0.5


class TestOnePlaceAnswersWhatImageryLooksLike:
    def test_a_measured_pack_supplies_the_style(self):
        style = ill.style_for(run.load("wti-2025"))
        assert style.measured
        assert "monochrome" in style.for_provider()
        assert "bleeding" in style.for_provider()

    def test_an_unmeasured_pack_falls_back_to_the_chosen_direction(self):
        """Rather than to a treatment nobody measured or asked for."""
        style = ill.style_for(run.load("bxsl-investor"),
                              {"direction": {"ground": "dark"}})
        assert not style.measured
        assert style.treatment == "mono"

    def test_the_decks_palette_reaches_the_provider(self):
        style = ill.style_for(run.load("wti-2025"),
                              {"palette": {"colors": [{"value": "#1E8449"}]}})
        assert "#1E8449" in style.for_provider()

    def test_the_sentence_is_composed_once_not_per_call(self):
        """Each call assembling its own from whatever it had to hand is how
        two pages of one deck came back in unrelated treatments."""
        style = ill.style_for(run.load("wti-2025"))
        assert style.for_provider() == style.for_provider()

    def test_a_pack_note_does_not_repeat_the_treatment(self):
        """It produced "desaturated, muted colour, inset within the page's
        margins, desaturated, muted colour, inset within the page's margins"."""
        for pack_id in run.available():
            sentence = ill.style_for(run.load(pack_id)).for_provider()
            assert sentence.count("inset within") <= 1
            assert sentence.count("monochrome") <= 1

    def test_the_image_generator_reads_it(self):
        import inspect

        from app.agent.tools import generate_image

        source = inspect.getsource(generate_image.compose_prompt)
        assert "illustration" in source


class TestConsistencyIsANumberThatCanFail:
    def test_a_deck_in_one_hand_passes(self):
        style = ill.style_for(run.load("wti-2025"))
        result = ill.measure([0.02, 0.03, 0.01, 0.04], style)
        assert result.consistent
        assert result.images == 4

    def test_one_page_duotone_and_the_next_full_colour_fails(self):
        """Which reads as two decks stapled together."""
        style = ill.style_for(run.load("wti-2025"))
        assert not ill.measure([0.02, 0.7, 0.05, 0.65], style).consistent

    def test_a_deck_consistently_not_what_it_said_fails(self):
        """Drift and spread are two different failures."""
        style = ill.style_for(run.load("wti-2025"))
        result = ill.measure([0.72, 0.70, 0.71, 0.73], style)
        assert not result.consistent
        assert result.spread < ill.MAX_SPREAD
        assert result.drift > ill.MAX_DRIFT

    def test_a_deck_with_no_imagery_is_not_a_failure(self):
        style = ill.style_for(run.load("wti-2025"))
        result = ill.measure([], style)
        assert result.consistent
        assert "no imagery" in result.note

    def test_the_measurement_states_what_it_found(self):
        style = ill.style_for(run.load("wti-2025"))
        note = ill.measure([0.02, 0.03], style).note
        assert "images" in note and "apart from each other" in note
