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
from app.observability.logging import get_logger

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
        working_dir = tempfile.mkdtemp(prefix="slideagent_render_")

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
        from pptx import Presentation
        from pptx.util import Inches, Pt, Emu

        prs = Presentation()
        prs.slide_width = Emu(9144000)
        prs.slide_height = Emu(6858000)

        blank_layout = None
        for layout in prs.slide_layouts:
            if layout.name and "blank" in layout.name.lower():
                blank_layout = layout
                break
        if blank_layout is None:
            blank_layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]

        for slide_data in slides:
            slide = prs.slides.add_slide(blank_layout)

            for content in slide_data.get("contents", []):
                if content.get("type") == "text":
                    runs = content.get("runs", [])
                    if not runs:
                        continue

                    left = Inches(0.5)
                    top = Inches(0.5)
                    width = Inches(9)
                    height = Inches(5)

                    is_title = any(
                        r.get("bold", False) or r.get("fontSizePt", 14) >= 20
                        for r in runs
                    )
                    if is_title:
                        top = Inches(0.3)
                        height = Inches(1.5)

                    txBox = slide.shapes.add_textbox(left, top, width, height)
                    tf = txBox.text_frame
                    tf.word_wrap = True

                    for j, run_data in enumerate(runs):
                        if j == 0:
                            p = tf.paragraphs[0]
                        else:
                            p = tf.add_paragraph()

                        run = p.add_run()
                        run.text = run_data.get("text", "")

                        font_size = run_data.get("fontSizePt")
                        if font_size:
                            run.font.size = Pt(font_size)
                        if run_data.get("bold"):
                            run.font.bold = True
                        if run_data.get("italic"):
                            run.font.italic = True

                        if run_data.get("bullet"):
                            p.level = run_data.get("level", 0)

                elif content.get("type") == "image":
                    img = content.get("image", {})
                    if img.get("source") == "base64":
                        import base64
                        img_data = base64.b64decode(img.get("data", ""))
                        img_stream = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        img_stream.write(img_data)
                        img_stream.close()
                        try:
                            slide.shapes.add_picture(
                                img_stream.name,
                                Inches(1), Inches(1.5),
                                Inches(4), Inches(3),
                            )
                        finally:
                            os.unlink(img_stream.name)

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
                        for run_data in runs[len(paragraphs):]:
                            new_para = ET.SubElement(txBody, f"{{{NS['a']}}}p")
                            a_r = ET.SubElement(new_para, f"{{{NS['a']}}}r")
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

        # Find the <p:sldIdLst> element
        sldIdLst = root.find(".//p:sldIdLst", NS)
        if sldIdLst is None:
            return

        # Remove existing slide ID entries
        for sldId in list(sldIdLst):
            sldIdLst.remove(sldId)

        # Add new slide ID entries
        # Slide IDs start from 256 by convention
        for i in range(slide_count):
            rid = f"rId{i + 2}"  # rId1 is typically the slideMaster
            sldId = ET.SubElement(sldIdLst, f"{{{NS['p']}}}sldId")
            sldId.set("id", str(256 + i))
            sldId.set(f"{{{NS['r']}}}id", rid)

        # Update presentation.xml.rels to include slide references
        self._update_presentation_rels(working_dir, slide_count)

        tree.write(pres_path, xml_declaration=True, encoding="UTF-8")

    def _update_presentation_rels(self, working_dir: str, slide_count: int) -> None:
        """Update presentation.xml.rels to reference all slides."""
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

        # Remove existing slide relationships
        for rel in list(root):
            rel_type = rel.get("Type", "")
            if "slide" in rel_type and "layout" not in rel_type and "master" not in rel_type:
                root.remove(rel)

        # Add slide relationships
        for i in range(slide_count):
            rid = f"rId{i + 2}"
            rel = ET.SubElement(root, f"{{{rel_ns}}}Relationship")
            rel.set("Id", rid)
            rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
            rel.set("Target", f"slides/slide{i + 1}.xml")

        tree = ET.ElementTree(root)
        tree.write(rels_path, xml_declaration=True, encoding="UTF-8")

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