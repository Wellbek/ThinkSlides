"""
A template as the pages it renders to.

Everything in this package measures rendered pages, and a template arrives as
either a PDF (already rendered) or a PPTX (not). Two callers needed that
conversion - the density profile and the page survey - and each doing its own
meant putting a deck through LibreOffice twice for one upload.

So the conversion is a context manager. A PDF yields itself and costs nothing;
a PPTX is converted into a temporary directory that is cleaned up on exit.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from typing import Iterator, Optional

PDF_SUFFIX = ".pdf"


@contextmanager
def as_pdf(template_path: str) -> Iterator[Optional[str]]:
    """
    Yield a path to this template rendered as a PDF, or None.

    None means there is nothing to measure - a package LibreOffice could not
    convert, or one that converted to no pages at all, which is what a .potx
    carrying layouts and no slides does.
    """
    if os.path.splitext(template_path)[1].lower() == PDF_SUFFIX:
        yield template_path if page_count(template_path) else None
        return

    from app.renderer.preview_generator import PreviewGenerator

    work_dir = tempfile.mkdtemp(prefix="ts_rendered_")
    try:
        pdf_path = PreviewGenerator.convert_to_pdf(template_path, work_dir)
        yield pdf_path if pdf_path and page_count(pdf_path) else None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def page_count(pdf_path: str) -> int:
    """How many pages a PDF has, or 0 when it cannot be opened."""
    import fitz

    try:
        doc = fitz.open(pdf_path)
    except Exception:                                     # pragma: no cover
        return 0
    try:
        return len(doc)
    finally:
        doc.close()
