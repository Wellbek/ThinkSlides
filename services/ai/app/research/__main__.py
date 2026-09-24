"""
Command line for headless runs.

    python -m app.research run <task.yaml> [--mode claude-code|replay|live] [--cassette PATH]
                               [--org UUID --model ID --max-cost USD]
    python -m app.research show <run-dir>

Runs against the dev Postgres and Redis on localhost (``make infra``). Search
goes through Tavily when TAVILY_API_KEY is set, read from the environment or
the repository's .env; Deep tasks without it are downgraded to Simple.

In claude-code mode the run blocks on each model call until
``llm/<seq>.response.json`` appears next to ``llm/<seq>.request.md``.

A live run bills an API: it runs on an organisation's saved credentials with
the model named, is refused if --max-cost exceeds what is left in
benchmark/budget.json, stops once it has spent --max-cost, and appends what it
spent to benchmark/ledger.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
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
    # Tavily for search; the settings key to decrypt a workspace's saved
    # credentials, which live runs use.
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name in ("TAVILY_API_KEY", "SETTINGS_ENCRYPTION_KEY") and name not in os.environ:
                os.environ[name] = value.strip()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a task end to end")
    run.add_argument("task", type=Path)
    run.add_argument("--mode", default="claude-code", help="claude-code, replay or live")
    run.add_argument("--cassette", type=Path, help="cassette.jsonl to replay")
    run.add_argument("--org", help="live: organisation whose saved provider credentials to use")
    run.add_argument("--model", help="live: the model to run on; must be in benchmark/prices.json")
    run.add_argument("--max-cost", type=float, default=0.0,
                     help="live: stop the run once it has spent this many USD")
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

    live = None
    if args.mode == runner.LIVE:
        if not (args.org and args.model):
            parser.error("live runs need --org, --model and --max-cost")
        live = runner.Live(org_id=uuid.UUID(args.org), model=args.model,
                           max_cost=args.max_cost, benchmark_dir=REPO / "benchmark")
    outcome = asyncio.run(runner.run_task(
        Task.load(args.task), mode=args.mode, out_root=args.out,
        cassette_from=args.cassette, live=live,
    ))
    print(outcome.run_dir)
    print(runner.summarise(outcome.run_dir).split("\n\n")[0])
    return 0 if outcome.deck else 1


if __name__ == "__main__":
    sys.exit(main())
