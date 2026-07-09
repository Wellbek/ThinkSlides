"""
Tool: extract_template_design_system
Unpacks a PPTX/POTX template's XML to derive the color palette, font scheme,
slide dimensions, layout catalog with placeholder positions, spacing tokens,
corner decorations, master slide elements, and embedded media.

For PDF templates, pages are rendered to images and analysed by a vision LLM
(GLM-4.6V / GPT-4o) which returns a structured design-system bundle matching
the same schema produced by the XML path.

This is also called at template upload time (in api/templates.py) and stored
as a versioned DesignSystemBundle in the database.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.observability.logging import get_logger
from app.llm import get_vision_llm_provider
import os
import json
import re
import time
import zipfile
import xml.etree.ElementTree as ET
import base64

logger = get_logger(__name__)

# OOXML namespaces
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

# Sentinel values that mean "use the built-in default design system" rather
# than a real file path. The LLM sometimes invents paths like
# "default_template.pptx" when the user asks for a default template; any of
# these (empty / None / case-insensitive "default" / "none" / a missing file
# whose name contains "default") resolve to the built-in parsed design system
# below instead of failing.
DEFAULT_SENTINELS = {"", "default", "none", "null", "builtin", "built-in"}


def _inch(inches: float) -> int:
    """Convert inches to EMU (1 inch = 914400 EMU)."""
    return int(round(inches * 914400))


def get_default_design_system() -> dict:
    """Return a fully-parsed built-in default design system.

    This mirrors the design produced by
    ``app.renderer.default_template.DefaultTemplateGenerator`` (same palette,
    fonts, slide dimensions and layout catalog) so that presentations built
    without a user template stay visually coherent with what the renderer
    actually emits.

    It is used as a fallback whenever ``extract_template_design_system`` is
    called without a real template file - e.g. when the user asks for a
    "default template" - so the agent never fails with "Template not found".
    """
    # Palette aligned with DefaultTemplateGenerator.COLORS
    palette_colors = [
        {"name": "primary", "value": "#1F4E78", "type": "hex"},     # professional blue
        {"name": "secondary", "value": "#2980B9", "type": "hex"},   # lighter blue
        {"name": "accent", "value": "#FF7F50", "type": "hex"},      # orange accent
        {"name": "dark", "value": "#15334F", "type": "hex"},        # dark blue
        {"name": "light", "value": "#F0F4F8", "type": "hex"},       # light gray
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#808080", "type": "hex"},
    ]

    # Slide dimensions: 10in x 5.625in (16:9), matching the renderer default
    slide_dimensions = {
        "widthEmu": _inch(10),
        "heightEmu": _inch(5.625),
        "widthPx": 960,
        "heightPx": 540,
    }

    layouts = [
        {
            "layoutId": "title",
            "layoutName": "Title Slide",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(1.0), "yEmu": _inch(1.5), "cxEmu": _inch(8.0), "cyEmu": _inch(1.5)},
                {"idx": 1, "type": "subtitle", "name": "Subtitle",
                 "xEmu": _inch(1.0), "yEmu": _inch(3.0), "cxEmu": _inch(8.0), "cyEmu": _inch(1.0)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "content",
            "layoutName": "Title and Content",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "body", "name": "Content",
                 "xEmu": _inch(0.6), "yEmu": _inch(1.5), "cxEmu": _inch(9.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "twoColumn",
            "layoutName": "Two Column",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "body", "name": "Left Column",
                 "xEmu": _inch(0.6), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.5)},
                {"idx": 2, "type": "body", "name": "Right Column",
                 "xEmu": _inch(5.0), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "image",
            "layoutName": "Picture with Caption",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "image", "name": "Image",
                 "xEmu": _inch(1.0), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.0)},
                {"idx": 2, "type": "body", "name": "Caption",
                 "xEmu": _inch(5.5), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "table",
            "layoutName": "Table",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "table", "name": "Table",
                 "xEmu": _inch(0.6), "yEmu": _inch(1.5), "cxEmu": _inch(9.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "conclusion",
            "layoutName": "Conclusion",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(1.0), "yEmu": _inch(1.5), "cxEmu": _inch(8.0), "cyEmu": _inch(3.0)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
    ]

    return {
        "palette": {"colors": palette_colors, "schemeName": "thinkslides-default"},
        "typography": {
            "titleFont": {"typeface": "Arial", "sizePt": 36, "bold": True},
            "bodyFont": {"typeface": "Arial", "sizePt": 20, "bold": False},
            "captionFont": {"typeface": "Arial", "sizePt": 12, "bold": False},
        },
        "slideDimensions": slide_dimensions,
        "layouts": layouts,
        "spacing": {
            "slidePaddingEmu": _inch(0.5),
            "elementGapEmu": _inch(0.25),
            "titleTopMarginEmu": _inch(0.3),
            "bodyTopMarginEmu": _inch(0.2),
        },
        "masterElements": [],
        "embeddedMedia": [],
        "sourceType": "default",
    }


class ExtractTemplateDesignSystemTool:
    """Extract the complete design system from a PPTX/POTX template file."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="extract_template_design_system",
            description=(
                "Parse a presentation template (PPTX/POTX/PDF) and extract its design system: "
                "color palette, font scheme, slide dimensions, layout catalog with "
                "placeholder positions, spacing tokens, master elements, and embedded media. "
                "PPTX/POTX files are parsed via XML; PDF files are rendered to images and "
                "analysed by a vision LLM. Returns a structured design system bundle. "
                "If the user has no template or asks for a default, omit template_path "
                "(or pass 'default') to get the built-in default design system - do NOT "
                "invent a filename."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="template_path",
                    type="string",
                    description=(
                        "Path to the PPTX/POTX/PDF template file. "
                        "Omit this (or pass 'default') to use the built-in default "
                        "design system when the user has no template."
                    ),
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(cls, template_path: str = "") -> ToolResult:
        start = time.time()
        try:
            normalized = (template_path or "").strip()
            looks_like_default = (
                normalized.lower() in DEFAULT_SENTINELS
                or "default" in normalized.lower()
            )

            # No path / explicit default request -> built-in design system
            if not normalized or normalized.lower() in DEFAULT_SENTINELS:
                return cls._default_result(start)

            # A path was given but the file does not exist. If it looks like a
            # default request (e.g. the LLM invented "default_template.pptx"),
            # gracefully fall back to the built-in design system instead of
            # failing the whole generation.
            if not os.path.exists(template_path):
                if looks_like_default:
                    logger.warning(
                        "template_path_not_found_using_default",
                        template_path=template_path,
                    )
                    return cls._default_result(start)
                return ToolResult(
                    success=False,
                    error=(
                        f"Template not found: {template_path}. If the user does not "
                        "have a template, call extract_template_design_system without "
                        "a template_path (or with 'default') to use the built-in design system."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

            ext = os.path.splitext(template_path)[1].lower()

            if ext == ".pdf":
                design_system = await cls._extract_from_pdf(template_path)
            elif ext in (".pptx", ".potx"):
                design_system = await cls._extract_from_pptx(template_path)
            else:
                return ToolResult(
                    success=False,
                    error=f"Unsupported template format: {ext}. Supported: .pptx, .potx, .pdf",
                    duration_ms=int((time.time() - start) * 1000),
                )

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "design_system_extracted",
                template_path=template_path,
                source_type=ext,
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
    def _default_result(start: float) -> ToolResult:
        """Build a successful ToolResult from the built-in default design system."""
        design_system = get_default_design_system()
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "design_system_extracted",
            template_path="(default)",
            source_type="default",
            layout_count=len(design_system.get("layouts", [])),
            duration_ms=duration_ms,
        )
        return ToolResult(
            success=True,
            data=design_system,
            duration_ms=duration_ms,
        )

    # ── PPTX / POTX extraction (XML-based) ──────────────────────────────

    @classmethod
    async def _extract_from_pptx(cls, template_path: str) -> dict:
        """Extract design system from a PPTX/POTX template via XML parsing."""
        with zipfile.ZipFile(template_path, "r") as zf:
            design_system = {}
            design_system["palette"] = cls._extract_palette(zf)
            design_system["typography"] = cls._extract_typography(zf)
            design_system["slideDimensions"] = cls._extract_dimensions(zf)
            design_system["layouts"] = cls._extract_layouts(zf)
            design_system["spacing"] = cls._extract_spacing(zf)
            design_system["masterElements"] = cls._extract_master_elements(zf)
            design_system["embeddedMedia"] = cls._extract_embedded_media(zf)
        design_system["sourceType"] = "pptx"
        return design_system

    # ── PDF extraction (vision LLM-based) ───────────────────────────────

    @classmethod
    async def _extract_from_pdf(cls, template_path: str) -> dict:
        """Extract design system from a PDF template using a vision LLM.

        The PDF pages are rendered to PNG images and sent to a vision-capable
        model (e.g. GLM-4.6V, GPT-4o) which analyses the visual design and
        returns a structured JSON design-system bundle matching the same
        schema produced by the XML path.
        """
        images = cls._convert_pdf_pages_to_images(template_path)
        if not images:
            raise ValueError("No pages could be rendered from the PDF")

        prompt = cls._build_vision_prompt(page_count=len(images))
        vision_provider = get_vision_llm_provider()

        logger.info(
            "pdf_vision_analysis_starting",
            template_path=template_path,
            sampled_pages=len(images),
        )

        response_text = await vision_provider.chat_with_images(
            prompt=prompt,
            images=images,
            temperature=0.3,
            max_tokens=4096,
        )

        design_system = cls._parse_vision_response(response_text)
        design_system["sourceType"] = "pdf"

        logger.info(
            "pdf_vision_analysis_completed",
            palette_colors=len(design_system.get("palette", {}).get("colors", [])),
            layout_count=len(design_system.get("layouts", [])),
        )

        return design_system

    @staticmethod
    def _convert_pdf_pages_to_images(
        pdf_path: str,
        max_pages: int = 15,
        dpi_scale: float = 1.5,
    ) -> list[str]:
        """Render PDF pages to base64-encoded JPEG data URIs.

        For PDFs with more pages than *max_pages*, pages are sampled evenly
        across the document (always including the first and last page) so
        that the vision LLM sees representative layouts throughout.

        Args:
            pdf_path: Path to the PDF file.
            max_pages: Maximum number of pages to render (default 15).
            dpi_scale: Scaling factor — 1.5 balances quality and token count.
                JPEG compression keeps each page ~50–150 KB instead of
                ~500 KB–1 MB for PNG.
            Returns:
            List of data URI strings (``data:image/jpeg;base64,...``).
        """
        import fitz  # PyMuPDF

        images: list[str] = []
        doc = fitz.open(pdf_path)

        try:
            total_pages = len(doc)
            if total_pages == 0:
                return images

            # Smart sampling: evenly distribute sampled pages, always include
            # first and last page so the vision model sees the full range.
            if total_pages <= max_pages:
                page_indices = list(range(total_pages))
            else:
                step = (total_pages - 1) / (max_pages - 1)
                page_indices = sorted(
                    set(round(i * step) for i in range(max_pages))
                )

            logger.info(
                "pdf_pages_rendering",
                total_pages=total_pages,
                sampled_pages=len(page_indices),
            )

            for page_num in page_indices:
                page = doc[page_num]
                mat = fitz.Matrix(dpi_scale, dpi_scale)
                # alpha=False required for JPEG output (no transparency)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_bytes = pix.tobytes("jpeg")
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                images.append(f"data:image/jpeg;base64,{b64}")
        finally:
            doc.close()

        return images

    @staticmethod
    def _build_vision_prompt(page_count: int = 1) -> str:
        """Build the prompt instructing the vision LLM to extract design-system info.

        The prompt asks for JSON matching the same schema that the XML
        extraction path produces, so downstream consumers (renderer, agent)
        don't need to know the source format.
        """
        page_note = (
            f"I am showing you {page_count} pages from this template."
            if page_count > 1
            else "I am showing you one page from this template."
        )
        return f"""Analyze the visual design system of this presentation template. {page_note}

Return a JSON object with these EXACT keys (no extra keys, no missing keys):

1. "palette": {{ "colors": [ {{"name": string, "value": hex like "#FF0000", "type": "hex"}} ], "schemeName": string }}
   - Identify ALL distinct colors: slide background, title text, body text, accent bars, decorative shapes, footers, etc.

2. "typography": {{ "titleFont": {{"typeface": string, "sizePt": number, "bold": boolean}}, "bodyFont": {{"typeface": string, "sizePt": number, "bold": boolean}}, "captionFont": {{"typeface": string, "sizePt": number, "bold": boolean}} }}
   - Estimate font sizes in points. Common ranges: titles 24-44pt, body 14-18pt, captions 9-12pt.

3. "slideDimensions": {{ "widthEmu": number, "heightEmu": number, "widthPx": number, "heightPx": number }}
   - Standard widescreen (16:9): 12192000×6858000 EMU = 1280×720 px
   - Standard (4:3): 9144000×6858000 EMU = 960×720 px
   - Determine which aspect ratio the slides use.

4. "layouts": [ {{ "layoutId": string, "layoutName": string, "masterId": "1", "placeholders": [ {{"idx": number, "type": "title"|"body"|"subtitle"|"ctrTitle"|"sldNum"|"dt"|"ftr", "name": string, "xEmu": number, "yEmu": number, "cxEmu": number, "cyEmu": number}} ], "hasBackground": boolean, "hasDecorations": boolean }} ]
   - Each distinct slide layout you see should be a separate entry.
   - Estimate placeholder positions in EMU (1 inch = 914400 EMU, 1 cm = 360000 EMU).
   - Common layouts: title slide, title+content, section header, two-column, blank.

5. "spacing": {{ "slidePaddingEmu": number, "elementGapEmu": number, "titleTopMarginEmu": number, "bodyTopMarginEmu": number }}
   - Margins/padding around content areas. Default: slidePaddingEmu ≈ 457200 (~0.5 inch).

6. "masterElements": [ {{"elementType": "shape"|"image"|"line", "name": string, "position": {{"xEmu": number, "yEmu": number, "cxEmu": number, "cyEmu": number}} }} ]
   - Decorative elements that appear on every slide: logos, accent bars, background shapes, footer lines.

7. "embeddedMedia": [ {{"mediaType": "image"|"audio"|"video", "rId": "", "fileName": string, "mimeType": string, "fileSizeBytes": number}} ]
   - Any embedded images/logos visible in the template.

Be precise with hex colors and positions. Return ONLY valid JSON — no markdown fences, no commentary."""

    @staticmethod
    def _parse_vision_response(response_text: str) -> dict:
        """Parse the vision LLM response into a design-system bundle.

        Falls back to sensible defaults for any fields the LLM could not
        determine, ensuring the output always matches the expected schema.
        """
        DEFAULTS: dict = {
            "palette": {"colors": [], "schemeName": "pdf-extracted"},
            "typography": {
                "titleFont": {"typeface": "Calibri", "sizePt": 28, "bold": True},
                "bodyFont": {"typeface": "Calibri", "sizePt": 14, "bold": False},
                "captionFont": {"typeface": "Calibri", "sizePt": 10, "bold": False},
            },
            "slideDimensions": {
                "widthEmu": 12192000,
                "heightEmu": 6858000,
                "widthPx": 1280,
                "heightPx": 720,
            },
            "layouts": [],
            "spacing": {
                "slidePaddingEmu": 457200,
                "elementGapEmu": 228600,
                "titleTopMarginEmu": 274320,
                "bodyTopMarginEmu": 182880,
            },
            "masterElements": [],
            "embeddedMedia": [],
        }

        # Strip whitespace
        text = response_text.strip()

        # Remove markdown code fences if the LLM wrapped its output
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        parsed: dict | None = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "vision_response_json_parse_failed",
                response_preview=text[:500],
            )
            # Try to extract the first {...} block
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                except json.JSONDecodeError:
                    logger.error("vision_response_json_extraction_failed")

        if parsed is None:
            logger.warning("vision_response_fallback_to_defaults")
            return dict(DEFAULTS)

        # Merge: use parsed values where present, defaults for missing keys
        result: dict = {}
        for key, default_val in DEFAULTS.items():
            if key in parsed and parsed[key] is not None:
                result[key] = parsed[key]
            else:
                result[key] = default_val

        return result

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
