"""
Command line for headless runs.

    python -m app.research run <task.yaml> [--mode claude-code|replay] [--cassette PATH]
    python -m app.research show <run-dir>

Runs against the dev Postgres and Redis on localhost (``make infra``). Search
goes through Tavily when TAVILY_API_KEY is set, read from the environment or
the repository's .env; Deep tasks without it are downgraded to Simple.

In claude-code mode the run blocks on each model call until
``llm/<seq>.response.json`` appears next to ``llm/<seq>.request.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DEFAULT_OUT = REPO / "benchmark" / "runs"


def _environment() -> None:
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://thinkslides:thinkslides_dev_password@localhost:5432/thinkslides",
    )
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    scratch = Path(os.environ.get("RESEARCH_SCRATCH", "/tmp/thinkslides-research"))
    for name, var in (("templates", "TEMPLATE_DIR"), ("output", "OUTPUT_DIR"),
                      ("previews", "PREVIEW_OUTPUT_DIR"), ("uploads", "UPLOAD_DIR")):
        (scratch / name).mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(var, str(scratch / name))
    env_file = REPO / ".env"
    if "TAVILY_API_KEY" not in os.environ and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("TAVILY_API_KEY="):
                os.environ["TAVILY_API_KEY"] = line.split("=", 1)[1].strip()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a task end to end")
    run.add_argument("task", type=Path)
    run.add_argument("--mode", default="claude-code")
    run.add_argument("--cassette", type=Path, help="cassette.jsonl to replay")
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    show = commands.add_parser("show", help="summarise a finished run")
    show.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    _environment()
    from app.research import runner
    from app.research.task import Task

    if args.command == "show":
        print(runner.summarise(args.run_dir))
        return 0

    outcome = asyncio.run(runner.run_task(
        Task.load(args.task), mode=args.mode, out_root=args.out, cassette_from=args.cassette,
    ))
    print(outcome.run_dir)
    print(runner.summarise(outcome.run_dir).split("\n\n")[0])
    return 0 if outcome.deck else 1


if __name__ == "__main__":
    sys.exit(main())
