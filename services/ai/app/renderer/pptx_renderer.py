"""
PPTX Renderer

Clones real layout XML from the template, fills placeholder elements
without touching rPr formatting attributes, and repacks.
Never recreates design from scratch — always clones and fills.
"""

from __future__ import annotations
import os
import copy
import json
import zipfile
import shutil
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Optional
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from app.observability.logging import get_logger
from app.renderer.layout import LayoutEngine, LayoutType, PositionConfig
from app.renderer.typography import TypographyEngine, TextStyle
from app.renderer.default_template import get_default_template_path

logger = get_logger(__name__)

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

ET.register_namespace("", "http://schemas.openxmlformats.org/package/2006/relationships")
ET.register_namespace("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")
ET.register_namespace("c", "http://schemas.openxmlformats.org/drawingml/2006/chart")


class PPTXRenderer:
    """
    Renders a PPTX file by cloning template layout XML and filling placeholders.

    Strategy:
    1. If a template is provided, use it as the base — copy its structure,
       slide layouts, and masters verbatim.
    2. For each slide in the input, clone the appropriate layout XML.
    3. Fill placeholder <a:t> text nodes without modifying any rPr (run properties)
       formatting attributes — this preserves the template's design fidelity.
    4. For images, add relationship entries and <p:pic> elements.
    5. Repack as a valid PPTX ZIP.
    """

    def __init__(self, template_path: Optional[str] = None) -> None:
        self.template_path = template_path

    def render(
        self,
        slides: list[dict],
        metadata: dict,
        output_path: str,
    ) -> str:
        if self.template_path and os.path.exists(self.template_path):
            return self._render_from_template(slides, metadata, output_path)
        else:
            return self._render_from_default(slides, metadata, output_path)

    def _render_from_template(
        self,
        slides: list[dict],
        metadata: dict,
        output_path: str,
    ) -> str:
        working_dir = tempfile.mkdtemp(prefix="thinkslides_render_")

        try:
            with zipfile.ZipFile(self.template_path, "r") as zf:
                zf.extractall(working_dir)

            slides_dir = os.path.join(working_dir, "ppt", "slides")
            rels_dir = os.path.join(working_dir, "ppt", "slides", "_rels")

            existing_slides = sorted(
                [f for f in os.listdir(slides_dir) if f.startswith("slide") and f.endswith(".xml")]
            ) if os.path.exists(slides_dir) else []

            for sf in existing_slides:
                os.remove(os.path.join(slides_dir, sf))
                rel_path = os.path.join(rels_dir, sf + ".rels") if os.path.exists(rels_dir) else ""
                if rel_path and os.path.exists(rel_path):
                    os.remove(rel_path)

            layout_map = self._build_layout_map(working_dir)

            for i, slide_data in enumerate(slides):
                layout_id = str(slide_data.get("layoutId", "1"))
                slide_num = i + 1
                slide_file = f"slide{slide_num}.xml"

                layout_file = layout_map.get(layout_id)
                if layout_file and os.path.exists(layout_file):
                    slide_xml = self._clone_layout_as_slide(
                        layout_file, slide_data, working_dir, slide_num
                    )
                else:
                    slide_xml = self._create_minimal_slide(slide_data)

                os.makedirs(slides_dir, exist_ok=True)
                slide_path = os.path.join(slides_dir, slide_file)
                with open(slide_path, "wb") as f:
                    f.write(slide_xml)

                self._create_slide_rels(working_dir, slide_num, layout_id)

            self._update_presentation_xml(working_dir, len(slides))
            self._update_content_types(working_dir, len(slides))
            self._apply_metadata(working_dir, metadata)
            self._repack(working_dir, output_path)

            logger.info("pptx_rendered_from_template", slide_count=len(slides), output=output_path)
            return output_path

        finally:
            shutil.rmtree(working_dir, ignore_errors=True)

    def _render_from_default(
        self,
        slides: list[dict],
        metadata: dict,
        output_path: str,
    ) -> str:
        # Use default template
        template_path = get_default_template_path()
        prs = Presentation(template_path)

        for slide_data in slides:
            # Infer layout type from slide content
            layout_type = LayoutEngine.infer_layout_type(slide_data)
            layout_config = LayoutEngine.get_layout(layout_type)

            # Find appropriate layout from template
            layout = self._find_layout_for_type(prs, layout_type)

            slide = prs.slides.add_slide(layout)

            # Render content using intelligent positioning
            self._render_content_on_slide(slide, slide_data, layout_config)

        core_props = prs.core_properties
        if metadata.get("title"):
            core_props.title = metadata["title"]
        if metadata.get("author"):
            core_props.author = metadata["author"]
        if metadata.get("subject"):
            core_props.subject = metadata["subject"]

        prs.save(output_path)
        logger.info("pptx_rendered_default", slide_count=len(slides), output=output_path)
        return output_path

    def _find_layout_for_type(self, prs: Presentation, layout_type: LayoutType):
        """Find the best layout from template for a given layout type."""
        # Simple heuristic: match by layout name
        type_names = {
            LayoutType.TITLE: ["title", "blank"],
            LayoutType.CONTENT: ["content", "blank"],
            LayoutType.TWO_COLUMN: ["two", "column", "blank"],
            LayoutType.IMAGE: ["image", "blank"],
            LayoutType.TABLE: ["table", "blank"],
            LayoutType.CONCLUSION: ["conclusion", "blank"],
        }

        names = type_names.get(layout_type, ["blank"])

        for layout in prs.slide_layouts:
            layout_name = (layout.name or "").lower()
            if any(name in layout_name for name in names):
                return layout

        # Fall back to first layout
        return prs.slide_layouts[0] if prs.slide_layouts else None

    def _render_content_on_slide(
        self,
        slide,
        slide_data: dict,
        layout_config: LayoutType,
    ) -> None:
        """Render slide content with intelligent positioning."""
        contents = slide_data.get("contents", [])
        if not contents:
            return

        # Check if slide has a title
        has_title = any(
            c.get("type") == "text" and
            any(r.get("fontSizePt", 0) >= 28 or r.get("bold", False)
                for r in c.get("runs", []))
            for c in contents
        )

        # Separate title from other content
        title_content = None
        other_content = []

        for content in contents:
            if content.get("type") == "text":
                runs = content.get("runs", [])
                if any(r.get("fontSizePt", 0) >= 28 or r.get("bold", False)
                      for r in runs):
                    title_content = content
                    continue
            other_content.append(content)

        # Render title
        if title_content:
            title_pos = LayoutEngine.calculate_title_position(layout_config)
            self._add_text_box(
                slide,
                title_content.get("runs", []),
                title_pos,
                is_title=True,
            )

        # Render other content
        top_offset = 0
        for content in other_content:
            if content.get("type") == "text":
                content_pos = LayoutEngine.calculate_content_position(
                    layout_config,
                    has_title=has_title,
                )
                if top_offset > 0:
                    content_pos.top += top_offset
                    content_pos.height -= top_offset

                self._add_text_box(
                    slide,
                    content.get("runs", []),
                    content_pos,
                    is_title=False,
                )
                top_offset += Inches(0.3)  # spacing between sections

            elif content.get("type") == "image":
                img_pos = LayoutEngine.calculate_image_position(
                    layout_config,
                    has_title=has_title,
                )
                self._add_image(slide, content.get("image", {}), img_pos)

    def _add_text_box(
        self,
        slide,
        runs: list[dict],
        pos: PositionConfig,
        is_title: bool = False,
    ) -> None:
        """Add a text box with proper styling."""
        txBox = slide.shapes.add_textbox(
            pos.left, pos.top, pos.width, pos.height
        )
        tf = txBox.text_frame
        tf.word_wrap = True

        for i, run_data in enumerate(runs):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()

            run = p.add_run()
            run.text = run_data.get("text", "")

            # Get proper style
            if is_title:
                style = TypographyEngine.get_title_style(1)
            else:
                level = run_data.get("level", 0)
                style = TypographyEngine.get_body_style(level)

            # Apply style
            run.font.size = Pt(style.font_size_pt)
            run.font.bold = style.bold or run_data.get("bold", False)
            run.font.italic = style.italic or run_data.get("italic", False)
            run.font.underline = style.underline or run_data.get("underline", False)

            # Validate font size
            if run.font.size:
                requested_size = run.font.size.pt
                adjusted_size = TypographyEngine.validate_font_size(
                    requested_size,
                    is_title=is_title
                )
                if adjusted_size != requested_size:
                    run.font.size = Pt(adjusted_size)

            # Bullet points
            if run_data.get("bullet", False):
                p.level = run_data.get("level", 0)

    def _add_image(self, slide, img_data: dict, pos: PositionConfig) -> None:
        """Add an image to the slide."""
        if img_data.get("source") == "base64":
            import base64
            img_binary = base64.b64decode(img_data.get("data", ""))
            img_stream = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img_stream.write(img_binary)
            img_stream.close()
            try:
                slide.shapes.add_picture(
                    img_stream.name,
                    pos.left, pos.top, pos.width, pos.height
                )
            finally:
                os.unlink(img_stream.name)

    def _build_layout_map(self, working_dir: str) -> dict[str, str]:
        layout_map: dict[str, str] = {}
        layouts_dir = os.path.join(working_dir, "ppt", "slideLayouts")
        if not os.path.exists(layouts_dir):
            return layout_map

        for fname in os.listdir(layouts_dir):
            if fname.startswith("slideLayout") and fname.endswith(".xml"):
                num = fname.replace("slideLayout", "").replace(".xml", "")
                layout_map[num] = os.path.join(layouts_dir, fname)

        return layout_map

    def _clone_layout_as_slide(
        self,
        layout_file: str,
        slide_data: dict,
        working_dir: str,
        slide_num: int,
    ) -> bytes:
        tree = ET.parse(layout_file)
        root = tree.getroot()

        for content in slide_data.get("contents", []):
            if content.get("type") != "text":
                continue

            placeholder_idx = content.get("placeholderIdx", -1)
            runs = content.get("runs", [])

            for sp in root.findall(".//p:sp", NS):
                ph = sp.find(".//p:ph", NS)
                if ph is not None and int(ph.get("idx", "-1")) == placeholder_idx:
                    txBody = sp.find(".//p:txBody", NS)
                    if txBody is None:
                        continue

                    paragraphs = txBody.findall("a:p", NS)
                    if not paragraphs:
                        continue

                    for p_idx, para in enumerate(paragraphs):
                        existing_runs = para.findall("a:r", NS)

                        if p_idx < len(runs):
                            run_data = runs[p_idx]
                            if existing_runs:
                                a_t = existing_runs[0].find("a:t", NS)
                                if a_t is not None:
                                    a_t.text = run_data.get("text", "")
                            else:
                                a_r = ET.SubElement(para, f"{{{NS['a']}}}r")
                                a_t = ET.SubElement(a_r, f"{{{NS['a']}}}t")
                                a_t.text = run_data.get("text", "")

                            for excess_run in existing_runs[1:]:
                                para.remove(excess_run)
                        elif existing_runs:
                            for excess_run in existing_runs:
                                para.remove(excess_run)

                    if len(runs) > len(paragraphs):
                        # Extra bullets beyond what the template placeholder
                        # itself had paragraphs for. Clone the last template
                        # paragraph's formatting - pPr (bullet marker/indent)
                        # and its first run's rPr (font/size/color) - onto
                        # each new one. Previously these were created bare,
                        # so bullets past the placeholder's own paragraph
                        # count silently lost the template's bullet styling
                        # and typography, making multi-point slides look
                        # thinner/plainer than the template's design.
                        template_para = paragraphs[-1] if paragraphs else None
                        template_pPr = template_para.find("a:pPr", NS) if template_para is not None else None
                        template_run = template_para.find("a:r", NS) if template_para is not None else None
                        template_rPr = template_run.find("a:rPr", NS) if template_run is not None else None

                        for run_data in runs[len(paragraphs):]:
                            new_para = ET.SubElement(txBody, f"{{{NS['a']}}}p")
                            if template_pPr is not None:
                                new_para.append(copy.deepcopy(template_pPr))
                            a_r = ET.SubElement(new_para, f"{{{NS['a']}}}r")
                            if template_rPr is not None:
                                a_r.append(copy.deepcopy(template_rPr))
                            a_t = ET.SubElement(a_r, f"{{{NS['a']}}}t")
                            a_t.text = run_data.get("text", "")

                    break

        return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8")

    def _create_minimal_slide(self, slide_data: dict) -> bytes:
        shape_id = 2
        shapes_xml = ""
        for content in slide_data.get("contents", []):
            if content.get("type") == "text":
                text = " ".join(r.get("text", "") for r in content.get("runs", []))
                shapes_xml += f"""
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="{shape_id}" name="Content"/>
          <p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>
          <p:nvPr/>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm>
            <a:off x="457200" y="274638"/>
            <a:ext cx="8229600" cy="5715000"/>
          </a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        </p:spPr>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p><a:r><a:t>{text}</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>"""
                shape_id += 1

        slide_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x="0" y="0"/>
          <a:ext cx="0" cy="0"/>
          <a:chOff x="0" y="0"/>
          <a:chExt cx="0" cy="0"/>
        </a:xfrm>
      </p:grpSpPr>
{shapes_xml}
    </p:spTree>
  </p:cSld>
