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
from app.agent.tools._today import today_line
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.measure import archetypes as archetype_model
from app.agent import run_context
from app.measure import clusters
from app.measure import density as density_model
from app.measure import grammar as grammar_model
from app.observability.logging import get_logger
from typing import Sequence
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

# How many pages of a template the vision model is shown. Unchanged from the
# even sampler this replaced: the point is spending the budget better, not
# spending more of it.
VISION_PAGE_BUDGET = 15


def _pdf_page_count(pdf_path: str) -> int:
    from app.measure.rendered import page_count

    return page_count(pdf_path)


# The built-in default design system lives in ``app.design_system`` so it can
# be shared with the renderer (which would otherwise create an import cycle if
# it imported from this agent-tools module). Re-exported here for backward
# compatibility with any caller importing it from its historical location.
from app.design_system import get_default_design_system, derive_data_viz_config  # noqa: E402


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
                ToolParameter(
                    name="page_range",
                    type="string",
                    description=(
                        "Which pages of the template to treat as the template, e.g. "
                        "'20-35' or '1,4,9-12'. Omit for the whole document. Use it "
                        "when the user says only part of a deck is the style to "
                        "follow - those pages then have to stand on their own as a "
                        "complete template."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="color_theme",
                    type="string",
                    description=(
                        "Color palette for the built-in default design system: either one of "
                        "professional, warm, monochrome, bold, nature, OR a specific brand hex "
                        "color like '#00704A' when the topic implies one (e.g. a well-known "
                        "brand's actual color) - prefer the exact hex over the nearest named "
                        "preset when you know it. Only applies when template_path is omitted - "
                        "an uploaded template's own colors are always used as-is."
                    ),
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(cls, template_path: str = "", color_theme: str = "",
                  page_range: str = "") -> ToolResult:
        start = time.time()
        try:
            normalized = (template_path or "").strip()
            looks_like_default = (
                normalized.lower() in DEFAULT_SENTINELS
                or "default" in normalized.lower()
            )

            # No path / explicit default request -> built-in design system
            if not normalized or normalized.lower() in DEFAULT_SENTINELS:
                return cls._default_result(start, color_theme)

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
                    return cls._default_result(start, color_theme)
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
                design_system = await cls._extract_from_pdf(template_path, page_range)
            elif ext in (".pptx", ".potx"):
                design_system = await cls._extract_from_pptx(template_path, page_range)
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
    def _default_result(start: float, color_theme: str = "") -> ToolResult:
        """Build a successful ToolResult from the built-in default design system."""
        design_system = get_default_design_system(color_theme=color_theme or None)
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "design_system_extracted",
            template_path="(default)",
            source_type="default",
            color_theme=design_system.get("colorTheme"),
            layout_count=len(design_system.get("layouts", [])),
            duration_ms=duration_ms,
        )
        return ToolResult(
            success=True,
            data=design_system,
            duration_ms=duration_ms,
        )

    # ── Measured tokens (density, type scale) ───────────────────────────

    @classmethod
    def _measure_template(cls, design_system: dict, template_path: str,
                          page_range: str = "", survey: bool = False) -> None:
        """
        Read a template's rendered pages once, and learn everything from them.

        Density, the type ramp and - where the caller has not already surveyed
        the pages itself - the layout clusters. One conversion for all of it:
        a PPTX has to go through LibreOffice to be read at all, and doing that
        twice for one upload is a minute of a user's time spent on nothing.

        No LLM is involved in any of it, and none should be. How many words a
        page carries, what point size they are set in and which pages look
        alike are counts, and a model asked to estimate them from a picture
        will guess.
        """
        from app.measure.rendered import as_pdf

        try:
            with as_pdf(template_path) as pdf_path:
                if not pdf_path:
                    logger.info("template_profile_skipped",
                                template_path=template_path)
                    return
                pages = clusters.parse_page_range(
                    page_range or None, _pdf_page_count(pdf_path)
                )
                if survey:
                    surveyed = clusters.survey_pdf(pdf_path, pages,
                                                   budget=VISION_PAGE_BUDGET)
                    design_system["pageSurvey"] = surveyed.to_dict()
                    cls._read_grammar(design_system, pdf_path, surveyed)
                profile = density_model.profile_pdf(pdf_path, pages)
        except Exception as e:  # pragma: no cover - a broken template
            logger.warning("template_profile_failed", error=str(e))
            return

        density, types = profile
        if density.pages_measured:
            design_system["density"] = density.to_dict()
        design_system["typography"] = cls._merge_typography(
            design_system.get("typography") or {}, types
        )
        # dataViz names the face charts are drawn in, and it was derived from
        # the pre-measurement bodyFont. Leaving it stale is how a deck ends up
        # with a measured serif body and sans chart labels.
        body_face = design_system["typography"]["bodyFont"].get("typeface")
        if body_face and isinstance(design_system.get("dataViz"), dict):
            design_system["dataViz"]["fontFamily"] = body_face
        logger.info(
            "template_profile_measured",
            pages=density.pages_measured,
            words_median=density.words_median,
            blocks_median=density.blocks_median,
            display_pt=types.display_pt,
            body_pt=types.body_pt,
            display_is_serif=types.display_is_serif,
        )

    @staticmethod
    def _read_grammar(design_system: dict, pdf_path: str, survey) -> None:
        """
        Read each distinct layout's region grammar, as its own step.

        Its own step, and its own failure: the reason #118 asked for this to
        be split out was that one malformed response used to lose the whole
        bundle. A layout whose grammar cannot be read leaves the rest of the
        design system exactly as it was, and the deck composes the way it did
        before regions existed.

        One page per cluster, not one per sampled page: the survey already
        established that the pages of a cluster look alike, and reading eight
        pages of the same layout would report the same grammar eight times.
        """
        pages = [cluster.representative for cluster in survey.clusters]
        labels = {cluster.representative: cluster.label
                  for cluster in survey.clusters}
        if not pages:
            return
        try:
            read = grammar_model.read_pdf(pdf_path, pages, labels)
        except Exception as e:  # pragma: no cover - a page that will not read
            logger.warning("layout_grammar_failed", error=str(e))
            return

        design_system["layoutGrammar"] = read.to_dict()
        logger.info(
            "layout_grammar_read",
            layouts=len(read.layouts),
            features=sorted(read.features),
        )

        # And what those layouts are *called*, plus the chapter the template
        # repeats them in. Its own try: a deck with no repeating series is the
        # normal case, and failing to find one must not cost the grammar.
        try:
            named = archetype_model.read_pdf(pdf_path, survey, read)
        except Exception as e:  # pragma: no cover - a page that will not read
            logger.warning("archetypes_failed", error=str(e))
            return
        design_system["archetypes"] = named.to_dict()
        logger.info(
            "archetypes_read",
            archetypes=len(named.archetypes),
            chapter_length=named.chapter.length if named.chapter else 0,
            chapter_count=named.chapter.count if named.chapter else 0,
        )

    @staticmethod
    def _merge_typography(declared: dict, types) -> dict:
        """
        Measured sizes, and whichever typeface name is the more trustworthy.

        A point size is never substituted at render time, so the measured ramp
        always wins over the sizes the XML path fills in (28/14/10, which are
        a guess) and over a vision model's estimate. A typeface *name* is
        substituted whenever the font is missing from this container, so it is
        resolved case by case - see ``density.resolve_face``.
        """
        from app.measure.density import (
            fallback_chain, preferred_face, resolve_face,
        )
        from app.measure.page_metrics import _is_serif

        merged = dict(types.as_typography())
        for role, measured_pt in (("titleFont", types.display_pt),
                                  ("bodyFont", types.body_pt),
                                  ("captionFont", types.caption_pt)):
            was = dict(declared.get(role) or {})
            now = dict(merged.get(role) or {})
            face = resolve_face(was.get("typeface"), now.get("typeface"))
            # The chain keeps the substitute this host rendered with, so a
            # chart rasterised here still matches the deck; the deck itself is
            # given the family's real head, which the reader's machine has.
            chain = fallback_chain(face)
            if face:
                now["typeface"] = preferred_face(face)
            if not measured_pt and was.get("sizePt"):
                now["sizePt"] = was["sizePt"]
            if "bold" in was:
                now["bold"] = was["bold"]
            if was.get("eastAsiaTypeface"):
                now["eastAsiaTypeface"] = was["eastAsiaTypeface"]
            now["fallbacks"] = chain
            merged[role] = now
        merged["displayIsSerif"] = _is_serif(
            str(merged["titleFont"].get("typeface") or "")
        )
        return merged

    # ── PPTX / POTX extraction (XML-based) ──────────────────────────────

    @classmethod
    async def _extract_from_pptx(cls, template_path: str,
                                 page_range: str = "") -> dict:
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
        # No LLM call on this path by design (fast, deterministic XML
        # parsing) - table/chart styling tokens are derived the same way
        # from whatever palette was actually extracted, rather than adding
        # an LLM round-trip just to pick chart colors.
        design_system["dataViz"] = derive_data_viz_config(
            design_system["palette"]["colors"],
            body_font=design_system.get("typography", {}).get("bodyFont", {}).get("typeface", "Arial"),
        )
        # A PPTX carrying real slides is surveyed like a PDF: its layouts are
        # what the deck actually looks like, and the XML catalog says only
        # which placeholder boxes exist.
        cls._measure_template(design_system, template_path, page_range, survey=True)
        return design_system

    # ── PDF extraction (vision LLM-based) ───────────────────────────────

    @classmethod
    async def _extract_from_pdf(cls, template_path: str,
                                page_range: str = "") -> dict:
        """Extract design system from a PDF template using a vision LLM.

        Which pages the model sees is decided first, and not by the model:
        every page is read cheaply, pages that look alike are grouped, and the
        budget is spent one page per group before a second from any. See
        ``measure.clusters`` for why even sampling was the wrong spend.
        """
        survey = clusters.survey_pdf(template_path, page_range or None,
                                     budget=VISION_PAGE_BUDGET)
        images = cls._render_pages(template_path, survey.sample)
        if not images:
            raise ValueError("No pages could be rendered from the PDF")

        prompt = cls._build_vision_prompt(page_count=len(images), survey=survey)
        # The workspace's own configured model. This used to be a process-wide
        # provider built from environment variables, which meant reading a
        # user's template - their brand - on a key they had never chosen.
        vision_provider = run_context.get_vision_provider()
        if vision_provider is None:
            raise ValueError(
                "The model saved in Settings cannot read a PDF template. "
                "Choose a vision-capable model, or upload the template as a "
                ".pptx instead."
            )

        logger.info(
            "pdf_vision_analysis_starting",
            template_path=template_path,
            sampled_pages=len(images),
            considered_pages=len(survey.considered),
            clusters=len(survey.clusters),
            layouts_seen=list(survey.sampled_labels()),
        )

        response_text = await vision_provider.chat_with_images(
            prompt=prompt,
            images=images,
            temperature=0.3,
            max_tokens=4096,
        )

        design_system = cls._parse_vision_response(response_text)
        design_system["sourceType"] = "pdf"
        # The survey travels with the design system: #121 names the archetypes
        # from it and #130 scores whether the sample covered the deck, and
        # neither should have to re-read the PDF to find out what was seen.
        design_system["pageSurvey"] = survey.to_dict()
        cls._read_grammar(design_system, template_path, survey)
        cls._measure_template(design_system, template_path, page_range)

        logger.info(
            "pdf_vision_analysis_completed",
            palette_colors=len(design_system.get("palette", {}).get("colors", [])),
            layout_count=len(design_system.get("layouts", [])),
        )

        return design_system

    @staticmethod
    def _render_pages(
        pdf_path: str,
        pages: Sequence[int],
        dpi_scale: float = 1.5,
    ) -> list[str]:
        """Render the chosen 1-based pages as base64 JPEG data URIs.

        ``dpi_scale`` 1.5 balances quality against token count, and JPEG keeps
        each page ~50-150 KB instead of the ~500 KB-1 MB a PNG would.
        """
        import fitz  # PyMuPDF

        images: list[str] = []
        doc = fitz.open(pdf_path)
        try:
            for page_number in pages:
                if not 1 <= page_number <= len(doc):
                    continue
                pix = doc[page_number - 1].get_pixmap(
                    matrix=fitz.Matrix(dpi_scale, dpi_scale), alpha=False
                )
                b64 = base64.b64encode(pix.tobytes("jpeg")).decode("utf-8")
                images.append(f"data:image/jpeg;base64,{b64}")
        finally:
            doc.close()

        logger.info("pdf_pages_rendering", sampled_pages=len(images))
        return images

    @staticmethod
    def _build_vision_prompt(page_count: int = 1, survey=None) -> str:
        """Build the prompt instructing the vision LLM to extract design-system info.

        The prompt asks for JSON matching the same schema that the XML
        extraction path produces, so downstream consumers (renderer, agent)
        don't need to know the source format.

        It also says *why* these pages: the model is looking at one page per
        distinct layout rather than a slice of a document, which is the
        difference between "here are pages 1, 14 and 27" and "here is one of
        each kind of page this deck has".
        """
        page_note = (
            f"{today_line()}\n\n"
            f"I am showing you {page_count} pages from this template."
            if page_count > 1
            else f"{today_line()}\n\nI am showing you one page from this template."
        )
        if survey is not None and survey.clusters:
            page_note += (
                f" They were chosen by grouping all {len(survey.considered)} "
                f"pages by visual structure and taking one from each of the "
                f"{len(survey.clusters)} groups found, largest first - so these "
                "are one of each distinct layout rather than an even slice of "
                "the document, and each one may stand for many more pages."
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

8. "dataViz": {{ "chartColorSequence": [hex, hex, ...], "chartAxisColor": hex, "chartGridColor": hex, "tableHeaderBg": hex, "tableHeaderFg": hex, "tableBandBg": hex, "tableRowBg": hex, "tableBorder": hex, "fontFamily": string, "chartValueAxis": bool, "chartCategoryAxis": bool, "chartValueGridlines": bool, "chartCategoryGridlines": bool, "chartBaselineRule": bool, "chartLabelMode": "auto"|"none"|"all"|"terminal"|"end", "chartLegend": "auto"|"none"|"swatches"|"inline", "chartUnitConvention": "axis"|"subtitle"|"title", "chartBarThickness": number, "tableTreatment": "filled"|"hairline", "tableHeaderFilled": bool }}
   - How this template DRAWS its charts and tables, not only what colour they are. Colours are the smallest part of why a chart looks like it belongs to a deck.
   - chartColorSequence: 3-5 of the template's own colors, ordered for use as chart data-series colors (most prominent/brand color first).
   - tableHeaderBg should be the template's primary/accent brand color; tableHeaderFg should be whichever of black/white has better contrast against it.
   - Look at the template's own charts and answer what you SEE:
     * chartValueAxis / chartValueGridlines: does a chart in this template have a numbered value axis and horizontal gridlines, or has it dropped both? Many consultancy decks drop both and label the data directly instead.
     * chartLabelMode: "end" if every bar carries its value at its end, "terminal" if only the last point of a line is labelled, "all" if every point is, "none" if the chart relies on its axis, "auto" if you cannot tell.
     * chartUnitConvention: where the unit is stated - "axis" (rotated up the side), "subtitle" (a second line under the chart's title, e.g. "thousands", "$ billion"), or "title".
     * chartBarThickness: roughly what fraction of each category slot the bars fill, 0.4 (thin, widely spaced) to 0.95 (nearly touching).
     * tableTreatment: "hairline" if the template's tables separate rows with thin rules on the page's own background, "filled" if rows are banded blocks of colour.
   - Base every colour on colors you actually identified in "palette" above - do not invent new colors here. For the style questions, describe the template rather than a preference.

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
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
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
            result = dict(DEFAULTS)
            result["dataViz"] = derive_data_viz_config(
                result["palette"]["colors"], result["typography"]["bodyFont"]["typeface"]
            )
            return result

        # Merge: use parsed values where present, defaults for missing keys
        result: dict = {}
        for key, default_val in DEFAULTS.items():
            if key in parsed and parsed[key] is not None:
                result[key] = parsed[key]
            else:
                result[key] = default_val

        # dataViz is handled separately from the DEFAULTS merge above: when
        # the vision model provides its own judgment (it can see the actual
        # template), trust it; otherwise derive one from whatever palette
        # was actually extracted rather than falling back to an unrelated
        # generic default.
        derived = derive_data_viz_config(
            result.get("palette", {}).get("colors", []),
            result.get("typography", {}).get("bodyFont", {}).get("typeface", "Arial"),
        )
        vision_data_viz = parsed.get("dataViz") if isinstance(parsed, dict) else None
        if isinstance(vision_data_viz, dict) and vision_data_viz.get("chartColorSequence"):
            # Merged over the derived set rather than replacing it: the model
            # reliably answers the colour questions and answers the style ones
            # partially, and a half-answered dataViz used to mean the style
            # tokens were simply absent. Whatever it did not say keeps the
            # deterministic default.
            result["dataViz"] = {**derived, **vision_data_viz}
        else:
            result["dataViz"] = derived

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
                    "mimeType": "application/octet-stream",
                    "fileSizeBytes": file_info.file_size,
                })

        except Exception as e:
            logger.warning("embedded_media_extraction_failed", error=str(e))

        return media
