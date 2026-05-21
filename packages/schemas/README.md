# @slideagent/schemas

Shared type schemas used across the SlideAgent platform.

## Structure

- `src/` — TypeScript schemas (used by the Next.js frontend)
- `python/` — Python Pydantic schemas (used by the AI service)

## Schemas

### `slide.ts` / `slide.py`
The canonical Slide JSON schema. This is the contract between the agent loop and the PPTX renderer.

### `agent-events.ts` / `agent_events.py`
Typed SSE events streamed from the orchestrator to the frontend.

### `api.ts` / `api.py`
API request/response schemas for REST endpoints.

## Development

```bash
# Build TypeScript schemas
cd packages/schemas && npm run build

# Python schemas are used directly (no build step)
```