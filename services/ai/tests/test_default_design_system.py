"""
Tests for the unified default design system.

These guard against the drift described in issue #24: the LLM-facing design
dict (``get_default_design_system``) and the rendered fallback PPTX
(``DefaultTemplateGenerator``) must agree on dimensions and palette, because
``DefaultTemplateGenerator`` now derives its tokens from that dict.
"""

import os
import tempfile

import pytest
from pptx import Presentation
from pptx.util import Emu

from app.design_system import get_default_design_system
from app.renderer.default_template import DefaultTemplateGenerator, _hex_to_rgbcolor


def test_generator_dimensions_match_design_system():
    """The generated PPTX dimensions equal the design system's slideDimensions."""
    width_emu, height_emu = DefaultTemplateGenerator._dimensions()
    dims = get_default_design_system()["slideDimensions"]

    assert width_emu == dims["widthEmu"]
    assert height_emu == dims["heightEmu"]
    # 16:9 sanity check: 10in x 5.625in
    assert width_emu == Emu(9144000)
    assert height_emu == Emu(5143500)


def test_generator_palette_matches_design_system():
    """Every palette color in the dict is present as an RGBColor with matching RGB."""
    palette_rgb = DefaultTemplateGenerator._palette_rgb()
    palette = get_default_design_system()["palette"]["colors"]

    assert palette_rgb["white"] == _hex_to_rgbcolor("#FFFFFF")
    for entry in palette:
        name = entry["name"]
        assert name in palette_rgb, f"palette color '{name}' missing from generator"
        assert palette_rgb[name] == _hex_to_rgbcolor(entry["value"])


def test_generated_template_has_design_system_dimensions():
    """The saved PPTX file actually carries the design system dimensions."""
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        output_path = f.name
    try:
        DefaultTemplateGenerator.generate(output_path)
        prs = Presentation(output_path)
        dims = get_default_design_system()["slideDimensions"]
        assert prs.slide_width == dims["widthEmu"]
        assert prs.slide_height == dims["heightEmu"]
    finally:
        os.unlink(output_path)


def test_generated_template_is_valid_pptx():
    """The generated default template opens cleanly with python-pptx."""
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        output_path = f.name
    try:
        DefaultTemplateGenerator.generate(output_path)
        assert os.path.getsize(output_path) > 0
        prs = Presentation(output_path)
        # No slides until the renderer adds them, but layouts must exist.
        assert len(prs.slide_layouts) > 0
    finally:
        os.unlink(output_path)


def test_default_design_system_schema_is_complete():
    """The canonical dict exposes every key downstream code relies on."""
    ds = get_default_design_system()
    for key in (
        "palette",
        "typography",
        "slideDimensions",
        "layouts",
        "spacing",
        "masterElements",
        "embeddedMedia",
        "sourceType",
    ):
        assert key in ds, f"design system missing key '{key}'"
    assert ds["sourceType"] == "default"
    assert len(ds["layouts"]) == 6
    assert any(c["name"] == "white" for c in ds["palette"]["colors"])


def test_hex_to_rgbcolor_round_trip():
    """Hex conversion matches python-pptx's own RGBColor parsing."""
    assert _hex_to_rgbcolor("#1F4E78") == _hex_to_rgbcolor("1F4E78")
    assert str(_hex_to_rgbcolor("#FFFFFF")).upper() == "FFFFFF"


def test_hex_to_rgbcolor_rejects_invalid():
    """Malformed hex raises rather than producing a wrong color."""
    with pytest.raises(ValueError):
        _hex_to_rgbcolor("nope")
    with pytest.raises(ValueError):
        _hex_to_rgbcolor("#123")
