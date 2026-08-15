"""
What a design system looks like, before there is a deck to look at.

Preview PNGs exist as a by-product of a finished deck: render the PPTX,
convert it through LibreOffice, rasterise the pages. To show a user three
candidate design systems *before* anything is generated, there has to be a way
to preview a design system that has no deck behind it.

So: a specimen deck. Three fixed slides that exercise the parts of a design
system a user needs to see -

1. the **cover**, for the display face and how the brand tone is deployed;
2. a **dense content slide**, for the body scale, the density posture, the
   rules and bands - this is the one that actually distinguishes directions;
3. a **chart slide**, for the data-viz tokens: series colours, axis treatment,
   labels, table banding.

The copy is the same lorem ipsum in every direction, deliberately. Holding the
content constant is what makes three previews a comparison of *design* rather
than a comparison of writing.

And they go through the real ``PPTXRenderer``, not an approximation of it, so
a design system that renders badly previews badly. A preview that flatters a
direction the deck will not honour is worse than no preview.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any, Optional, Sequence

from app.observability.logging import get_logger
from app.renderer.preview_generator import PreviewGenerator

logger = get_logger(__name__)

# Where a direction's specimens live under the previews root. Throwaway by
# design: a run that never chooses a direction must not leave nine PNGs
# behind, so the whole directory goes when the session ends or a direction is
# chosen.
DIRECTION_DIR = "direction"
SPECIMEN_ZOOM = 2.0


def specimen_slides() -> list[dict]:
    """
    The three specimen slides, as slide JSON the renderer already understands.

    Deliberately built from the plain content vocabulary rather than the block
    vocabulary: a specimen has to render under *any* design system, including
    one whose grammar declares no regions, and the point is to show the type,
    the grounds and the data-viz tokens rather than to show off the renderer.
    """
    return [
        {
            "slideIndex": 0,
            "layoutId": "title",
            "layoutName": "Title Slide",
            "titleRegister": "label",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [
                    {"text": "Lorem ipsum dolor sit amet", "bold": True},
                ]},
                {"type": "text", "placeholderIdx": 1, "runs": [
                    {"text": "Consectetur adipiscing elit, sed do eiusmod",
                     "bold": False},
                ]},
            ],
        },
        {
            "slideIndex": 1,
            "layoutId": "content",
            "layoutName": "Content",
            "section": "Lorem ipsum",
            "titleRegister": "claim",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [
                    {"text": "Tempor incididunt rose 24 percent across every "
                             "measured segment", "bold": True},
                ]},
                {"type": "text", "placeholderIdx": 1, "runs": [
                    {"text": "Ut enim ad minim veniam, quis nostrud "
                             "exercitation ullamco laboris nisi ut aliquip.",
                     "bullet": True, "level": 0},
                    {"text": "Duis aute irure dolor in reprehenderit in "
                             "voluptate velit esse cillum dolore eu fugiat.",
                     "bullet": True, "level": 0},
                    {"text": "Excepteur sint occaecat cupidatat non proident, "
                             "sunt in culpa qui officia deserunt mollit.",
                     "bullet": True, "level": 0},
                    {"text": "Sed ut perspiciatis unde omnis iste natus error "
                             "sit voluptatem accusantium doloremque.",
                     "bullet": True, "level": 0},
                    {"text": "Nemo enim ipsam voluptatem quia voluptas sit "
                             "aspernatur aut odit aut fugit, sed quia.",
                     "bullet": True, "level": 0},
                ]},
            ],
        },
        {
            "slideIndex": 2,
            "layoutId": "chart",
            "layoutName": "Chart",
            "section": "Lorem ipsum",
            "titleRegister": "claim",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [
                    {"text": "Voluptatem accusantium grew in every one of the "
                             "five measured years", "bold": True},
                ]},
                {"type": "chart", "chart": {
                    "chartType": "bar",
                    "title": "Lorem ipsum, percent",
                    "categories": ["2020", "2021", "2022", "2023", "2024"],
                    "series": [
                        {"name": "Dolor", "values": [18.0, 27.0, 36.0, 45.0, 54.0]},
                        {"name": "Amet", "values": [12.0, 15.0, 21.0, 26.0, 33.0]},
                    ],
                }},
            ],
        },
    ]


def render_specimens(
    design_system: dict,
    session_id: str,
    direction_id: str,
    output_dir: Optional[str] = None,
) -> list[str]:
    """
    Render the specimen deck under ``design_system``, returning preview URLs.

    One LibreOffice conversion for the whole three-slide deck rather than one
    per slide: conversion is the slow step by an order of magnitude, and the
    difference between three conversions and nine decides whether this fits
    inside a chat pause.
    """
    from app.renderer.pptx_renderer import PPTXRenderer

    generator = PreviewGenerator(output_dir)
    target = _direction_dir(generator.output_dir, session_id, direction_id)
    os.makedirs(target, exist_ok=True)

    work_dir = tempfile.mkdtemp(prefix=f"ts_specimen_{direction_id}_")
    try:
        pptx_path = os.path.join(work_dir, "specimen.pptx")
        PPTXRenderer(
            template_path=None,
            color_theme=str((design_system or {}).get("colorTheme") or "") or None,
            design_system=design_system,
        ).render(
            slides=specimen_slides(),
            metadata={"title": str((design_system or {}).get("name")
                                   or "Design direction")},
            output_path=pptx_path,
        )
        pdf_path = generator.convert_to_pdf(pptx_path, work_dir)
        if not pdf_path:
            logger.error("specimen_conversion_failed", direction_id=direction_id)
            return []
        urls = _rasterise(pdf_path, target, session_id, direction_id)
        logger.info("specimens_rendered", direction_id=direction_id,
                    session_id=session_id, count=len(urls))
        return urls
    except Exception as e:  # pragma: no cover - a direction that will not render
        logger.error("specimen_failed", direction_id=direction_id, error=str(e))
        return []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def render_directions(
    directions: Sequence[dict],
    session_id: str,
    output_dir: Optional[str] = None,
) -> dict[str, list[str]]:
    """
    Render every direction's specimens at once, keyed by direction id.

    Concurrently, because this sits inside a chat pause and the user is
    watching: three conversions in sequence is three times the wait for no
    reason. Each LibreOffice runs against its own ``UserInstallation`` profile
    - the isolation that already exists in ``convert_to_pdf`` is exactly what
    makes concurrent instances safe.

    One direction failing to render does not take the others with it. A user
    offered two of three directions has still been offered a choice; a user
    offered an error has not.
    """
    async def one(direction: dict) -> tuple[str, list[str]]:
        direction_id = str(direction.get("id") or "")
        urls = await asyncio.to_thread(
            render_specimens, direction.get("designSystem") or {},
            session_id, direction_id, output_dir,
        )
        return direction_id, urls

    results = await asyncio.gather(
        *(one(direction) for direction in directions if direction.get("id")),
        return_exceptions=True,
    )
    rendered: dict[str, list[str]] = {}
    for result in results:
        if isinstance(result, BaseException):
            logger.error("specimen_direction_failed", error=str(result))
            continue
        direction_id, urls = result
        rendered[direction_id] = urls
    return rendered


def cleanup_directions(session_id: str, output_dir: Optional[str] = None,
                       keep: Optional[str] = None) -> None:
    """
    Remove a session's direction specimens.

    Called when a direction is chosen and again when the session ends. They
    are throwaway by design, and an abandoned run that leaves nine PNGs behind
    is nine per abandoned run for ever.
    """
    root = os.path.join(
        (PreviewGenerator(output_dir).output_dir), session_id, DIRECTION_DIR
    )
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        if keep and name == keep:
            continue
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    if not os.listdir(root):
        shutil.rmtree(root, ignore_errors=True)
    logger.info("direction_previews_cleaned", session_id=session_id, kept=keep)


def specimen_urls(session_id: str, direction_id: str,
                  output_dir: Optional[str] = None) -> list[str]:
    """The specimen URLs already on disk for a direction, in order."""
    target = _direction_dir(PreviewGenerator(output_dir).output_dir,
                            session_id, direction_id)
    if not os.path.isdir(target):
        return []
    names = sorted(
        (n for n in os.listdir(target) if n.endswith(".png")),
        key=lambda n: int(os.path.splitext(n)[0]) if n[:-4].isdigit() else 0,
    )
    return [f"/previews/{session_id}/{DIRECTION_DIR}/{direction_id}/{n}"
            for n in names]


# ── Internals ─────────────────────────────────────────────────────────

def _direction_dir(root: str, session_id: str, direction_id: str) -> str:
    return os.path.join(root, session_id, DIRECTION_DIR, direction_id)


def _rasterise(pdf_path: str, target: str, session_id: str,
               direction_id: str) -> list[str]:
    import fitz

    urls: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        matrix = fitz.Matrix(SPECIMEN_ZOOM, SPECIMEN_ZOOM)
        for index in range(len(doc)):
            name = f"{index + 1}.png"
            doc[index].get_pixmap(matrix=matrix, alpha=False).save(
                os.path.join(target, name)
            )
            urls.append(
                f"/previews/{session_id}/{DIRECTION_DIR}/{direction_id}/{name}"
            )
    finally:
        doc.close()
    return urls


def describe(design_system: dict) -> dict[str, Any]:
    """
    The few facts about a design system a preview cannot show at thumbnail
    size, for logging and for the distinctness check in #113.
    """
    typography = (design_system or {}).get("typography") or {}
    density = (design_system or {}).get("density") or {}
    return {
        "displayFace": (typography.get("titleFont") or {}).get("typeface"),
        "bodyFace": (typography.get("bodyFont") or {}).get("typeface"),
        "displayIsSerif": bool(typography.get("displayIsSerif")),
        "wordsMedian": (density.get("wordsPerSlide") or {}).get("median"),
        "blocksMedian": (density.get("blocksPerSlide") or {}).get("median"),
    }