</p:sld>"""
        return slide_xml.encode("utf-8")

    def _create_slide_rels(self, working_dir: str, slide_num: int, layout_id: str) -> None:
        rels_dir = os.path.join(working_dir, "ppt", "slides", "_rels")
        os.makedirs(rels_dir, exist_ok=True)
        rels_path = os.path.join(rels_dir, f"slide{slide_num}.xml.rels")

        rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
    Target="../slideLayouts/slideLayout{layout_id}.xml"/>
</Relationships>"""

        with open(rels_path, "w", encoding="utf-8") as f:
            f.write(rels_xml)

    def _update_presentation_xml(self, working_dir: str, slide_count: int) -> None:
        """Update presentation.xml to reference the correct number of slides."""
        pres_path = os.path.join(working_dir, "ppt", "presentation.xml")
        if not os.path.exists(pres_path):
            return

        tree = ET.parse(pres_path)
        root = tree.getroot()

        # Find the <p:sldIdLst> element. Some templates - including our own
        # generated default, which starts with zero slides - omit it
        # entirely rather than emitting an empty <p:sldIdLst/>. Silently
        # bailing out here previously produced a slide-less package: the
        # rest of the pipeline (slide XML, rels, content types) still ran
        # and reported success, but the file opened with 0 slides - the
        # "downloaded pptx is empty" failure mode. Create it in the correct
        # schema position (after sldMasterIdLst, before sldSz) instead.
        sldIdLst = root.find("p:sldIdLst", NS)
        if sldIdLst is None:
            sldIdLst = ET.Element(f"{{{NS['p']}}}sldIdLst")
            sldMasterIdLst = root.find("p:sldMasterIdLst", NS)
            insert_pos = list(root).index(sldMasterIdLst) + 1 if sldMasterIdLst is not None else 0
            root.insert(insert_pos, sldIdLst)

        # Remove existing slide ID entries
        for sldId in list(sldIdLst):
            sldIdLst.remove(sldId)

        # Assign relationship IDs first (by scanning the actual rels file -
        # see _update_presentation_rels), then reuse the same IDs here so the
        # two files never disagree about which rId maps to which slide.
        rids = self._update_presentation_rels(working_dir, slide_count)

        # Add new slide ID entries. Slide IDs start from 256 by convention.
        for i, rid in enumerate(rids):
            sldId = ET.SubElement(sldIdLst, f"{{{NS['p']}}}sldId")
            sldId.set("id", str(256 + i))
            sldId.set(f"{{{NS['r']}}}id", rid)

        tree.write(pres_path, xml_declaration=True, encoding="UTF-8")

    def _update_presentation_rels(self, working_dir: str, slide_count: int) -> list[str]:
        """Update presentation.xml.rels to reference all slides.

        Returns the relationship IDs assigned, in slide order, so the caller
        can reuse them in <p:sldIdLst> instead of re-deriving them.
        """
        rels_path = os.path.join(working_dir, "ppt", "_rels", "presentation.xml.rels")
        if not os.path.exists(rels_path):
            os.makedirs(os.path.dirname(rels_path), exist_ok=True)
            # Create a minimal rels file
            root = ET.fromstring("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""")
        else:
            tree = ET.parse(rels_path)
            root = tree.getroot()

        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"

        # Remove existing slide relationships - they get regenerated below.
        for rel in list(root):
            rel_type = rel.get("Type", "")
            if "slide" in rel_type and "layout" not in rel_type and "master" not in rel_type:
                root.remove(rel)

        # Find the next free numeric rId instead of hardcoding rId{i+2}, which
        # assumed rId1 is always the slideMaster and rId2..N+1 were free. Any
        # template whose presentation.xml.rels already used those IDs for
        # something else (a theme, printerSettings, tableStyles, ...) ended up
        # with duplicate Id attributes - invalid OOXML that PowerPoint reports
        # as a file needing repair.
        used_ids = set()
        for rel in root:
            rid = rel.get("Id", "")
            if rid.startswith("rId") and rid[3:].isdigit():
                used_ids.add(int(rid[3:]))
        next_id = max(used_ids, default=1) + 1

        rids = [f"rId{next_id + i}" for i in range(slide_count)]
        for i, rid in enumerate(rids):
            rel = ET.SubElement(root, f"{{{rel_ns}}}Relationship")
            rel.set("Id", rid)
            rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
            rel.set("Target", f"slides/slide{i + 1}.xml")

        tree = ET.ElementTree(root)
        tree.write(rels_path, xml_declaration=True, encoding="UTF-8")
        return rids

    def _update_content_types(self, working_dir: str, slide_count: int) -> None:
        """Update [Content_Types].xml to include all slides."""
        ct_path = os.path.join(working_dir, "[Content_Types].xml")
        if not os.path.exists(ct_path):
            return

        tree = ET.parse(ct_path)
        root = tree.getroot()

        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"

        # Remove existing slide overrides
        for override in list(root):
            part_name = override.get("PartName", "")
            if "/slides/slide" in part_name:
                root.remove(override)

        # Add slide content type overrides
        for i in range(slide_count):
            override = ET.SubElement(root, f"{{{ct_ns}}}Override")
            override.set("PartName", f"/ppt/slides/slide{i + 1}.xml")
            override.set(
                "ContentType",
                "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
            )

        tree.write(ct_path, xml_declaration=True, encoding="UTF-8")

    def _apply_metadata(self, working_dir: str, metadata: dict) -> None:
        """Apply presentation metadata to core.xml."""
        core_path = os.path.join(working_dir, "docProps", "core.xml")
        if not os.path.exists(core_path):
            return

        tree = ET.parse(core_path)
        root = tree.getroot()

        dc_ns = "http://purl.org/dc/elements/1.1/"
        cp_ns = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"

        if metadata.get("title"):
            title_elem = root.find(f"{{{dc_ns}}}title")
            if title_elem is None:
                title_elem = ET.SubElement(root, f"{{{dc_ns}}}title")
            title_elem.text = metadata["title"]

        if metadata.get("author"):
            creator_elem = root.find(f"{{{dc_ns}}}creator")
            if creator_elem is None:
                creator_elem = ET.SubElement(root, f"{{{dc_ns}}}creator")
            creator_elem.text = metadata["author"]

        if metadata.get("subject"):
            subject_elem = root.find(f"{{{dc_ns}}}subject")
            if subject_elem is None:
                subject_elem = ET.SubElement(root, f"{{{dc_ns}}}subject")
            subject_elem.text = metadata["subject"]

        tree.write(core_path, xml_declaration=True, encoding="UTF-8")

    def _repack(self, working_dir: str, output_path: str) -> None:
        """Repack the working directory into a PPTX ZIP file."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root_dir, dirs, files in os.walk(working_dir):
                for file in files:
                    file_path = os.path.join(root_dir, file)
                    arcname = os.path.relpath(file_path, working_dir)
                    zf.write(file_path, arcname)