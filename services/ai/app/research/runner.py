"""
Run a benchmark task end to end without the UI.

The real AgentLoop, against the dev Postgres and Redis, answering its own
pauses from the task file and resuming the way the API does. Each pause ends
one AgentLoop run and the answer starts the next, so a task produces one trace
per part. Everything the run produced lands in one directory:

    manifest.json           task, mode, git commit, parts, outcome
    events.log              one line per event, as the activity feed shows it
    trace-<n>.jsonl / .md   the span tree of each part
    cassette.jsonl          every model answer and search result, for replay
    llm/                    requests and responses, in claude-code mode
    deck.pptx, png/         the rendered deck and its pages
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.research.budget import (
    LEDGER_PATH,
    Ledger,
    MeteredProvider,
    MeteredVisionProvider,
    Meter,
    load_prices,
)
from app.research.exchange import (
    Cassette,
    ClaudeCodeExchange,
    ClaudeCodeProvider,
    ClaudeCodeVisionProvider,
    RecordingProvider,
    RecordingVisionProvider,
    ReplayProvider,
    ReplayVisionProvider,
    recorded_search,
)
from app.research.task import Task

CLAUDE_CODE = "claude-code"
REPLAY = "replay"
LIVE = "live"
MODES = (CLAUDE_CODE, REPLAY, LIVE)


@dataclass
class Live:
    """What a paid run needs: whose credentials, which model, and its cap."""

    org_id: uuid.UUID
    model: str
    max_cost: float
    benchmark_dir: Path


@dataclass
class Outcome:
    run_dir: Path
    parts: int = 0
    termination: str = ""
    deck: Path | None = None
    pages: int = 0
    errors: list[str] = field(default_factory=list)


def describe(event: dict) -> str:
    """One log line per event, in the shape the activity feed shows."""
    kind = event.get("type")
    if kind == "tool_call":
        return f"[call] {event.get('toolName')} {str(event.get('toolArgs'))[:260]}"
    if kind == "tool_result":
        detail = "" if event.get("success") else str(event.get("result") or "")
        return (f"[result] {event.get('toolName')} success={event.get('success')} "
                f"| {event.get('summary') or ''} {detail[:400]}")
    if kind == "thought":
        return f"[thought] {str(event.get('content'))[:300]}"
    if kind == "question":
        questions = event.get("questions") or [
            {"question": event.get("question"), "options": event.get("options")}
        ]
        lines = [f"[question] {str(event.get('context') or '')[:200]}"]
        for q in questions:
            lines.append(f"    Q: {q.get('question')}")
            lines += [f"       - {o}" for o in q.get("options") or []]
        return "\n".join(lines)
    if kind in ("error", "file_generated", "done"):
        return f"[{kind}] {str(event)[:400]}"
    return ""


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _search_config(mode: str, cassette_from: Path | None):
    """Tavily when a key is set; on replay, whatever the recorded run had.

    A replay never searches - its results come from the cassette - but
    whether a search backend exists decides which path the run takes (Deep
    is downgraded without one, the research question is offered with one),
    so it has to match the recording, not this machine's environment.
    """
    from app.ai_settings import SearchConfig

    if mode == REPLAY:
        manifest = cassette_from.parent / "manifest.json"
        had_search = manifest.exists() and json.loads(manifest.read_text()).get("search")
        return SearchConfig(backend="tavily", api_key="replayed") if had_search else None
    key = os.environ.get("TAVILY_API_KEY", "")
    return SearchConfig(backend="tavily", api_key=key) if key else None


async def _seed(db, prompt: str):
    """A session to run in, under a dedicated research organisation."""
    from sqlalchemy import select

    from app.db.models import ChatMessage, ChatSession, Organisation, User

    org = (await db.execute(
        select(Organisation).where(Organisation.slug == "research-runs")
    )).scalar_one_or_none()
    if org is None:
        org = Organisation(name="Research runs", slug="research-runs")
        db.add(org)
        await db.flush()
    user = (await db.execute(
        select(User).where(User.organisation_id == org.id).limit(1)
    )).scalar_one_or_none()
    if user is None:
        user = User(organisation_id=org.id, email="research@localhost",
                    password_hash="!", display_name="Research runner", role="owner")
        db.add(user)
        await db.flush()
    session = ChatSession(organisation_id=org.id, user_id=user.id, title="Research run")
    db.add(session)
    await db.flush()
    db.add(ChatMessage(session_id=session.id, role="user", content=prompt))
    await db.commit()
    return session.id


async def _record_answer(session_id, question_event: dict, reply: str) -> None:
    """Record the answer in the paused run's working state, as the API does.

    The browser's answer goes through /answer-question, which appends it to
    ``answered_questions`` before resuming (see api/generate.py _run_agent).
    Later phases read the answers from there - the planner's summary among
    them - so a run that only posted the reply as a chat message would plan
    without the answers a real user's run has.
    """
    import redis.asyncio as redis

    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    key = f"working_state:{session_id}"
    try:
        raw = await client.get(key)
        if not raw:
            return
        state = json.loads(raw)
        state.setdefault("answered_questions", []).append({
            "question": question_event.get("question") or "",
            "answer": reply,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        await client.set(key, json.dumps(state), ex=3600)
    finally:
        await client.aclose()


async def _live_providers(db, live: Live):
    """A workspace's saved provider, on the model the run asked for."""
    import dataclasses

    from app import ai_settings
    from app.llm.provider import build_chat_provider, build_vision_provider

    credentials = ai_settings.resolve_chat_credentials(await ai_settings.load_row(db, live.org_id))
    if credentials is None:
        raise SystemExit(f"organisation {live.org_id} has no chat model configured")
    credentials = dataclasses.replace(credentials, model=live.model)
    try:
        vision = build_vision_provider(credentials)
    except ValueError:
        vision = None
    return build_chat_provider(credentials), vision, credentials.provider


