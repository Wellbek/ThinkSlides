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
from pathlib import Path
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
            # Use LibreOffice headless to convert PPTX to PNG
            # LibreOffice exports each slide as a separate PNG
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to", "png",
                    "--outdir", temp_dir,
                    pptx_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.error(
                    "preview_failed",
                    reason="libreoffice_error",
                    stderr=result.stderr,
                    pptx_path=pptx_path,
                )
                # Fallback: return empty list but don't fail the entire render
                return []

            # Find generated PNG files and organize them
            png_files = sorted(
                [f for f in os.listdir(temp_dir) if f.endswith(".png")]
            )

            preview_urls = []
            for i, png_file in enumerate(png_files):
                src_path = os.path.join(temp_dir, png_file)
                dest_name = f"slide_{i+1}.png"
                dest_path = os.path.join(session_preview_dir, dest_name)

                shutil.move(src_path, dest_path)

                # Generate URL path (will be served via orchestrator or S3)
                preview_url = f"/previews/{session_id}/{dest_name}"
                preview_urls.append(preview_url)

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