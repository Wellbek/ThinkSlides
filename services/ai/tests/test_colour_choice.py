"""
A colour the user asked for is a colour the deck comes back in.

Two runs on unrelated subjects - speciality coffee, and the Green Climate
Fund - both offered exactly one alternative colour, and it was the same
one: ``Forest green (#1B4332)``, the literal value in the prompt's own
example. And typing a colour into the free-text box did nothing at all,
because only ``#RRGGBB`` was ever read out of the reply.
"""

import pytest

from app import color_words, colors
from app.agent.tools import propose_design_directions as tool
from app.design_system import derive_brand_palette
from app.directions import DirectionSpec, contrast_problems, recolour, validate


def _direction(hex_value: str, ground: str = "light") -> dict:
    compiled, _ = validate([DirectionSpec(
        id="d", name="D", rationale="why", ground=ground,
        structure="list", palette_hex=hex_value,
    )])
    return compiled[0]


def _slot(direction: dict, name: str) -> str:
    for entry in direction["designSystem"]["palette"]["colors"]:
        if entry["name"] == name:
            return entry["value"].upper()
    raise AssertionError(f"no {name} slot")


# ── reading what the user typed ────────────────────────────────────────

def test_a_colour_named_in_words_is_a_colour():
    request = color_words.parse("purple")
    assert request and request.base == color_words.NAMED_COLOURS["purple"]
    assert request.accent is None


def test_an_accent_is_kept_apart_from_the_ground():
    request = color_words.parse("White with slight pink accents")
    assert request
    assert request.base == "#FFFFFF"
    assert request.accent and request.accent != "#FFFFFF"
    # "slight" lightens it - the accent is a blush, not a hot pink.
    assert colors._to_hls(request.accent)[1] > colors._to_hls("#E86A92")[1]


def test_a_two_colour_phrase_gives_the_second_the_smaller_role():
    request = color_words.parse("navy with gold accents")
    assert request.base == color_words.NAMED_COLOURS["navy"]
    assert request.accent == color_words.NAMED_COLOURS["gold"]


def test_a_picked_swatch_still_wins_outright():
    request = color_words.parse("Forest green (#1B4332)")
    assert request.base == "#1B4332"


def test_answering_as_proposed_changes_nothing():
    assert color_words.parse("As proposed - the colours in the previews") is None


def test_a_reply_naming_no_colour_changes_nothing():
    assert color_words.parse("whatever you think best") is None


@pytest.mark.parametrize("word,expected_darker", [("dark teal", True), ("pale teal", False)])
def test_a_modifier_moves_the_colour_it_belongs_to(word, expected_darker):
    plain = colors._to_hls(color_words.NAMED_COLOURS["teal"])[1]
    got = colors._to_hls(color_words.parse(word).base)[1]
    assert (got < plain) is expected_darker


def test_a_modifier_two_clauses_away_is_not_applied():
    # "dark slides with a green rule" is not a request for dark green.
    request = color_words.parse("dark slides with a green rule")
    assert request.base == color_words.NAMED_COLOURS["green"]


# ── applying it ────────────────────────────────────────────────────────

def test_purple_produces_a_purple_deck():
    recoloured = recolour(_direction("#1B4332"), color_words.NAMED_COLOURS["purple"])
    hue = colors._to_hls(_slot(recoloured, "primary"))[0]
    assert abs(hue - colors._to_hls(color_words.NAMED_COLOURS["purple"])[0]) < 0.03


def test_white_with_pink_accents_is_a_light_deck_with_pink_highlights():
    request = color_words.parse("White with slight pink accents")
    recoloured = recolour(_direction("#1B4332", ground="dark"),
                          request.base, accent_hex=request.accent)

    assert _slot(recoloured, "WHITE".lower()) == "#FFFFFF"
    assert _slot(recoloured, "accent") == request.accent.upper()
    # The ink is dark enough to read, and the deck is not repainted pink.
    assert colors._to_hls(_slot(recoloured, "primary"))[1] < 0.3
    # And the ground follows the answer rather than the direction chosen
    # before the answer existed.
    assert recoloured["designSystem"]["ground"] == "light"
    assert contrast_problems(recoloured["designSystem"]) == []


def test_an_accent_alone_leaves_the_ground_where_it_was():
    original = _direction("#14213D", ground="dark")
    recoloured = recolour(original, None, accent_hex="#D4AF37")
    assert _slot(recoloured, "accent") == "#D4AF37"
    assert _slot(recoloured, "primary") == _slot(original, "primary")


def test_an_accent_drives_the_second_chart_series_too():
    palette = {e["name"]: e["value"] for e in derive_brand_palette("#14213D", "#D4AF37")}
    gold_hue = colors._to_hls("#D4AF37")[0]
    assert abs(colors._to_hls(palette["secondary"])[0] - gold_hue) < 0.05


def test_an_uploaded_template_is_never_repainted():
    # No spec means the palette is the user's own, and not ours to overwrite.
    template = {"id": "your-template", "name": "Your template", "designSystem": {}}
    assert recolour(template, "#6B21A8") is template


def test_a_reply_with_no_colour_in_it_leaves_the_direction_alone():
    original = _direction("#14213D")
    assert recolour(original, None, accent_hex=None) is original


def test_a_light_brand_stays_readable_after_recolouring():
    # The theme darkens a title that could not be read on its own ground,
    # so even a pale yellow answer produces a deck rather than a blank page.
    recoloured = recolour(_direction("#14213D"), "#FFF176")
    assert contrast_problems(recoloured["designSystem"]) == []


# ── offering the choice ────────────────────────────────────────────────

def test_the_colour_question_always_offers_three():
    proposed = [_direction(h) for h in ("#1B4332", "#14213D", "#6B21A8")]
    for direction, spec_hex in zip(proposed, ("#1B4332", "#14213D", "#6B21A8")):
        direction["spec"]["paletteHex"] = spec_hex
    topped = tool._top_up_palettes([], proposed)
    assert len(topped) == 3
    assert len({hex_value for _, hex_value in topped}) == 3


def test_a_topped_up_option_is_named_not_numbered():
    proposed = [_direction("#0F766E")]
    proposed[0]["spec"]["paletteHex"] = "#0F766E"
    (name, hex_value), = tool._top_up_palettes([], proposed)
    assert name.lower() == "teal"
    assert hex_value == "#0F766E"


def test_what_the_model_proposed_is_not_displaced_by_the_top_up():
    proposed = [_direction("#0F766E")]
    proposed[0]["spec"]["paletteHex"] = "#0F766E"
    topped = tool._top_up_palettes([("GCF blue", "#2A6EBB")], proposed)
    assert topped[0] == ("GCF blue", "#2A6EBB")


def test_the_prompt_offers_no_value_a_model_could_copy():
    prompt = tool._prompt("a deck about anything", "", None, None, 3, [])
    # The hex and the direction ids two runs came back with, verbatim.
    for copied in ("#1B4332", "Forest green", "dark-analyst", "Dark analyst"):
        assert copied not in prompt
