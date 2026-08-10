"""
Local harness: render a slide-JSON deck straight through PPTXRenderer,
convert with the same LibreOffice call the preview pipeline uses, and emit
one PNG per slide plus a contact sheet.

Usage:
    python render_deck.py <deck.json> <outdir> [--theme "#00704A"]
"""
from __future__ import annotations
import os
import sys
import json
import shutil
import tempfile
import argparse

_TMP = tempfile.mkdtemp(prefix="ts_bench_")
os.environ.setdefault("TEMPLATE_DIR", os.path.join(_TMP, "templates"))
os.environ.setdefault("OUTPUT_DIR", os.path.join(_TMP, "output"))
os.environ.setdefault("PREVIEW_OUTPUT_DIR", os.path.join(_TMP, "previews"))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://t:t@localhost:5432/t")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("LLM_PROVIDER", "zhipu")
os.environ.setdefault("ZHIPU_API_KEY", "test-key")
os.environ.setdefault("JWT_SECRET", "test")

from app.renderer.pptx_renderer import PPTXRenderer  # noqa: E402
from app.renderer.preview_generator import PreviewGenerator  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("outdir")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--template", default=None)
    args = ap.parse_args()

    with open(args.deck) as f:
        payload = json.load(f)

    slides = payload["slides"]
    metadata = payload.get("metadata", {})
    theme = args.theme or payload.get("colorTheme")

    os.makedirs(args.outdir, exist_ok=True)
    pptx_path = os.path.join(args.outdir, "deck.pptx")

    renderer = PPTXRenderer(
        template_path=args.template,
        color_theme=theme,
        design_system=payload.get("designSystem"),
    )
    renderer.render(slides, metadata, pptx_path)
    print(f"pptx: {pptx_path} ({os.path.getsize(pptx_path)} bytes)")

    pg = PreviewGenerator(output_dir=os.path.join(args.outdir, "png"))
    urls = pg.generate_previews(pptx_path, "bench")
    if not urls:
        print("PREVIEW FAILED - LibreOffice could not convert the deck", file=sys.stderr)
        return 1

    png_dir = os.path.join(args.outdir, "png", "bench")
    for name in sorted(os.listdir(png_dir)):
        print(f"  {os.path.join(png_dir, name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
