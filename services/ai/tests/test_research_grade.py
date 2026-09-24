"""
A deck is graded against its own checklist, item by item, with evidence.

The page text is extracted by code and handed to the judge as the record;
the judge only rules. Each judge's verdict is kept separately so two judges
can be compared.
"""

import json

import pytest

from app.llm.provider import VisionLLMProvider
from app.research.grade import agreement, build_prompt, grade_run, read_verdict
from app.research.task import Task

TASK = Task(id="t", category="technical", prompt="Three slides on caching.", slides=3,
            checklist=["The deck has exactly 3 slides", "One slide shows the 304 exchange as a sequence"])


class _Judge(VisionLLMProvider):
    def __init__(self, reply):
        self.reply, self.seen = reply, None

    async def chat_with_images(self, prompt, images, temperature=0.4, max_tokens=4096):
        self.seen = (prompt, len(images))
        return self.reply


def test_the_prompt_carries_the_brief_the_pages_and_the_numbered_items():
    prompt = build_prompt(TASK, ["Cache-Control", "ETag and 304"])
    assert "Three slides on caching." in prompt
    assert "--- page 2 ---\nETag and 304" in prompt
    assert "2. One slide shows the 304 exchange as a sequence" in prompt
    assert "Today is" in prompt


def test_an_item_the_judge_skipped_or_garbled_counts_as_unanswered():
    reply = json.dumps({"items": [{"item": 1, "answer": "YES", "page": 0, "evidence": "3 pages"},
                                  {"item": "x", "answer": "yes"}]})
    items = read_verdict(TASK, reply)
    assert [i["answer"] for i in items] == ["yes", None]


def test_agreement_counts_only_items_both_judges_answered():
    a = {"items": [{"answer": "yes"}, {"answer": "no"}, {"answer": None}]}
    b = {"items": [{"answer": "yes"}, {"answer": "yes"}, {"answer": "no"}]}
    assert agreement(a, b) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_a_run_is_graded_from_its_rendered_pages(tmp_path):
    import fitz

    pages = tmp_path / "png" / "pages"
    pages.mkdir(parents=True)
    doc = fitz.open()
    for title in ("Cache-Control", "ETag", "Stale content"):
        doc.new_page(width=960, height=540).insert_text((60, 90), title, fontsize=30)
    doc.save(str(pages / "deck.pdf"))
    for i in (1, 2, 3):
        (pages / f"slide_{i}.png").write_bytes(b"\x89PNG")

    judge = _Judge(json.dumps({"items": [
        {"item": 1, "answer": "yes", "page": 0, "evidence": "Three pages."},
        {"item": 2, "answer": "no", "page": 2, "evidence": "Prose only."}]}))
    result = await grade_run(tmp_path, TASK, judge, "cheap-judge")

    assert judge.seen[1] == 3 and "Stale content" in judge.seen[0]
    assert result["passed"] == 1 and result["answered"] == 2
    assert json.loads((tmp_path / "grade-cheap-judge.json").read_text())["judge"] == "cheap-judge"
