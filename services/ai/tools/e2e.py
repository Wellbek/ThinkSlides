"""
End-to-end harness: drive the real AgentLoop against a real provider,
against the real Postgres/Redis, and render + preview the deck it produces.

Usage:
    python e2e.py "<prompt>" <outdir> [--mode simple|deep] [--detail detailed]
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

from sqlalchemy import select, text  # noqa: E402
from app.db.session import async_session  # noqa: E402
from app.db.models import (  # noqa: E402
    ChatMessage, ChatSession, Organisation, OrganisationAISettings, User,
)
from app.agent.loop import AgentLoop  # noqa: E402


async def _seed(db, prompt: str):
    """Seed a session under the organisation that has a configured provider."""
    configured = (
        await db.execute(select(OrganisationAISettings).limit(1))
    ).scalar_one_or_none()
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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("outdir")
    ap.add_argument("--mode", default="simple")
    ap.add_argument("--detail", default="detailed")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    log_path = os.path.join(args.outdir, "events.log")

    async with async_session() as db:
        session_id, org_id = await _seed(db, args.prompt)
        print(f"session: {session_id} org: {org_id}", flush=True)

        loop = AgentLoop(db=db, mode=args.mode, org_id=org_id, detail_level=args.detail)
        rendered_path = None
        with open(log_path, "w") as log:
            async for event in loop.run(session_id=session_id):
                kind = event.get("type")
                line = ""
                if kind == "tool_call":
                    line = f"[call] {event.get('toolName')} {str(event.get('toolArgs'))[:260]}"
                elif kind == "tool_result":
                    line = (f"[result] {event.get('toolName')} "
                            f"success={event.get('success')} "
                            f"{str(event.get('error') or '')[:220]}")
                elif kind == "thought":
                    line = f"[thought] {str(event.get('content'))[:300]}"
                elif kind == "error":
                    line = f"[error] {str(event)[:400]}"
                elif kind == "file_generated":
                    rendered_path = event.get("path") or event.get("filePath")
                    line = f"[file] {str(event)[:200]}"
                elif kind == "done":
                    line = f"[done] paused={event.get('paused')} {str(event)[:400]}"
                if line:
                    print(line, flush=True)
                    log.write(line + "\n")

        # The deck the loop actually rendered.
        from app.agent.loop import AgentLoop as _AL  # noqa: F401
        rendered_path = rendered_path or loop.working_state.last_rendered_path
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
