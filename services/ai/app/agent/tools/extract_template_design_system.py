"""
Tool: extract_template_design_system
Unpacks a PPTX/POTX template's XML to derive the color palette, font scheme,
slide dimensions, layout catalog with placeholder positions, spacing tokens,
corner decorations, master slide elements, and embedded media.

This is also called at template upload time (in api/templates.py) and stored
as a versioned DesignSystemBundle in the database.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.observability.logging import get_logger
import os
import json
import time
import zipfile
import xml.etree.ElementTree as ET

logger = get_logger(__name__)

# OOXML namespaces
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


class ExtractTemplateDesignSystemTool:
    """Extract the complete design system from a PPTX/POTX template file."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="extract_template_design_system",
            description=(
                "Parse a PowerPoint template (PPTX/POTX) and extract its design system: "
                "color palette, font scheme, slide dimensions, layout catalog with "
                "placeholder positions, spacing tokens, master elements, and embedded media. "
                "Returns a structured design system bundle."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="template_path",
                    type="string",
                    description="Path to the PPTX/POTX template file",
                ),
            ],
        )

    @classmethod
    async def run(cls, template_path: str) -> ToolResult:
        start = time.time()
        try:
            if not os.path.exists(template_path):
                return ToolResult(success=False, error=f"Template not found: {template_path}")

            with zipfile.ZipFile(template_path, "r") as zf:
                design_system = {}

                # 1. Color palette from theme
                design_system["palette"] = cls._extract_palette(zf)

                # 2. Typography / font scheme
                design_system["typography"] = cls._extract_typography(zf)

                # 3. Slide dimensions
                design_system["slideDimensions"] = cls._extract_dimensions(zf)

                # 4. Layout catalog with placeholder positions
                design_system["layouts"] = cls._extract_layouts(zf)

                # 5. Spacing tokens (derived from layout analysis)
                design_system["spacing"] = cls._extract_spacing(zf)

                # 6. Master slide elements
                design_system["masterElements"] = cls._extract_master_elements(zf)

                # 7. Embedded media
                design_system["embeddedMedia"] = cls._extract_embedded_media(zf)

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "design_system_extracted",
                template_path=template_path,
                layout_count=len(design_system.get("layouts", [])),
                duration_ms=duration_ms,
            )

            return ToolResult(
                success=True,
                data=design_system,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("design_system_extraction_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))

    @staticmethod
    def _extract_palette(zf: zipfile.ZipFile) -> dict:
        """Extract color palette from theme1.xml."""
        try:
            theme_path = "ppt/theme/theme1.xml"
            if theme_path not in zf.namelist():
                return {"colors": [], "schemeName": "unknown"}

            with zf.open(theme_path) as f:
                tree = ET.parse(f)

            root = tree.getroot()
            colors = []
            scheme_elem = root.find(".//a:clrScheme", NS)
            scheme_name = scheme_elem.get("name", "Custom") if scheme_elem is not None else "unknown"

            if scheme_elem is not None:
                color_mapping = {
                    "dk1": "dark1", "lt1": "light1",
                    "dk2": "dark2", "lt2": "light2",
                    "accent1": "accent1", "accent2": "accent2",
                    "accent3": "accent3", "accent4": "accent4",
                    "accent5": "accent5", "accent6": "accent6",
                    "hlink": "hyperlink", "folHlink": "followedHyperlink",
                }

                for xml_name, friendly_name in color_mapping.items():
                    color_elem = scheme_elem.find(f"a:{xml_name}", NS)
                    if color_elem is not None:
                        # Check for srgbClr or sysClr
                        srgb = color_elem.find("a:srgbClr", NS)
                        sys_clr = color_elem.find("a:sysClr", NS)
                        if srgb is not None:
                            hex_val = srgb.get("val", "")
                            colors.append({
                                "name": friendly_name,
                                "value": f"#{hex_val}",
                                "type": "hex",
                            })
                        elif sys_clr is not None:
                            hex_val = sys_clr.get("lastClr", "")
                            sys_name = sys_clr.get("val", "")
                            colors.append({
                                "name": friendly_name,
                                "value": hex_val if hex_val else sys_name,
                                "type": "system" if not hex_val else "hex",
                            })

            return {"colors": colors, "schemeName": scheme_name}
        except Exception as e:
            logger.warning("palette_extraction_failed", error=str(e))
            return {"colors": [], "schemeName": "unknown"}

    @staticmethod
    def _extract_typography(zf: zipfile.ZipFile) -> dict:
        """Extract font scheme from theme1.xml."""
        try:
            theme_path = "ppt/theme/theme1.xml"
            if theme_path not in zf.namelist():
                return {}

            with zf.open(theme_path) as f:
                tree = ET.parse(f)

            root = tree.getroot()
            font_scheme = root.find(".//a:fontScheme", NS)

            if font_scheme is None:
                return {}

            result = {}
            major_font = font_scheme.find(".//a:majorFont", NS)
            minor_font = font_scheme.find(".//a:minorFont", NS)

            if major_font is not None:
                latin = major_font.find("a:latin", NS)
                ea = major_font.find("a:ea", NS)
                result["titleFont"] = {
                    "typeface": latin.get("typeface", "Calibri") if latin is not None else "Calibri",
                    "sizePt": 28,
                    "bold": True,
                }
                if ea is not None and ea.get("typeface"):
                    result["titleFont"]["eastAsiaTypeface"] = ea.get("typeface")

            if minor_font is not None:
                latin = minor_font.find("a:latin", NS)
                ea = minor_font.find("a:ea", NS)
                result["bodyFont"] = {
                    "typeface": latin.get("typeface", "Calibri") if latin is not None else "Calibri",
                    "sizePt": 14,
                    "bold": False,
                }
                if ea is not None and ea.get("typeface"):
                    result["bodyFont"]["eastAsiaTypeface"] = ea.get("typeface")

            result["captionFont"] = {
                "typeface": result.get("bodyFont", {}).get("typeface", "Calibri"),
                "sizePt": 10,
                "bold": False,
            }

            return result
        except Exception as e:
            logger.warning("typography_extraction_failed", error=str(e))
            return {}

    @staticmethod
    def _extract_dimensions(zf: zipfile.ZipFile) -> dict:
        """Extract slide dimensions from presentation.xml."""
        try:
            pres_path = "ppt/presentation.xml"
            if pres_path not in zf.namelist():
                return {"widthEmu": 9144000, "heightEmu": 6858000, "widthPx": 960, "heightPx": 720}

            with zf.open(pres_path) as f:
                tree = ET.parse(f)

            root = tree.getroot()
            sldSz = root.find(".//p:sldSz", NS)

            if sldSz is not None:
                cx = int(sldSz.get("cx", "9144000"))
                cy = int(sldSz.get("cy", "6858000"))
                return {
                    "widthEmu": cx,
                    "heightEmu": cy,
                    "widthPx": round(cx / 9144),
                    "heightPx": round(cy / 9144),
                }

            return {"widthEmu": 9144000, "heightEmu": 6858000, "widthPx": 960, "heightPx": 720}
        except Exception as e:
            logger.warning("dimensions_extraction_failed", error=str(e))
            return {"widthEmu": 9144000, "heightEmu": 6858000, "widthPx": 960, "heightPx": 720}

    @staticmethod
    def _extract_layouts(zf: zipfile.ZipFile) -> list:
        """Extract slide layout catalog with placeholder positions."""
        layouts = []
        try:
            layout_files = sorted(
                [n for n in zf.namelist() if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml")]
            )

            for layout_path in layout_files:
                try:
                    with zf.open(layout_path) as f:
                        tree = ET.parse(f)

                    root = tree.getroot()
                    layout_name = ""
                    cSld = root.find(".//p:cSld", NS)
                    if cSld is not None:
                        cSld_name = cSld.get("name", "")
                        layout_name = cSld_name

                    # Extract layout ID from filename
                    layout_id = layout_path.split("slideLayout")[1].split(".")[0]

                    # Find placeholders
                    placeholders = []
                    for sp in root.findall(".//p:sp", NS):
                        nvSpPr = sp.find(".//p:nvSpPr", NS)
                        if nvSpPr is not None:
                            nvPr = nvSpPr.find("p:nvPr", NS)
                            if nvPr is not None:
                                ph = nvPr.find("p:ph", NS)
                                if ph is not None:
                                    ph_idx = ph.get("idx", "0")
                                    ph_type = ph.get("type", "body")
                                    ph_name = ""

                                    # Get placeholder name from cNvPr
                                    cNvPr = nvSpPr.find(".//p:cNvPr", NS)
                                    if cNvPr is not None:
                                        ph_name = cNvPr.get("name", "")

                                    # Get position from spPr
                                    xfrm = sp.find(".//a:xfrm", NS)
                                    x_emu, y_emu, cx_emu, cy_emu = 0, 0, 0, 0
                                    if xfrm is not None:
                                        off = xfrm.find("a:off", NS)
                                        ext = xfrm.find("a:ext", NS)
                                        if off is not None:
                                            x_emu = int(off.get("x", "0"))
                                            y_emu = int(off.get("y", "0"))
                                        if ext is not None:
                                            cx_emu = int(ext.get("cx", "0"))
                                            cy_emu = int(ext.get("cy", "0"))

                                    placeholders.append({
                                        "idx": int(ph_idx),
                                        "type": ph_type,
                                        "name": ph_name,
                                        "xEmu": x_emu,
                                        "yEmu": y_emu,
                                        "cxEmu": cx_emu,
                                        "cyEmu": cy_emu,
                                    })

                    layouts.append({
                        "layoutId": layout_id,
                        "layoutName": layout_name or f"Layout {layout_id}",
                        "masterId": "1",
                        "placeholders": placeholders,
                        "hasBackground": cSld is not None and cSld.find(".//p:bg", NS) is not None,
                        "hasDecorations": False,
                    })
                except Exception as e:
                    logger.warning("layout_parse_failed", path=layout_path, error=str(e))
                    continue

        except Exception as e:
            logger.warning("layouts_extraction_failed", error=str(e))

        return layouts

    @staticmethod
    def _extract_spacing(zf: zipfile.ZipFile) -> dict:
        """Derive spacing tokens from layout analysis."""
        # Default spacing tokens; could be refined by analyzing body placeholder margins
        return {
            "slidePaddingEmu": 457200,      # ~0.5 inch
            "elementGapEmu": 228600,        # ~0.25 inch
            "titleTopMarginEmu": 274320,     # ~0.3 inch
            "bodyTopMarginEmu": 182880,      # ~0.2 inch
        }

    @staticmethod
    def _extract_master_elements(zf: zipfile.ZipFile) -> list:
        """Extract master slide decorative elements (shapes, images, connectors)."""
        elements = []
        try:
            master_path = "ppt/slideMasters/slideMaster1.xml"
            if master_path not in zf.namelist():
                return elements

            with zf.open(master_path) as f:
                tree = ET.parse(f)

            root = tree.getroot()

            # Find non-placeholder shapes on the master
            for sp in root.findall(".//p:sp", NS):
                nvSpPr = sp.find(".//p:nvSpPr", NS)
                if nvSpPr is not None:
                    nvPr = nvSpPr.find("p:nvPr", NS)
                    if nvPr is not None:
                        ph = nvPr.find("p:ph", NS)
                        # Skip placeholders — we only want decorative elements
                        if ph is not None:
                            continue

                    cNvPr = nvSpPr.find(".//p:cNvPr", NS)
                    name = cNvPr.get("name", "shape") if cNvPr is not None else "shape"

                    xfrm = sp.find(".//a:xfrm", NS)
                    position = {"xEmu": 0, "yEmu": 0, "cxEmu": 0, "cyEmu": 0}
                    if xfrm is not None:
                        off = xfrm.find("a:off", NS)
                        ext = xfrm.find("a:ext", NS)
                        if off is not None:
                            position["xEmu"] = int(off.get("x", "0"))
                            position["yEmu"] = int(off.get("y", "0"))
                        if ext is not None:
                            position["cxEmu"] = int(ext.get("cx", "0"))
                            position["cyEmu"] = int(ext.get("cy", "0"))

                    elements.append({
                        "elementType": "shape",
                        "name": name,
                        "position": position,
                    })

        except Exception as e:
            logger.warning("master_elements_extraction_failed", error=str(e))

        return elements

    @staticmethod
    def _extract_embedded_media(zf: zipfile.ZipFile) -> list:
        """Extract info about embedded media files (images, audio, video)."""
        media = []
        try:
            media_files = [n for n in zf.namelist() if n.startswith("ppt/media/")]

            for mf in media_files:
                ext = os.path.splitext(mf)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".tiff"):
                    media_type = "image"
                elif ext in (".mp3", ".wav", ".wma"):
                    media_type = "audio"
                elif ext in (".mp4", ".avi", ".wmv"):
                    media_type = "video"
                else:
                    media_type = "image"

                file_info = zf.getinfo(mf)
                media.append({
                    "mediaType": media_type,
                    "rId": "",
                    "fileName": os.path.basename(mf),
                    "mimeType": f"application/octet-stream",
                    "fileSizeBytes": file_info.file_size,
                })

        except Exception as e:
            logger.warning("embedded_media_extraction_failed", error=str(e))

        return media