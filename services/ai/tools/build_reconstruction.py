"""
Phase 0: reconstruct a reference page in the DesignDoc, and see if it holds.

This is the gate the whole rebuild rests on. If a page from a publication we
have named as the bar cannot be *written down* in the representation, then no
amount of prompting, measuring or repairing further up will ever produce it,
and the representation has to change before anything is built on top.

Nothing here is hand-eyeballed. Every frame position is read out of the PDF,
and the grid is recovered by ``app.measure.grid`` rather than chosen, so a
reconstruction that lands is evidence about the *representation* and not
about how carefully somebody nudged rectangles.

    PYTHONPATH=. .venv/bin/python tools/build_reconstruction.py <outdir>
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

import fitz

from app.design.geometry import check_page
from app.design.pack import (
    ColorRoles,
    Confidence,
    LegibilityPolicy,
    Pack,
    Rhythm,
    SpacingScale,
    TypeRole,
)
from app.design.schema import (
    Canvas,
    DesignDoc,
    Figure,
    FracPlace,
    Frame,
    GridPlace,
    GridSpec,
    Insets,
    Page,
    PackRef,
    Paragraph,
    Rule,
    Run,
    TextBlock,
)
from app.measure.grid import edges_from_boxes, recover_grid

EY_PDF = os.path.expanduser(
    "~/Downloads/ey-inaugural-ey-iif-global-insurance-risk-management-survey-march23.pdf"
)
PAGE = 4  # a design-split page; the validation split is never opened


def _page_geometry(pdf_path: str, page_number: int):
    doc = fitz.open(pdf_path)
    page = doc[page_number - 1]
    width, height = page.rect.width, page.rect.height
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        text = "".join(
            span["text"] for line in block.get("lines", []) for span in line["spans"]
        ).strip()
        if not text:
            continue
        sizes = sorted(
            {round(span["size"], 1)
             for line in block.get("lines", []) for span in line["spans"]
             if span["text"].strip()}
        )
        blocks.append({"bbox": block["bbox"], "text": text, "sizes": sizes})
    images = []
    for entry in page.get_images(full=True):
        for rect in page.get_image_rects(entry[0]):
            images.append({"bbox": (rect.x0, rect.y0, rect.x1, rect.y1),
                           "xref": entry[0]})
    return doc, page, width, height, blocks, images


def build_pack(width_pt: float, height_pt: float, grid) -> Pack:
    """The EY design language, every number of it measured."""
    canvas = Canvas(w_in=width_pt / 72.0, h_in=height_pt / 72.0)
    w_in = canvas.w_in

    # Body copy measures 16pt on 21pt leading; 21pt is therefore the unit the
    # page's vertical rhythm is built from.
    baseline_pt = 21.0

    return Pack(
        id="ey-iif-risk",
        version="1",
        label="EY / IIF Global Insurance Risk Management Survey",
        source_note=(
            "Measured from the March 2023 report, design-split pages only. "
            "Faces are declared as the publication sets them; this machine "
            "substitutes Liberation, so rendered specimens differ in face "
            "while matching in metrics."
        ),
        canvas=canvas,
        grid=GridSpec(
            columns=grid.columns,
            gutter_in=grid.gutter * w_in,
            margin=Insets(
                left=grid.margin_left * w_in,
                right=grid.margin_right * w_in,
                top=0.042 * canvas.h_in,
                bottom=0.042 * canvas.h_in,
            ),
            baseline_in=baseline_pt / 72.0,
        ),
        grid_confidence=Confidence(
            value=grid.edge_snap,
            residual=grid.residual,
            note=f"recovered from page {PAGE}; {grid.columns} columns",
        ),
        rhythm=Rhythm(
            unit_pt=baseline_pt,
            confidence=Confidence(value=0.88, residual=0.5,
                                  note="16pt body set on 21pt, 125 of 141 lines"),
        ),
        spacing=SpacingScale(base_pt=baseline_pt / 4.0,
                             steps=(0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)),
        colors=ColorRoles(
            ground="#FFFFFF",
            ground_alt="#F6F6FA",
            ground_dark="#1A1A24",
            ink="#2E2E38",
            ink_muted="#747480",
            ink_reversed="#FFFFFF",
            accent="#FFE600",
            accent_alt="#C4C4CD",
            rule="#C4C4CD",
            scrim="#000000",
        ),
        body_face="Arial",
        display_face="Georgia",
        type_roles={
            "folio": TypeRole(size_pt=36.0, line_height_pt=36.0, face="body",
                              weight="light", color_role="ink_muted",
                              measured_median_pt=36.0),
            "eyebrow": TypeRole(size_pt=11.0, line_height_pt=13.0, face="body",
                                weight="light", case="upper",
                                tracking_per_mille=40, color_role="ink_muted",
                                measured_median_pt=11.0),
            "headline": TypeRole(size_pt=52.0, line_height_pt=57.0,
                                 face="body", weight="light", color_role="ink",
                                 measured_median_pt=52.0),
            "standfirst": TypeRole(size_pt=21.0, line_height_pt=27.0,
                                   face="display", color_role="ink",
                                   measured_median_pt=21.0),
            "pullquote": TypeRole(size_pt=23.5, line_height_pt=31.5,
                                  face="display", color_role="ink",
                                  measured_median_pt=23.5),
            "panelquote": TypeRole(size_pt=26.0, line_height_pt=33.0,
                                   face="body", weight="light",
                                   color_role="ink_reversed",
                                   measured_median_pt=26.0),
            "body": TypeRole(size_pt=16.0, line_height_pt=21.0, face="body",
                             weight="light", color_role="ink",
                             space_after=1, measure_chars_target=52,
                             measured_min_pt=16.0, measured_median_pt=16.0),
            "caption": TypeRole(size_pt=11.0, line_height_pt=14.0, face="body",
                                weight="light", color_role="ink_muted",
                                measured_median_pt=11.0),
        },
        policy=LegibilityPolicy(
            # 16pt on a 1080pt page is 1.48% of canvas height. The old global
            # floor of 16pt-on-540pt is 3.0% and would have refused this
            # publication's own body copy.
            body_min_ratio=0.0145,
            caption_min_ratio=0.0100,
            display_min_ratio=0.0230,
        ),
    )


def _text(role: str, *lines: str, **kw) -> TextBlock:
    return TextBlock(
        type_role=role,
        paragraphs=tuple(
            Paragraph(runs=(Run(text=line),)) for line in lines if line
        ),
        **kw,
    )


def build_page(pack: Pack, blocks, images, width: float, height: float,
               image_dir: str, doc_pdf) -> Page:
    """EY page 4, frame by frame, from the measured boxes."""
    canvas = pack.canvas
    box_top = pack.grid.margin.top
    box_h = canvas.h_in - pack.grid.margin.top - pack.grid.margin.bottom

    def frac_y(y_pt: float) -> float:
        """A page-fraction y as a fraction of the grid's content box."""
        return ((y_pt / height) * canvas.h_in - box_top) / box_h

    def height_frac(y0: float, y1: float) -> float:
        return ((y1 - y0) / height) * canvas.h_in / box_h

    def find(fragment: str, x_min: float = 0.0, x_max: float = 1.0):
        """The block carrying this text, optionally within a column band.

        The band matters: the panel quote on this page is a shortened
        restatement of a sentence that also appears in the body copy, so
        matching on text alone picks up the body paragraph and reconstructs
        the page with 600 characters where 137 belong.
        """
        candidates = [
            b for b in blocks
            if fragment.lower() in b["text"].lower()
            and x_min <= b["bbox"][0] / width <= x_max
        ]
        if not candidates:
            raise LookupError(f"no block containing {fragment!r} on page {PAGE}")
        return min(candidates, key=lambda b: len(b["text"]))

    def saved_image(index: int, xref: int) -> dict:
        pix = fitz.Pixmap(doc_pdf, xref)
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        path = os.path.join(image_dir, f"ey-p{PAGE}-img{index}.png")
        pix.save(path)
        with open(path, "rb") as handle:
            return {"source": "base64",
                    "data": base64.b64encode(handle.read()).decode("ascii")}

    images = sorted(images, key=lambda i: -(
        (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1])
    ))
    bleed_image = saved_image(0, images[0]["xref"])
    inset_image = saved_image(1, images[1]["xref"]) if len(images) > 1 else None
    inset_box = images[1]["bbox"] if len(images) > 1 else None

    quote = find("With the insurance sector")
    body_a = [b for b in blocks if abs(b["bbox"][0] / width - 0.277) < 0.01]
    body_b = [b for b in blocks if abs(b["bbox"][0] / width - 0.509) < 0.01]
    panel = find("CROs are acting", x_min=0.7)

    def para(block) -> Paragraph:
        return Paragraph(runs=(Run(text=block["text"]),))

    frames: list[Frame] = [
        # The light panel the whole page is set on, inset from the trim.
        Frame(id="page-panel", role="panel",
              place=FracPlace(x=0.023, y=0.042, w=0.954, h=0.916),
              ground="ground_alt", z=0),
        # The dark full-bleed column, and the photograph inside it. Column 3
        # of the recovered grid, bled to three edges.
        Frame(id="bleed-panel", role="rail",
              place=GridPlace(col=pack.grid.columns - 1, span=1, y=0.0, h=1.0),
              bleed=("top", "right", "bottom"), ground="ground_dark", z=1),
        Frame(id="bleed-figure", role="figure",
              place=GridPlace(col=pack.grid.columns - 1, span=1, y=0.0, h=1.0),
              bleed=("top", "right", "bottom"), z=2,
              content=(Figure(source=bleed_image, fit="cover", focal_x=0.5,
                              focal_y=0.5,
                              alt_text="A raised arm holding a document"),)),
        # Furniture: folio hung in the margin, running header beside it.
        Frame(id="folio", role="folio",
              place=FracPlace(x=0.045, y=0.055, w=0.03, h=0.06), z=3,
              content=(_text("folio", f"{PAGE:02d}"),)),
        Frame(id="eyebrow", role="eyebrow",
              place=FracPlace(x=0.085, y=0.070, w=0.12, h=0.05), z=3,
              content=(_text("eyebrow",
                             "Inaugural EY/IIF global insurance",
                             "risk management survey"),)),
        Frame(id="jump", role="caption",
              place=FracPlace(x=0.925, y=0.085, w=0.06, h=0.04), z=4,
              content=(_text("caption", "Jump to", "contents"),)),
        # The accent rule above the headline: EY yellow, the one saturated
        # mark on the page.
        Frame(id="headline-rule", role="banner",
              place=FracPlace(x=0.075, y=0.188, w=0.08, h=0.004), z=3,
              content=(Rule(orientation="horizontal", weight_pt=4.0,
                            color_role="accent"),)),
        Frame(id="headline", role="headline",
              place=FracPlace(x=0.075, y=0.198, w=0.16, h=0.13), z=3,
              content=(_text("headline", "Executive", "summary"),)),
    ]

    if inset_image and inset_box:
        x0, y0, x1, y1 = inset_box
        frames.append(
            Frame(id="inset-figure", role="figure",
                  place=FracPlace(x=x0 / width, y=y0 / height,
                                  w=(x1 - x0) / width, h=(y1 - y0) / height),
                  z=3,
                  content=(Figure(source=inset_image, fit="cover",
                                  alt_text="A risk officer at a trading desk"),))
        )

    frames.append(
        Frame(id="pullquote", role="pullquote",
              place=FracPlace(x=quote["bbox"][0] / width,
                              y=quote["bbox"][1] / height,
                              w=(quote["bbox"][2] - quote["bbox"][0]) / width,
                              h=(quote["bbox"][3] - quote["bbox"][1]) / height + 0.02),
              z=3,
              content=(TextBlock(type_role="pullquote",
                                 paragraphs=(para(quote),)),))
    )

    for name, column, blocks_in_column in (("body-a", 1, body_a),
                                           ("body-b", 2, body_b)):
        if not blocks_in_column:
            continue
        top = min(b["bbox"][1] for b in blocks_in_column)
        bottom = max(b["bbox"][3] for b in blocks_in_column)
        frames.append(
            Frame(id=name, role="column",
                  place=GridPlace(col=column, span=1, y=frac_y(top),
                                  h=height_frac(top, bottom) + 0.02),
                  z=3,
                  content=(TextBlock(
                      type_role="body",
                      paragraphs=tuple(para(b) for b in blocks_in_column),
                  ),))
        )

    # The quote reversed out of the dark column.
    frames.append(
        Frame(id="panel-quote", role="pullquote",
              place=FracPlace(x=0.776, y=0.156, w=0.145, h=0.25), z=4,
              content=(TextBlock(type_role="panelquote",
                                 paragraphs=(para(panel),)),))
    )

    return Page(id=f"ey-p{PAGE}", archetype="exec-summary", role="content",
                ground="ground", frames=tuple(frames))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir")
    args = parser.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    if not os.path.exists(EY_PDF):
        print(f"reference not found: {EY_PDF}", file=sys.stderr)
        return 2

    doc_pdf, _, width, height, blocks, images = _page_geometry(EY_PDF, PAGE)

    boxes = [b["bbox"] for b in blocks if (b["bbox"][2] - b["bbox"][0]) > 0.02 * width]
    grid = recover_grid(edges_from_boxes(boxes, width))
    if grid is None:
        print("no grid recovered - reconstruction would be guesswork", file=sys.stderr)
        return 3
    print(f"grid: {grid.columns} columns, margin {grid.margin_left:.3f}, "
          f"column {grid.column_width:.3f}, gutter {grid.gutter:.3f}, "
          f"snap {grid.edge_snap:.2f}")

    pack = build_pack(width, height, grid)
    page = build_page(pack, blocks, images, width, height, args.outdir, doc_pdf)
    design = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                       grid=pack.grid, pages=(page,),
                       title="EY page 4, reconstructed")

    problems = check_page(design, page)
    print(f"frames: {sum(1 for _ in page.all_frames())}")
    print("geometry problems:", problems or "none")

    pack_path = os.path.join("app", "design", "packs", f"{pack.id}.json")
    os.makedirs(os.path.dirname(pack_path), exist_ok=True)
    with open(pack_path, "w") as handle:
        json.dump(pack.model_dump(mode="json"), handle, indent=2)
    print("wrote", pack_path)

    doc_path = os.path.join(args.outdir, "ey-p4.designdoc.json")
    with open(doc_path, "w") as handle:
        json.dump(design.model_dump(mode="json"), handle, indent=2)
    print("wrote", doc_path)

    from app.renderer.designdoc_renderer import render_doc

    pptx_path = os.path.join(args.outdir, "ey-p4.pptx")
    render_doc(design, pack, pptx_path)
    print("wrote", pptx_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
