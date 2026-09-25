"""
Every design language recorded runs authored, put through today's gate.

Reproduces the figure `docs/DESIGN_DECISIONS.md` quotes for the compile gate
(section 4): how many of the languages models actually wrote would be offered
now, and why each refused one is refused. It reads cassettes only, so it runs
offline and costs nothing.

    cd services/ai
    PYTHONPATH=. .venv/bin/python -m app.research.gate_audit ../../benchmark/evidence
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.agent.tools._json_extract import extract_json_object
from app.design import author_run, authoring, deck_format


def authored_languages(root: Path) -> list[tuple[str, object]]:
    """Each distinct language in the cassettes under `root`, with its run."""
    seen: dict[str, tuple[str, object]] = {}
    for cassette in sorted(root.glob("*/*/cassette.jsonl")):
        run = f"{cassette.parent.parent.name}/{cassette.parent.name}"
        for line in cassette.read_text().splitlines():
            response = json.loads(line).get("response")
            if not isinstance(response, dict):
                continue
            content = response.get("content") or ""
            if '"pages"' not in content:
                continue
            for language in authoring.read(extract_json_object(content) or {}):
                seen.setdefault(language.name, (run, language))
    return list(seen.values())


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "../../benchmark/evidence")
    fmt = deck_format.by_id(None)
    languages = authored_languages(root)
    refused = 0
    for run, language in languages:
        why = author_run._problem(language, fmt.canvas, fmt.floor_pt(fmt.canvas))
        refused += bool(why)
        print(f"{run:52s} {language.name:28s} {why or 'offered'}")
    print(f"\n{len(languages)} languages, {refused} refused")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
