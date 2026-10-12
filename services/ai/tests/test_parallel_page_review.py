"""
Every page judged at once (#323's plan, section 13.1: per-page critique in
parallel), each page failing alone, and replay keeping a verdict on its page.
"""

import asyncio
import json

import pytest

from app.agent import run_context
from app.agent.tools.validate_rendered_output import ValidateRenderedOutputTool
from app.research.exchange import Cassette, RecordingVisionProvider, ReplayVisionProvider


@pytest.fixture
def three_pages(tmp_path):
    import fitz

    doc = fitz.open()
    for n in range(3):
        page = doc.new_page(width=960, height=540)
        page.insert_text((60, 90), f"Page {n + 1}", fontsize=36)
    path = tmp_path / "deck.pdf"
    doc.save(str(path))
    return str(path)


class _Judge:
    """Says page 2 has an issue, fails on page 3, and answers only once two
    pages are being judged at the same time."""

    def __init__(self):
        self.in_flight = 0
        self.most = 0
        self.order: list[str] = []
        self.together = asyncio.Event()

    async def chat_with_images(self, prompt, images, temperature=0.2, max_tokens=250):
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        if self.most >= 2:
            self.together.set()
        await asyncio.wait_for(self.together.wait(), timeout=2)
        self.in_flight -= 1
        index = self.order.index(images[0])
        if index == 2:
            raise RuntimeError("the provider failed on this page")
        return json.dumps({"hasIssue": index == 1, "issue": f"page {index + 1} is off",
                           "fix": "replace"})


@pytest.mark.asyncio
async def test_pages_are_judged_at_once_and_each_fails_alone(three_pages, monkeypatch):
    import base64

    import fitz

    judge = _Judge()
    doc = fitz.open(three_pages)
    judge.order = [
        "data:image/png;base64," + base64.b64encode(
            doc[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
        ).decode() for i in range(len(doc))]
    monkeypatch.setattr(run_context, "get_vision_provider", lambda: judge)

    issues = await ValidateRenderedOutputTool._check_aesthetics(three_pages)

    assert judge.most >= 2
    assert [(i["slideIndex"], i["detail"]) for i in issues] == [(1, "page 2 is off")]


@pytest.mark.asyncio
async def test_a_replayed_verdict_lands_on_the_page_it_was_given_for(tmp_path):
    class _Inner:
        async def chat_with_images(self, prompt, images, *a, **k):
            return f"verdict for {images[0]}"

    cassette = Cassette(tmp_path / "c.jsonl")
    recording = RecordingVisionProvider(_Inner(), cassette)
    # Recorded in the order the answers came back: page 2 first.
    await recording.chat_with_images("judge this page", ["page-2"])
    await recording.chat_with_images("judge this page", ["page-1"])

    replay = ReplayVisionProvider(Cassette(tmp_path / "c.jsonl"))
    first = await replay.chat_with_images("judge this page", ["page-1"])
    second = await replay.chat_with_images("judge this page", ["page-2"])

    assert (first, second) == ("verdict for page-1", "verdict for page-2")
