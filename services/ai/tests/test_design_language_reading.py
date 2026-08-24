"""
Reading a publication's type and palette as *roles* rather than as values.

A ramp and a list of hexes are not usable for generation. What a page needs
is which size is the body and which colour is the one saturated mark the
deck allows itself, and both are answerable from counted evidence about how
each is used.

The rules here were each written against a specific wrong reading, and the
tests name them, because every one of them looked obviously right until it
was run against a second publication:

* "the size carrying the most text is the body" reads an investor deck's
  legal disclaimers - 77% of its characters at 7pt, in a block at the foot
  of every page - as its body copy.
* "within one page of the most pages" is far too generous on a short deck: a
  methodology appendix sets 42% of a magazine's characters on one page in
  three.
* sampling colour from the rendered page reads a magenta cover photograph as
  the brand accent and misses the yellow rule the deck actually reserves.
* arithmetic saturation calls (0,1,1) fully saturated, so a near-black rule
  becomes the accent.
* "nearer to the ground than the main ink is" names white text reversed out
  of a dark panel as the muted ink.
"""
import os

import pytest

from app.measure.color_roles import (
    assign_roles as assign_color_roles,
    luminance_of,
    read_colors,
    saturation_of,
)
from app.measure.type_roles import (
    RungEvidence,
    assign_roles,
    clean_face,
    faces_for,
)

REFERENCES = {
    "ey": (
        "~/Downloads/ey-inaugural-ey-iif-global-insurance-risk-management-survey-march23.pdf",
        (4, 8, 12, 16, 20),
        16.0,
    ),
    "bxsl": ("~/Downloads/BXSL-1Q26-Investor-Presentation.pdf",
             (4, 8, 12, 16, 20), 12.0),
    "wti": ("~/Downloads/WTI-2025-04-The-Year-the-Frontier-v13_68535917c7c2a.pdf",
            (4, 8, 12), 10.0),
}


def _rung(size, chars, pages, y=0.5, upper=0.0, lines=1, folio=0, face="Sans"):
    rung = RungEvidence(size_pt=size)
    rung.chars = chars
    rung.occurrences = max(1, lines)
    rung.lines = lines
    rung.pages = set(pages)
    rung.upper_chars = int(chars * upper)
    rung.y_positions = [y]
    rung.folio_like = folio
    rung.fonts[face] = chars
    return rung


class TestTheBodyIsNotJustTheMostText:
    def test_a_disclaimer_block_at_the_foot_is_not_the_body(self):
        """77% of an investor deck's characters are 7pt legal text at the
        bottom of every page. Reading that as body copy sets the entire
        pack's scale to a footnote."""
        rungs = {
            7.0: _rung(7.0, 8678, [1, 2, 3, 4, 5], y=0.87, lines=9),
            12.0: _rung(12.0, 787, [1, 2, 3, 4], y=0.36, lines=2),
        }
        assert assign_roles(rungs)["body"].size_pt == 12.0

    def test_an_appendix_on_one_page_in_three_is_not_the_body(self):
        """A methodology page sets 42% of a magazine's characters at 7pt.
        Allowing the body to be absent from one page in three is enough to
        pick it."""
        rungs = {
            7.0: _rung(7.0, 6689, [12], y=0.52, lines=5),
            10.0: _rung(10.0, 4788, [4, 8, 12], y=0.49, lines=5),
        }
        assert assign_roles(rungs)["body"].size_pt == 10.0

    def test_the_footer_block_is_named_as_a_footnote(self):
        rungs = {
            7.0: _rung(7.0, 8678, [1, 2, 3, 4, 5], y=0.87, lines=9),
            12.0: _rung(12.0, 787, [1, 2, 3, 4], y=0.36, lines=2),
        }
        roles = assign_roles(rungs)
        assert "footnote" in roles and roles["footnote"].size_pt == 7.0


