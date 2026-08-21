"""
One unusable direction must not cost the user the other two.

Observed on a live run about vertical farming in Europe: the model
authored three topical directions, one of them set white body text on
``#FF5733`` for a contrast of 3.2:1 against the 4.5:1 WCAG AA floor, and
the whole set went in the bin - the two that passed, the palettes, and
the specimen copy written about the subject. What the user saw was the
built-in three in the house navy, previewed with generic copy, which is
what "it's always the same 3 suggestions" meant.
"""

from app import colors
from app.directions import (
    DIRECTION_COUNT, DirectionSpec, MIN_CONTRAST, contrast_problems,
    default_specs, repair_contrast, top_up, validate,
)


def _spec(**kwargs) -> DirectionSpec:
    base = dict(id="d", name="D", rationale="why", ground="light",
                structure="list")
    base.update(kwargs)
    return DirectionSpec(**base)


def test_an_unreadable_hue_is_repaired_rather_than_rejected():
    spec = _spec(id="vibrant-split", name="Vibrant split", ground="split",
                 structure="cards", palette_hex="#FF5733")
    compiled, problems = validate([spec])

    assert [p for p in problems if "unreadable" in p] == []
    assert len(compiled) == 1
    assert not contrast_problems(compiled[0]["designSystem"])


def test_a_repair_keeps_the_hue_the_model_chose():
    spec = _spec(ground="split", structure="cards", palette_hex="#FF5733")
    repaired, system = repair_contrast(spec)

    assert system is not None
    original = colors._to_hls("#FF5733")
    fixed = colors._to_hls(repaired.palette_hex)
    assert original is not None and fixed is not None
    # Same hue, different lightness: a repair, not a substitution.
    assert abs(original[0] - fixed[0]) < 0.02
    assert repaired.palette_hex != "#FF5733"


def test_a_repair_moves_as_little_as_it_can():
    spec = _spec(ground="split", structure="cards", palette_hex="#FF5733")
    repaired, _ = repair_contrast(spec)
    original = colors._to_hls("#FF5733")
    fixed = colors._to_hls(repaired.palette_hex)
    # Well inside the 0.40 cap - the first lightness that clears the floor
    # is taken, not the darkest one available.
    assert abs(original[1] - fixed[1]) <= 0.2


def test_a_direction_with_no_hue_of_its_own_cannot_be_repaired():
    # Nothing to move: it draws from the built-in palette, so an unreadable
    # one is a bug in the defaults rather than in the model's choice.
    assert repair_contrast(_spec(palette_hex="")) == (None, None)


def test_a_short_set_keeps_what_was_authored_and_is_topped_up():
    authored, _ = validate([
        _spec(id="forest", name="Forest", ground="dark", structure="bands",
              palette_hex="#1B4332"),
    ])
    assert len(authored) == 1

    filled = top_up(authored, DIRECTION_COUNT)
    assert len(filled) == DIRECTION_COUNT
    # The authored direction leads and survives intact.
    assert filled[0]["id"] == "forest"
    assert filled[0]["designSystem"]["palette"]["colors"] \
        == authored[0]["designSystem"]["palette"]["colors"]


def test_topping_up_never_repeats_a_ground():
    authored, _ = validate([
        _spec(id="forest", name="Forest", ground="dark", structure="bands",
              palette_hex="#1B4332"),
    ])
    grounds = [(d["spec"] or {}).get("ground") for d in top_up(authored, DIRECTION_COUNT)]
    assert len(set(grounds)) == len(grounds)


def test_two_directions_on_one_ground_do_not_both_survive():
    # distinctness_problems reports this; top_up is where the floor is
    # actually applied, since a reported problem the caller then ignores is
    # two thumbnails a user cannot tell apart.
    both, _ = validate([
        _spec(id="a", name="A", ground="dark", structure="bands", palette_hex="#1B4332"),
        _spec(id="b", name="B", ground="dark", structure="cards", palette_hex="#8B1E3F"),
    ])
    assert len(both) == 2
    filled = top_up(both, DIRECTION_COUNT)
    grounds = [(d["spec"] or {}).get("ground") for d in filled]
    assert len(set(grounds)) == DIRECTION_COUNT


def test_a_filler_takes_a_proposed_hue_rather_than_the_house_default():
    authored, _ = validate([
        _spec(id="forest", name="Forest", ground="dark", structure="bands",
              palette_hex="#1B4332"),
    ])
    filled = top_up(authored, DIRECTION_COUNT, seeds=["#6A4C93", "#8B1E3F"])
    seeded = [(d["spec"] or {}).get("paletteHex") for d in filled[1:]]
    assert seeded == ["#6A4C93", "#8B1E3F"]


def test_a_settled_brand_colour_still_wins_over_a_proposed_one():
    filled = top_up([], DIRECTION_COUNT, brand_hex="#1B4332", seeds=["#6A4C93"])
    assert len(filled) == DIRECTION_COUNT
    for direction in filled:
        assert (direction["spec"] or {}).get("paletteHex") in ("", None)


def test_every_topped_up_direction_still_clears_the_contrast_floor():
    for direction in top_up([], DIRECTION_COUNT, seeds=["#FF5733", "#FFD54F"]):
        assert not contrast_problems(direction["designSystem"])


def test_the_defaults_themselves_are_still_readable():
    for spec in default_specs():
        problems = contrast_problems(
            validate([spec])[0][0]["designSystem"]
        )
        assert problems == [], f"{spec.name}: {problems}"


def test_min_contrast_is_wcag_aa():
    assert MIN_CONTRAST == 4.5
