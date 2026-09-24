"""
Grade a finished deck against its task's checklist.

Extraction and judgement are separate, as SlidesGen-Bench found more
reliable than asking a judge to read and rule in one breath (R-403): the
page text is read deterministically from the rendered PDF and handed to the
judge as the record, and the page images go alongside for what text cannot
show - whether an exchange is drawn as a sequence, whether a chart plots one
measure per axis. The judge answers every item yes or no, citing the page
it found the answer on.

The judge is whichever vision provider the caller hands in: a Claude Code
session for the frontier reference, or a metered cheap model. Each judge's
verdict is written to grade-<judge>.json next to the run, so verdicts from
different judges sit side by side and can be compared for agreement.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from app.agent.tools._json_extract import extract_json_object
from app.agent.tools._today import today_line
from app.llm.provider import VisionLLMProvider
from app.research.task import Task

PROMPT = """You are grading a finished slide deck against the checklist its brief was written with.

The brief:
{brief}

What each page says, extracted from the rendered deck (page numbers match the images, in order):
{pages}

The images are the same pages, rendered. Use them for anything the text cannot show - how something is drawn, what a chart plots, whether a page is legible.

Answer every checklist item strictly: "yes" only when the deck plainly does what the item asks, "no" otherwise. Cite the page number that decides it, and quote or describe the evidence in one short sentence. An item about the whole deck cites page 0.

Checklist:
{items}

Reply with a JSON object and nothing else:
{{"items": [{{"item": <number>, "answer": "yes" | "no", "page": <number>, "evidence": "<one short sentence>"}}]}}
"""


def page_texts(pdf: Path) -> list[str]:
    import fitz

    with fitz.open(str(pdf)) as doc:
        return [page.get_text("text").strip() for page in doc]


def build_prompt(task: Task, texts: list[str]) -> str:
    pages = "\n\n".join(f"--- page {i} ---\n{text or '(no text)'}" for i, text in enumerate(texts, 1))
    items = "\n".join(f"{i}. {item}" for i, item in enumerate(task.checklist, 1))
    # Dated like every prompt here: whether a figure is current is part of
    # judging it.
    return today_line() + "\n\n" + PROMPT.format(
        brief=task.prompt.strip(), pages=pages, items=items)


def read_verdict(task: Task, reply: str) -> list[dict]:
    """The judge's answers, one per checklist item; an item it skipped counts as unanswered."""
    answers = {}
    entries = (extract_json_object(reply) or {}).get("items")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry.get("item"))
        except (TypeError, ValueError):
            continue
        answer = str(entry.get("answer") or "").strip().lower()
        answers[number] = {
            "answer": answer if answer in ("yes", "no") else None,
            "page": entry.get("page"),
            "evidence": str(entry.get("evidence") or "")[:400],
        }
    return [{"item": text, **answers.get(i, {"answer": None, "page": None, "evidence": ""})}
            for i, text in enumerate(task.checklist, 1)]


async def grade_run(run_dir: Path, task: Task, judge: VisionLLMProvider, judge_name: str) -> dict:
    pdfs = sorted(run_dir.glob("png/**/deck.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"{run_dir} has no rendered deck to grade")
    texts = page_texts(pdfs[0])
    images = [base64.b64encode(p.read_bytes()).decode()
              for p in sorted(pdfs[0].parent.glob("slide_*.png"),
                              key=lambda p: int(p.stem.split("_")[1]))]
    reply = await judge.chat_with_images(build_prompt(task, texts), images, temperature=0.0)
    items = read_verdict(task, reply)
    answered = [i for i in items if i["answer"]]
    result = {
        "task": task.id,
        "run": run_dir.name,
        "judge": judge_name,
        "passed": sum(i["answer"] == "yes" for i in items),
        "answered": len(answered),
        "items": items,
    }
    (run_dir / f"grade-{judge_name}.json").write_text(json.dumps(result, indent=2))
    return result


def agreement(a: dict, b: dict) -> float | None:
    """The share of items two judges both answered and answered alike."""
    pairs = [(x["answer"], y["answer"]) for x, y in zip(a["items"], b["items"])
             if x["answer"] and y["answer"]]
    return sum(x == y for x, y in pairs) / len(pairs) if pairs else None


def render(result: dict) -> str:
    lines = [f"checklist ({result['judge']}): {result['passed']} of {len(result['items'])} yes"]
    for i in result["items"]:
        mark = {"yes": "yes", "no": " NO", None: "  ?"}[i["answer"]]
        lines.append(f"  {mark} {i['item']}" + (f" - p{i['page']}: {i['evidence']}" if i["evidence"] else ""))
    return "\n".join(lines)
