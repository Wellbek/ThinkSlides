"""
Preview Image Generator

Converts PPTX files to PNG images for slide previews.
Uses LibreOffice headless for accurate rendering.
"""

from __future__ import annotations
import os
import subprocess
import tempfile
import shutil
from typing import Optional, List
from app.observability.logging import get_logger

logger = get_logger(__name__)


class PreviewGenerator:
    """Generates PNG preview images from PPTX files."""

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self.output_dir = output_dir or os.environ.get("PREVIEW_OUTPUT_DIR", "/app/previews")
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_previews(
        self,
        pptx_path: str,
        session_id: str,
    ) -> List[str]:
        """
        Generate PNG preview images for all slides in a PPTX file.

        Args:
            pptx_path: Path to the PPTX file
            session_id: Session ID for organizing preview files

        Returns:
            List of preview URLs for each slide
        """
        if not os.path.exists(pptx_path):
            logger.error("preview_failed", reason="pptx_not_found", path=pptx_path)
            return []

        session_preview_dir = os.path.join(self.output_dir, session_id)
        os.makedirs(session_preview_dir, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix="pptx_preview_")

        try:
            pdf_path = self.convert_to_pdf(pptx_path, temp_dir)
            if not pdf_path:
                return []

            # Persist the intermediate PDF alongside the preview PNGs so
            # validate_rendered_output can inspect the exact same
            # LibreOffice-rendered geometry later without paying for a
            # second (slow) conversion - see get_pdf_path.
            try:
                shutil.copy(pdf_path, os.path.join(session_preview_dir, "deck.pdf"))
            except OSError as e:
                logger.warning("preview_pdf_persist_failed", session_id=session_id, error=str(e))

            # PDF -> one PNG per page via PyMuPDF (fitz). LibreOffice's
            # --convert-to png only exports the first slide, so rendering
            # via PDF is required to preview every slide.
            preview_urls = self._render_pdf_to_pngs(
                pdf_path, session_preview_dir, session_id
            )

            logger.info(
                "previews_generated",
                session_id=session_id,
                pptx_path=pptx_path,
                slide_count=len(preview_urls),
            )

            return preview_urls

        except Exception as e:
            logger.error("preview_failed", reason=str(e), pptx_path=pptx_path)
            return []
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def convert_to_pdf(pptx_path: str, work_dir: str) -> Optional[str]:
        """
        Convert pptx_path to PDF via LibreOffice headless, writing into
        work_dir (caller owns work_dir's lifecycle/cleanup). Returns the PDF
        path, or None on failure.

        Uses an isolated user profile to avoid lock collisions with any
        concurrent LibreOffice instance, and sets HOME so the profile is
        writable. LibreOffice returns exit code 0 even when it fails to load
        the source file, so the PDF's existence is verified rather than the
        exit code.
        """
        profile_dir = os.path.join(work_dir, "lo_profile")
        try:
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", "pdf",
                    "--outdir", work_dir,
                    pptx_path,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "HOME": work_dir},
            )
        except subprocess.TimeoutExpired:
            logger.error("pdf_conversion_failed", reason="timeout", pptx_path=pptx_path)
            return None

        base_name = os.path.splitext(os.path.basename(pptx_path))[0]
        pdf_path = os.path.join(work_dir, f"{base_name}.pdf")

        if not os.path.exists(pdf_path):
            logger.error(
                "pdf_conversion_failed",
                reason="libreoffice_no_pdf",
                stderr=result.stderr,
                stdout=result.stdout,
                pptx_path=pptx_path,
            )
            return None

        return pdf_path

    def get_pdf_path(self, session_id: str, pptx_path: Optional[str] = None) -> Optional[str]:
        """The intermediate PDF generate_previews already produced for this
        session, if any - lets validate_rendered_output reuse the exact same
        LibreOffice-rendered geometry instead of converting a second time.

        Given the deck it is meant to describe, a PDF older than that deck is
        not returned: a script that edited the file after the render left the
        PDF showing the deck as it was, and validation judged a page the user
        would never see (#376).
        """
        pdf_path = os.path.join(self.output_dir, session_id, "deck.pdf")
        if not os.path.exists(pdf_path):
            return None
        if pptx_path and os.path.exists(pptx_path) \
                and os.path.getmtime(pdf_path) < os.path.getmtime(pptx_path):
            return None
        return pdf_path

    @staticmethod
    def _render_pdf_to_pngs(
        pdf_path: str,
        out_dir: str,
        session_id: str,
    ) -> List[str]:
        """Render each page of a PDF to a PNG preview, return preview URLs."""
        import fitz  # PyMuPDF

        preview_urls: List[str] = []
        doc = fitz.open(pdf_path)
        try:
            zoom = 2.0  # ~2x for readable thumbnails
            matrix = fitz.Matrix(zoom, zoom)
            for page_index in range(len(doc)):
                page = doc[page_index]
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                dest_name = f"slide_{page_index + 1}.png"
                dest_path = os.path.join(out_dir, dest_name)
                pix.save(dest_path)
                preview_urls.append(f"/previews/{session_id}/{dest_name}")
        finally:
            doc.close()

        return preview_urls

    def get_preview_url(
        self,
        session_id: str,
        slide_index: int,
    ) -> Optional[str]:
        """
        Get the preview URL for a specific slide.

        Args:
            session_id: Session ID
            slide_index: 0-based slide index

        Returns:
            Preview URL or None if not found
        """
        preview_file = os.path.join(
            self.output_dir,
            session_id,
            f"slide_{slide_index+1}.png"
        )

        if os.path.exists(preview_file):
            return f"/previews/{session_id}/slide_{slide_index+1}.png"

        return None

    def cleanup_session(self, session_id: str) -> None:
        """
        Clean up preview files for a session.

        Args:
            session_id: Session ID to clean up
        """
        session_dir = os.path.join(self.output_dir, session_id)

        try:
            if os.path.exists(session_dir):
                shutil.rmtree(session_dir)
                logger.info("previews_cleaned", session_id=session_id)
        except Exception as e:
            logger.error("cleanup_failed", session_id=session_id, error=str(e))