class TestRolesComeFromUse:
    def test_a_number_alone_in_the_margin_is_a_folio_not_a_headline(self):
        rungs = {
            16.0: _rung(16.0, 4000, [1, 2, 3], lines=8),
            36.0: _rung(36.0, 10, [1, 2, 3], y=0.06, folio=3, lines=3),
        }
        roles = assign_roles(rungs)
        assert roles.get("folio") is not None
        assert roles["folio"].size_pt == 36.0
        assert roles.get("headline") is None

    def test_small_capitalised_text_at_the_top_is_an_eyebrow(self):
        rungs = {
            16.0: _rung(16.0, 4000, [1, 2, 3], lines=8),
            11.0: _rung(11.0, 300, [1, 2, 3], y=0.09, upper=0.8, lines=2),
        }
        roles = assign_roles(rungs)
        assert roles.get("eyebrow") is not None
        assert roles["eyebrow"].size_pt == 11.0

    def test_the_display_face_comes_from_the_headline_not_from_a_guess(self):
        rungs = {
            16.0: _rung(16.0, 4000, [1, 2], face="BodySans", lines=8),
            48.0: _rung(48.0, 40, [1, 2], face="DisplaySerif", lines=1),
        }
        body_face, display_face = faces_for(assign_roles(rungs))
        assert body_face == "BodySans"
        assert display_face == "DisplaySerif"


class TestSaturationAndMutedInk:
    def test_a_near_black_is_not_the_most_saturated_colour_in_the_deck(self):
        """(0, 1, 1) is arithmetically fully saturated and visually black."""
        assert saturation_of("#000101") == 0.0
        assert saturation_of("#FFE600") > 0.9

    def test_reversed_text_is_not_named_the_muted_ink(self):
        from app.measure.color_roles import ColorEvidence

        evidence = {
            "#FFFFFF": ColorEvidence("#FFFFFF", ink_chars=400, ground_cells=90.0,
                                     total_cells=100.0),
            "#000000": ColorEvidence("#000000", ink_chars=9000),
            "#595959": ColorEvidence("#595959", ink_chars=120),
        }
        roles = assign_color_roles(evidence)
        assert roles["ink"] == "#000000"
        assert roles.get("ink_muted") == "#595959"
        assert roles.get("ink_reversed") == "#FFFFFF"

    def test_luminance_orders_light_above_dark(self):
        assert luminance_of("#FFFFFF") > luminance_of("#747480") > luminance_of("#000000")


@pytest.mark.skipif(
    not os.path.exists(os.path.expanduser(REFERENCES["ey"][0])),
    reason="reference PDFs not present; see app/eval/corpus.py",
)
class TestAgainstTheRealPublications:
    @pytest.mark.parametrize("name", sorted(REFERENCES))
    def test_the_body_size_matches_the_measured_profile(self, name):
        import fitz

        from app.measure.type_roles import read_rungs

        path, pages, expected = REFERENCES[name]
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            pytest.skip(f"{name} reference not present")
        doc = fitz.open(path)
        roles = assign_roles(read_rungs([(p, doc[p - 1]) for p in pages]))
        assert roles["body"].size_pt == pytest.approx(expected, abs=0.6)

    def test_ey_s_accent_is_its_yellow_and_not_a_photograph(self):
        """Sampling the rendered page reads the magenta cover image as the
        accent. The palette is what the deck *specifies*, not what it shows."""
        import fitz

        path = os.path.expanduser(REFERENCES["ey"][0])
        doc = fitz.open(path)
        evidence, _ = read_colors([(p, doc[p - 1]) for p in REFERENCES["ey"][1]])
        roles = assign_color_roles(evidence)
        assert roles.get("accent") == "#FFE600"
        assert roles.get("ink") == "#2E2E38"

    def test_a_page_that_draws_no_background_still_has_a_ground(self):
        """A white magazine page draws no background rectangle, so a
        fill-only reading promotes a decorative navy panel to the ground."""
        import fitz

        path = os.path.expanduser(REFERENCES["wti"][0])
        if not os.path.exists(path):
            pytest.skip("wti reference not present")
        doc = fitz.open(path)
        evidence, _ = read_colors([(p, doc[p - 1]) for p in REFERENCES["wti"][1]])
        roles = assign_color_roles(evidence)
        assert luminance_of(roles["ground"]) > 0.8


class TestFaceNames:
    @pytest.mark.parametrize("raw,expected", [
        ("ABCDEF+EYInterstate-Light", "EYInterstate"),
        ("GuardianSansTT-Light", "Guardian Sans TT"),
        ("SegoeProCond-Bold", "Segoe Pro Cond"),
        ("Georgia", "Georgia"),
    ])
    def test_a_postscript_name_reduces_to_a_family(self, raw, expected):
        assert clean_face(raw) == expected