async def run_task(task: Task, *, mode: str, out_root: Path,
                   cassette_from: Path | None = None, live: Live | None = None) -> Outcome:
    from app.agent.loop import AgentLoop
    from app.agent.tools import web_search
    from app.db.models import ChatMessage
    from app.db.session import async_session
    from app.observability.trace_store import DatabaseSink, JsonlSink

    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if mode == REPLAY and cassette_from is None:
        raise ValueError("replay needs the cassette of a previous run")
    meter = ledger = None
    if mode == LIVE:
        if live is None:
            raise ValueError("a live run needs an organisation, a model and --max-cost")
        prices = load_prices(live.benchmark_dir / "prices.json")
        if live.model not in prices:
            raise ValueError(f"{live.model} is not in prices.json; an unpriced run is unbounded")
        ledger = Ledger(LEDGER_PATH, live.benchmark_dir / "budget.json")
        ledger.authorise(live.max_cost)
        meter = Meter(prices[live.model], live.max_cost)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = out_root / task.id / f"{stamp}-{mode}"
    run_dir.mkdir(parents=True)
    outcome = Outcome(run_dir=run_dir)

    if mode == REPLAY:
        source = Cassette(cassette_from)
        # A replay answered exactly what was recorded, so its cassette is the
        # same one - kept, so a replay can itself be replayed.
        shutil.copy(cassette_from, run_dir / "cassette.jsonl")
        chat, vision = ReplayProvider(source), ReplayVisionProvider(source)
        search = recorded_search(web_search.run_search, source, replay=True)
    elif mode == LIVE:
        cassette = Cassette(run_dir / "cassette.jsonl")
        chat = vision = None  # built once there is a database session
        search = recorded_search(web_search.run_search, cassette, replay=False)
    else:
        cassette = Cassette(run_dir / "cassette.jsonl")
        exchange = ClaudeCodeExchange(run_dir / "llm", cassette)
        chat, vision = ClaudeCodeProvider(exchange), ClaudeCodeVisionProvider(exchange)
        search = recorded_search(web_search.run_search, cassette, replay=False)

    search_config = _search_config(mode, cassette_from)
    manifest = {
        "search": search_config.backend if search_config else None,
        "task": task.id, "category": task.category, "mode": mode,
        "taskFile": str(task.source) if task.source else None,
        "commit": _git_commit(), "startedAt": datetime.datetime.now().isoformat(),
        "replayOf": str(cassette_from) if cassette_from else None,
        "model": live.model if live else None,
        "maxCostUsd": live.max_cost if live else None,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    original_search = web_search.run_search
    web_search.run_search = search
    try:
        async with async_session() as db:
            if mode == LIVE:
                real_chat, real_vision, provider_name = await _live_providers(db, live)
                manifest["provider"] = provider_name
                chat = RecordingProvider(MeteredProvider(real_chat, meter), cassette)
                vision = (RecordingVisionProvider(MeteredVisionProvider(real_vision, meter), cassette)
                          if real_vision else None)
            session_id = await _seed(db, task.prompt)
            manifest["sessionId"] = str(session_id)
            with open(run_dir / "events.log", "w") as log:
                for part in range(task.max_pauses + 1):
                    loop = AgentLoop(
                        db=db,
                        mode=task.mode if part == 0 else None,
                        detail_level=task.detail if part == 0 else None,
                        chat_provider=chat,
                        vision_provider=vision,
                        search_config=search_config,
                        trace_sinks=[DatabaseSink(), JsonlSink(run_dir, name=f"trace-{part}")],
                    )
                    question, paused = None, False
                    async for event in loop.run(session_id=session_id):
                        kind = event.get("type")
                        if kind == "question":
                            question = event
                        elif kind == "done":
                            paused = bool(event.get("paused"))
                        elif kind == "error":
                            outcome.errors.append(str(event.get("message") or event)[:300])
                        line = describe(event)
                        if line:
                            log.write(line + "\n")
                            log.flush()
                    outcome.parts = part + 1
                    outcome.termination = loop.termination
                    if not paused or question is None:
                        break
                    reply = task.answer(part, question)
                    log.write(f"[answer] {reply}\n")
                    await _record_answer(session_id, question, reply)
                    db.add(ChatMessage(session_id=session_id, role="user", content=reply))
                    await db.commit()

            rendered = loop.working_state.last_rendered_path
            if rendered and os.path.exists(rendered):
                from app.renderer.preview_generator import PreviewGenerator

                outcome.deck = run_dir / "deck.pptx"
                shutil.copy(rendered, outcome.deck)
                pages = PreviewGenerator(output_dir=str(run_dir / "png")).generate_previews(
                    str(outcome.deck), "pages")
                outcome.pages = len(pages)
    finally:
        web_search.run_search = original_search
        if meter is not None:
            spend = meter.summary()
            manifest["spend"] = spend
            ledger.record(task=task.id, run=run_dir.name, model=live.model,
                          provider=manifest.get("provider"), **spend)
        manifest.update({
            "finishedAt": datetime.datetime.now().isoformat(),
            "parts": outcome.parts,
            "termination": outcome.termination,
            "deck": outcome.deck.name if outcome.deck else None,
            "pages": outcome.pages,
            "errors": outcome.errors,
        })
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return outcome


def summarise(run_dir: Path) -> str:
    """A run directory as a short report: outcome, then each part's timeline."""
    manifest = json.loads((run_dir / "manifest.json").read_text())
    lines = [
        f"{manifest['task']} ({manifest['mode']}, commit {manifest.get('commit') or '?'})",
        f"termination {manifest.get('termination')}, {manifest.get('parts')} part(s), "
        f"{manifest.get('pages')} page(s)",
    ]
    lines += [f"error: {e}" for e in manifest.get("errors") or []]
    for part in sorted(run_dir.glob("trace-*.md")):
        lines += ["", part.read_text()]
    return "\n".join(lines)

