"""
A benchmark task: one brief, how the run is driven, and how it is judged.

Tasks live as YAML under ``benchmark/tasks/<category>/<id>.yaml``:

    id: technical-01
    category: technical-architecture
    prompt: |
      Three slides explaining ...
    mode: simple            # simple | deep
    detail: concise         # concise | detailed | comprehensive
    answers:                # replies to the run's pauses, in order
      - first               # "first" takes each question's first option
      - Go ahead as planned
    checklist:              # binary checks the finished deck is held to
      - The deck has exactly 3 slides
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

FIRST_OPTION = "first"


@dataclass
class Task:
    id: str
    category: str
    prompt: str
    mode: str = "simple"
    detail: str = "detailed"
    answers: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    max_pauses: int = 6
    source: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Task":
        path = Path(path)
        data = yaml.safe_load(path.read_text()) or {}
        missing = [k for k in ("id", "category", "prompt") if not data.get(k)]
        if missing:
            raise ValueError(f"{path}: missing {', '.join(missing)}")
        known = {f for f in cls.__dataclass_fields__ if f != "source"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"{path}: unknown keys {', '.join(sorted(unknown))}")
        return cls(**data, source=path)

    def answer(self, pause: int, question_event: dict) -> str:
        """The reply to the ``pause``-th question, as a user would type it."""
        scripted = self.answers[pause] if pause < len(self.answers) else FIRST_OPTION
        if scripted != FIRST_OPTION:
            return scripted
        questions = question_event.get("questions") or [
            {"question": question_event.get("question"), "options": question_event.get("options")}
        ]
        replies = []
        for q in questions:
            options = q.get("options") or []
            choice = options[0] if options else "you decide"
            if isinstance(choice, dict):
                choice = choice.get("label") or choice.get("id") or "you decide"
            replies.append(f"{q.get('question')} {choice}")
        return "\n".join(replies)
