"""
End-to-end harness: drive the real AgentLoop against a real provider,
against the real Postgres/Redis, and render + preview the deck it produces.

The agent now consults the user before planning and again before drafting, so a
run pauses twice. The harness answers for the user - by default picking each
question's first suggested option, which is also how it checks that the options
the model offers are usable ones - and resumes the way the API does. Pass
--no-questions to send a "just build it" prompt instead and skip both pauses.

Usage:
    python e2e.py "<prompt>" <outdir> [--mode simple|deep] [--detail detailed]
                                      [--no-questions] [--answer TEXT]
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://thinkslides:thinkslides_dev_password@localhost:5432/thinkslides")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("TEMPLATE_DIR", "/tmp/ts_e2e/templates")
os.environ.setdefault("OUTPUT_DIR", "/tmp/ts_e2e/output")
os.environ.setdefault("PREVIEW_OUTPUT_DIR", "/tmp/ts_e2e/previews")
os.environ.setdefault("UPLOAD_DIR", "/tmp/ts_e2e/uploads")
for d in ("templates", "output", "previews", "uploads"):
    os.makedirs(f"/tmp/ts_e2e/{d}", exist_ok=True)

from sqlalchemy import select  # noqa: E402
from app.db.session import async_session  # noqa: E402
from app.db.models import (  # noqa: E402
    ChatMessage, ChatSession, Organisation, OrganisationAISettings, User,
)
from app.agent.loop import AgentLoop  # noqa: E402


async def _seed(db, prompt: str, org_id: uuid.UUID | None = None):
    """Seed a session under the organisation that has a configured provider.

    ``org_id`` pins it. Without one this takes whichever settings row comes
    back first, which is fine for a one-off look but not for an eval pass -
    two workspaces with different models would silently score as one.
    """
    query = select(OrganisationAISettings)
    if org_id is not None:
        query = query.where(OrganisationAISettings.organisation_id == org_id)
    configured = (await db.execute(query.limit(1))).scalar_one_or_none()
    if org_id is not None and configured is None:
        raise SystemExit(f"no configured workspace with id {org_id}")
    org = None
    if configured is not None:
        org = (
            await db.execute(
                select(Organisation).where(Organisation.id == configured.organisation_id)
            )
        ).scalar_one_or_none()
    if org is None:
        org = (await db.execute(select(Organisation).limit(1))).scalar_one_or_none()
    if org is None:
        org = Organisation(name="E2E Bench", slug=f"e2e-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()

    user = (
        await db.execute(select(User).where(User.organisation_id == org.id).limit(1))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            organisation_id=org.id,
            email=f"bench-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            full_name="Bench",
        )
        db.add(user)
        await db.flush()

    session = ChatSession(
        organisation_id=user.organisation_id, user_id=user.id, title="E2E Bench"
    )
    db.add(session)
    await db.flush()
    db.add(ChatMessage(session_id=session.id, role="user", content=prompt))
    await db.commit()
    return session.id, org.id


def _describe(event: dict) -> str:
    """One log line per event, in the shape the activity feed shows."""
    kind = event.get("type")
    if kind == "tool_call":
        return f"[call] {event.get('toolName')} {str(event.get('toolArgs'))[:260]}"
    if kind == "tool_result":
        # A failed tool puts its message in "result" - the same field a
        # successful one puts its data in, which is what the model reads.
        # Looking for it under "error" printed nothing, so every tool failure
        # in every bench run read as a blank line and the deck that quietly
        # lost its diagram looked like a deck that never asked for one.
        detail = "" if event.get("success") else str(event.get("result") or "")
        return (f"[result] {event.get('toolName')} "
                f"success={event.get('success')} "
                f"| {event.get('summary') or ''} "
                f"{detail[:400]}")
    if kind == "thought":
        return f"[thought] {str(event.get('content'))[:300]}"
    if kind == "rate_limit":
        return f"[wait] {event.get('message')}"
    if kind == "question":
        questions = event.get("questions") or [
            {"question": event.get("question"), "options": event.get("options")}
        ]
        lines = [f"[question] context={str(event.get('context') or '')[:200]}"]
        for q in questions:
            lines.append(f"    Q: {q.get('question')}")
            for option in q.get("options") or []:
                lines.append(f"       - {option}")
        return "\n".join(lines)
    if kind == "error":
        return f"[error] {str(event)[:400]}"
    if kind == "file_generated":
        return f"[file] {str(event)[:200]}"
    if kind == "done":
        return f"[done] paused={event.get('paused')} {str(event)[:400]}"
    return ""


def _auto_answer(question_event: dict, override: str) -> str:
    """Answer as a user would: pick each question's first suggested option."""
    if override:
        return override
    questions = question_event.get("questions") or [
        {"question": question_event.get("question"), "options": question_event.get("options")}
    ]
    picked = []
    for q in questions:
        options = q.get("options") or []
        picked.append(f"{q.get('question')} -> {options[0] if options else 'you decide'}")
    return "\n".join(picked)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("outdir")
    ap.add_argument("--mode", default="simple")
    ap.add_argument("--detail", default="detailed")
    ap.add_argument(
        "--no-questions",
        action="store_true",
        help="Opt out of the consultation checkpoints, as a user can.",
    )
    ap.add_argument(
        "--answer",
        default="",
        help="Answer every question with this text instead of the first option.",
    )
    ap.add_argument("--max-pauses", type=int, default=4)
    ap.add_argument(
        "--org", default=None,
        help="organisation UUID to run under, instead of whichever configured "
             "workspace comes back first",
    )
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    log_path = os.path.join(args.outdir, "events.log")

    prompt = args.prompt
    if args.no_questions:
        prompt += " Just build it, don't ask me anything."

    async with async_session() as db:
        session_id, org_id = await _seed(
            db, prompt, uuid.UUID(args.org) if args.org else None
        )
        print(f"session: {session_id} org: {org_id}", flush=True)

        rendered_path = None
        loop = None
        with open(log_path, "w") as log:
            # Each pause is a separate run, exactly as the API resumes one.
            for attempt in range(args.max_pauses + 1):
                loop = AgentLoop(
                    db=db,
                    mode=args.mode if attempt == 0 else None,
                    org_id=org_id,
                    detail_level=args.detail if attempt == 0 else None,
                )
                pending_question = None
                paused = False
                async for event in loop.run(session_id=session_id):
                    if event.get("type") == "question":
                        pending_question = event
                    if event.get("type") == "done":
                        paused = bool(event.get("paused"))
                    if event.get("type") == "file_generated":
                        rendered_path = event.get("path") or event.get("filePath")
                    line = _describe(event)
                    if line:
                        print(line, flush=True)
                        log.write(line + "\n")

                if not paused or pending_question is None:
                    break

                answer = _auto_answer(pending_question, args.answer)
                print(f"[answer] {answer}", flush=True)
                log.write(f"[answer] {answer}\n")
                db.add(ChatMessage(session_id=session_id, role="user", content=answer))
                await db.commit()

        # The deck the loop actually rendered.
        rendered_path = rendered_path or (loop.working_state.last_rendered_path if loop else None)
        print(f"rendered: {rendered_path}", flush=True)

        if rendered_path and os.path.exists(rendered_path):
            import shutil
            from app.renderer.preview_generator import PreviewGenerator

            shutil.copy(rendered_path, os.path.join(args.outdir, "deck.pptx"))
            pg = PreviewGenerator(output_dir=os.path.join(args.outdir, "png"))
            urls = pg.generate_previews(rendered_path, "bench")
            print(f"previews: {len(urls)}", flush=True)
            return 0

        print("NO DECK RENDERED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
