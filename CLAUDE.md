# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ThinkSlides is an enterprise-grade, multi-tenant web application that generates fully branded, template-faithful PowerPoint presentations through a conversational LLM chat interface combined with document and data ingestion.

The system is a three-tier architecture:
- **Frontend** (`apps/web`): Next.js 14 App Router, TypeScript, Tailwind CSS, Zustand state management
- **AI Service** (`services/ai`): Python FastAPI with ReAct agent loop, LLM provider abstraction (Zhipu GLM by default, Azure OpenAI swapable)
- **Orchestrator** (`services/orchestrator`): Go Gin service handling SSE streaming, Redis job queue, and JWT auth

## Common Development Commands

### Full Stack
- `make dev` - Start all services with Docker Compose
- `make build` - Build all Docker images
- `make test` - Run all tests (web, ai, orchestrator)
- `make migrate` - Run database migrations
- `make clean` - Clean up all artifacts and containers

### Individual Services
- `make dev-web` - Start Next.js frontend locally (http://localhost:3000)
- `make dev-ai` - Start Python AI service locally (http://localhost:8000)
- `make dev-orchestrator` - Start Go orchestrator locally (http://localhost:8080)

### Frontend (`apps/web/`)
- `npm run dev` - Start Next.js dev server
- `npm run build` - Build for production
- `npm run lint` - Run ESLint
- `npm run typecheck` - Run TypeScript type checking

### AI Service (`services/ai/`)
- `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` - Start FastAPI dev server
- `python -m pytest tests/ -v` - Run tests
- `alembic upgrade head` - Run migrations
- `alembic revision --autogenerate -m "message"` - Create migration

### Orchestrator (`services/orchestrator/`)
- `go run cmd/server/main.go` - Start Go server
- `go test ./... -v` - Run tests
- `go build -o bin/server cmd/server/main.go` - Build binary

### Infrastructure
- `make infra` - Start only PostgreSQL and Redis (no app services)
- `make infra-down` - Stop PostgreSQL and Redis
- `make logs` - Follow all service logs
- `make logs-ai` - Follow AI service logs
- `make logs-orchestrator` - Follow orchestrator logs

## Architecture

### ReAct Agent Loop

The core AI workflow is a ReAct (Reason-Act-Observe) agent loop implemented in `services/ai/app/agent/loop.py`:

1. LLM reasons about the current state (yields "thought" event)
2. LLM selects a tool to call (yields "tool_call" event)
3. Tool executes and returns result (yields "tool_result" event)
4. Cycle repeats until task is complete (yields "done" event)

All intermediate steps are yielded as typed SSE events and streamed to the UI in real time.

### SSE Event Flow

The system uses a multi-hop SSE architecture:
1. Python AI service publishes events to Redis (`agent:events:{session_id}`)
2. Go orchestrator SSE Hub subscribes to Redis channels and broadcasts to local SSE connections
3. Next.js frontend connects via `/api/orchestrator/sessions/{id}/events` (rewritten by middleware to orchestrator)
4. SSEClient in `apps/web/src/lib/sse.ts` receives events and updates Zustand store

Key SSE event types: `thought`, `tool_call`, `tool_result`, `slide_finalized`, `file_generated`, `error`, `progress`, `done`.

### Agent Tools

Agent tools are registered in `services/ai/app/agent/registry.py` and implemented in `services/ai/app/agent/tools/`:
- `analyze_document` - Parse uploaded documents
- `extract_key_data` - Extract structured data from content
- `extract_template_design_system` - Analyze PPTX templates to extract design system
- `plan_presentation_structure` - Plan slide structure
- `select_layout` - Select appropriate layouts
- `design_slide_content` - Generate slide content
- `validate_slide_json` - Validate slide structure
- `generate_image_prompt` - Generate image prompts (optional)
- `finalize_and_render_pptx` - Render final PPTX file

### State Management

Frontend uses Zustand stores in `apps/web/src/stores/`:
- `chat-store.ts` - Chat sessions, messages, templates, SSE client management

The store manages:
- Active session and message history
- SSE client lifecycle (connect/disconnect)
- Event stream handling (thoughts, tool calls, progress)
- Generated files

### Shared Schemas

TypeScript schemas in `packages/schemas/src/` define shared types used across frontend and backend. These are compiled via TypeScript and should mirror Python Pydantic models in `services/ai/app/db/`.

### PPTX Rendering

The PPTX renderer in `services/ai/app/renderer/` uses an XML-clone approach:
- Clones real layout XML from templates
- Fills placeholders without touching `rPr` (run properties) formatting
- Preserves template faithfulness by avoiding direct formatting manipulation

### Configuration

All services use environment variables configured in `.env` (copy from `.env.example`). Key variables:
- `LLM_PROVIDER` - "zhipu" or "azure_openai"
- `ZHIPU_API_KEY` - Zhipu API key (default provider)
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `JWT_SECRET` - JWT signing secret

### Database

- PostgreSQL 16 with pgvector extension
- SQLAlchemy async models in `services/ai/app/db/`
- Alembic migrations in `services/ai/alembic/`
- SQL migrations in `db/migrations/` for Docker initialization

## Important Patterns

### SSE Authentication

SSE connections are authenticated via JWT token passed as query parameter (`?token=...`) since EventSource API doesn't support custom headers. The Go middleware checks both Authorization header and query param.

### File Uploads

File uploads flow through:
1. Frontend uploads to Next.js `/api/orchestrator/sessions/{id}/messages` endpoint
2. Next.js forwards to Go orchestrator
3. Go saves to shared volume and delegates to AI service
4. AI service processes and stores in database

### Session Lifecycle

1. User creates session (optionally with template)
2. Frontend connects SSE for that session
3. User sends message → triggers job in Redis queue
4. Background worker dequeues job and calls AI service
5. AI service runs ReAct loop, streaming events to Redis
6. Frontend receives events in real time via SSE
7. On "done" event, SSE auto-closes

### Design Decision Record

`docs/DESIGN_DECISIONS.md` states every rule the design engine uses to decide what goes where - archetype selection costs, the exhibit legibility floor, splitting, the fallback chain, table layout, repair and the revision ladder - with parameter values, rationale and the runs that motivated each.
A change to any of those rules, weights, thresholds or fallbacks updates it in the same pull request; it is meant to be citable in a paper.

### Tool Phase Mapping

Tools are mapped to logical phases in `services/ai/app/agent/loop.py` (TOOL_PHASES) for progress tracking:
- `content_analysis` - Document analysis, template extraction
- `structure_planning` - Presentation structure planning
- `layout_selection` - Layout selection
- `content_generation` - Slide content design, image prompts
- `validation` - Slide JSON validation
- `rendering` - PPTX rendering

### Testing

- Frontend: No test framework configured yet
- AI Service: pytest in `services/ai/tests/`
- Orchestrator: Go tests use standard `go test` (examples: `*_test.go` files)

### Path Aliases

Frontend uses TypeScript path aliases:
- `@/*` → `apps/web/src/*`
- `@thinkslides/schemas` → `packages/schemas/src`

## Environment Setup

First-time setup requires:
1. Copy `.env.example` to `.env` and fill in API keys
2. `docker compose up -d postgres redis` (or `make infra`)
3. `make migrate` to run database migrations
4. `make dev` to start all services

For local development of individual services, you can run them directly (see commands above) while keeping PostgreSQL and Redis running in Docker.