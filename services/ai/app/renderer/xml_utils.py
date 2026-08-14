"""
XML utility functions for the PPTX renderer.

Handles namespace-aware XML manipulation while preserving
OOXML formatting and structure.
"""

from __future__ import annotations
import xml.etree.ElementTree as ET
from typing import Optional

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


def describe_picture(picture, description: str) -> None:
    """
    Give a placed picture its own description, replacing python-pptx's.

    ``add_picture`` takes a path, and python-pptx writes that path into the
    shape's ``descr`` attribute. Every chart and every image this renderer
    places comes from a ``NamedTemporaryFile``, so each shipped deck carried
    strings like ``descr="tmp3nuj3me1.png"`` - a local temp filename, in the
    file the user downloads, and different on every render of the same deck.
    That made the renderer's own output non-deterministic, which is why this
    surfaced: the region work needs "the same slide JSON renders the same
    file" to be true before it can claim to have changed nothing.

    The attribute is also the accessibility description a screen reader
    reads, so it is set to the content's alt text where there is one and
    removed where there is not.
    """
    try:
        cNvPr = picture._element.nvPicPr.cNvPr
    except AttributeError:                                # pragma: no cover
        return
    text = (description or "").strip()
    if text:
        cNvPr.set("descr", text)
    else:
        cNvPr.attrib.pop("descr", None)


def find_placeholder_by_idx(
    root: ET.Element,
    idx: int,
) -> Optional[ET.Element]:
    """Find a <p:sp> element by its placeholder idx attribute."""
    for sp in root.findall(".//p:sp", NS):
        ph = sp.find(".//p:ph", NS)
        if ph is not None and int(ph.get("idx", "-1")) == idx:
            return sp
    return None


def set_text_preserving_format(
    sp: ET.Element,
    text: str,
) -> None:
    """
    Set text content in a shape while preserving all rPr (run properties).

    Strategy:
    - Find the first <a:r> element's <a:t> and set its text
    - Remove excess <a:r> elements
    - Never modify <a:rPr> attributes
    """
    txBody = sp.find(".//p:txBody", NS)
    if txBody is None:
        return

    paragraphs = txBody.findall("a:p", NS)
    if not paragraphs:
        return

    # Use the first paragraph
    para = paragraphs[0]
    runs = para.findall("a:r", NS)

    if runs:
        # Set text on first run's <a:t>
        a_t = runs[0].find("a:t", NS)
        if a_t is not None:
            a_t.text = text

        # Remove extra runs
        for excess in runs[1:]:
            para.remove(excess)
    else:
        # No existing runs — create one
        a_r = ET.SubElement(para, f"{{{NS['a']}}}r")
        a_t = ET.SubElement(a_r, f"{{{NS['a']}}}t")
        a_t.text = text


def get_shape_position(sp: ET.Element) -> dict:
    """Extract position and size from a shape's <a:xfrm> element."""
    xfrm = sp.find(".//a:xfrm", NS)
    if xfrm is None:
        return {"x": 0, "y": 0, "cx": 0, "cy": 0}

    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)

    result = {"x": 0, "y": 0, "cx": 0, "cy": 0}
    if off is not None:
        result["x"] = int(off.get("x", "0"))
        result["y"] = int(off.get("y", "0"))
    if ext is not None:
        result["cx"] = int(ext.get("cx", "0"))
        result["cy"] = int(ext.get("cy", "0"))

    return result


def emu_to_inches(emu: int) -> float:
    """Convert EMU (English Metric Units) to inches."""
    return emu / 914400


def inches_to_emu(inches: float) -> int:
    """Convert inches to EMU."""
    return int(inches * 914400)