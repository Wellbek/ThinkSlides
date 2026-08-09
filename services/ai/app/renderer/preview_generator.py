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
            # Step 1: PPTX -> PDF via LibreOffice headless.
            # Use an isolated user profile to avoid lock collisions with any
            # concurrent LibreOffice instance, and set HOME so the profile is
            # writable. Note: LibreOffice returns exit code 0 even when it
            # fails to load the source file, so we verify the PDF was created.
            profile_dir = os.path.join(temp_dir, "lo_profile")
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", "pdf",
                    "--outdir", temp_dir,
                    pptx_path,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "HOME": temp_dir},
            )

            base_name = os.path.splitext(os.path.basename(pptx_path))[0]
            pdf_path = os.path.join(temp_dir, f"{base_name}.pdf")

            if not os.path.exists(pdf_path):
                logger.error(
                    "preview_failed",
                    reason="libreoffice_no_pdf",
                    stderr=result.stderr,
                    stdout=result.stdout,
                    pptx_path=pptx_path,
                )
                return []

            # Step 2: PDF -> one PNG per page via PyMuPDF (fitz).
            # LibreOffice's --convert-to png only exports the first slide, so
            # rendering via PDF is required to preview every slide.
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

        except subprocess.TimeoutExpired:
            logger.error("preview_failed", reason="timeout", pptx_path=pptx_path)
            return []
        except Exception as e:
            logger.error("preview_failed", reason=str(e), pptx_path=pptx_path)
            return []
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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