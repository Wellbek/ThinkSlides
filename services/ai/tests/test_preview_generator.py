"""
Tests for PreviewGenerator.convert_to_pdf / get_pdf_path - extracted so
validate_rendered_output can reuse the same LibreOffice-rendered PDF
generate_previews already produced, instead of converting a second time.
"""

import os
import time

from app.renderer.preview_generator import PreviewGenerator


def _render_a_pptx(tmp_path) -> str:
    from app.renderer.pptx_renderer import PPTXRenderer

    renderer = PPTXRenderer(template_path=None)
    slides = [{
        "slideIndex": 0, "layoutId": "1", "layoutName": "Default",
        "contents": [{"type": "text", "placeholderIdx": 0, "runs": [{"text": "Hello"}]}],
    }]
    output_path = str(tmp_path / "out.pptx")
    renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
    return output_path


class TestConvertToPdf:
    def test_produces_a_real_pdf(self, tmp_path):
        pptx_path = _render_a_pptx(tmp_path)
        work_dir = str(tmp_path / "work")
        os.makedirs(work_dir, exist_ok=True)

        pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)

        assert pdf_path is not None
        assert os.path.exists(pdf_path)
        assert os.path.getsize(pdf_path) > 0
        with open(pdf_path, "rb") as f:
            assert f.read(5) == b"%PDF-"

    def test_returns_none_for_a_nonexistent_pptx(self, tmp_path):
        work_dir = str(tmp_path / "work")
        os.makedirs(work_dir, exist_ok=True)
        pdf_path = PreviewGenerator.convert_to_pdf(str(tmp_path / "nope.pptx"), work_dir)
        assert pdf_path is None


class TestGetPdfPath:
    def test_returns_none_when_no_previews_generated_yet(self, tmp_path):
        preview_gen = PreviewGenerator(output_dir=str(tmp_path / "previews"))
        assert preview_gen.get_pdf_path(f"nonexistent-{time.time_ns()}") is None

    def test_a_pdf_older_than_the_deck_is_not_reused(self, tmp_path):
        """A script that edits the deck after the render leaves the cached PDF
        showing the deck as it was; validation must not judge that page (#376)."""
        previews = tmp_path / "previews" / "sid"
        previews.mkdir(parents=True)
        pdf = previews / "deck.pdf"
        pdf.write_bytes(b"%PDF")
        deck = tmp_path / "deck.pptx"
        deck.write_bytes(b"x")
        preview_gen = PreviewGenerator(output_dir=str(tmp_path / "previews"))
        os.utime(pdf, (1000, 1000))
        os.utime(deck, (2000, 2000))
        assert preview_gen.get_pdf_path("sid", str(deck)) is None
        os.utime(deck, (500, 500))
        assert preview_gen.get_pdf_path("sid", str(deck)) == str(pdf)

    def test_generate_previews_persists_the_intermediate_pdf(self, tmp_path):
        pptx_path = _render_a_pptx(tmp_path)
        preview_gen = PreviewGenerator(output_dir=str(tmp_path / "previews"))
        session_id = f"test-{time.time_ns()}"

        preview_urls = preview_gen.generate_previews(pptx_path, session_id)

        assert len(preview_urls) == 1
        pdf_path = preview_gen.get_pdf_path(session_id)
        assert pdf_path is not None
        assert os.path.exists(pdf_path)
        with open(pdf_path, "rb") as f:
            assert f.read(5) == b"%PDF-"

    def test_pdf_path_is_scoped_per_session(self, tmp_path):
        pptx_path = _render_a_pptx(tmp_path)
        preview_gen = PreviewGenerator(output_dir=str(tmp_path / "previews"))
        session_a = f"a-{time.time_ns()}"
        session_b = f"b-{time.time_ns()}"

        preview_gen.generate_previews(pptx_path, session_a)

        assert preview_gen.get_pdf_path(session_a) is not None
        assert preview_gen.get_pdf_path(session_b) is None
