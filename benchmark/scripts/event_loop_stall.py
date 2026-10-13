"""
How long one render blocks the AI service's event loop (PROJECT_PLAN D-16).

A heartbeat task ticks every 10 ms while `finalize_and_render_pptx` renders the
WTI specimen deck in the same loop. Every tick the heartbeat misses is time
during which no other session on that worker could publish an event, await a
model or answer a request.

    cd services/ai
    PREVIEW_OUTPUT_DIR=/tmp/ts-previews .venv/bin/python ../../benchmark/scripts/event_loop_stall.py /tmp/ts-out

Prints one JSON line per run.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
os.environ["OUTPUT_DIR"] = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ts-out"

from app.agent import run_context  # noqa: E402
from app.agent.tools import finalize_and_render_pptx as finalize  # noqa: E402
from app.design import run as design_run  # noqa: E402
from app.renderer.specimen import language_specimen_slides  # noqa: E402

TICK_S = 0.01


async def once() -> dict:
    run_context.set_pack(design_run.load("wti-2025"))
    ticks: list[float] = []

    async def heartbeat():
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(TICK_S)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    start = time.perf_counter()
    result = await finalize.FinalizeAndRenderPptxTool.run(
        slides_json=json.dumps(language_specimen_slides()), session_id="stall")
    end = time.perf_counter()
    await asyncio.sleep(0.03)
    beat.cancel()
    served = [t for t in ticks if start <= t <= end]
    gaps = [b - a for a, b in zip(ticks, ticks[1:]) if b >= start and a <= end]
    return {"ok": result.success, "render_s": round(end - start, 2),
            "ticks_served": len(served), "ticks_expected": int((end - start) / TICK_S),
            "longest_stall_s": round(max(gaps), 2)}


if __name__ == "__main__":
    for _ in range(3):
        print(json.dumps(asyncio.run(once())), flush=True)
