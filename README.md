# ThinkSlides

Enterprise-grade, multi-tenant web application that generates fully branded, template-faithful
PowerPoint presentations through a conversational LLM chat interface combined with document
and data ingestion.

A user chats in natural language, optionally uploads documents and a branded PPTX/POTX template,
and a ReAct AI agent reasons through the task, calls a structured tool registry, and renders a
template-faithful `.pptx` - streaming every thought, tool call, and result to the UI in real time
via Server-Sent Events.

## Architecture

ThinkSlides is a three-tier system running as five Docker Compose services on a single bridge
network (`thinkslides-network`):

- **Frontend** (`apps/web`) - Next.js 14 App Router, TypeScript, Tailwind CSS, Zustand state.
  Port `3000`.
- **Orchestrator** (`services/orchestrator`) - Go 1.24 / Gin HTTP gateway, JWT auth, Redis job
  queue, and SSE streaming hub. Port `8080`.
- **AI Service** (`services/ai`) - Python 3.11 / FastAPI ReAct agent loop, LLM provider
  abstraction, PPTX XML-clone renderer, RAG ingestion. Port `8000`.
- **PostgreSQL 16 + pgvector** - system of record and vector store. Port `5432`.
- **Redis 7** - job queue, SSE pub/sub event bus, and agent working-state cache. Port `6379`.

### System architecture

<!-- DIAGRAM PLACEHOLDER: replace docs/diagrams/system-architecture.png -->
![System Architecture](docs/diagrams/system-architecture.png)

The orchestrator is the sole HTTP entry point for the browser (proxied through Next.js middleware
under `/api/orchestrator/*`). The AI service is invoked by the orchestrator's background worker over
plain HTTP and is fire-and-forget for generation - results stream back asynchronously through Redis
pub/sub, not over the HTTP response. The orchestrator and AI service share four Docker volumes
(`uploads`, `templates`, `output`, `previews`) for file handoff. A gRPC contract
(`services/orchestrator/proto/agent.proto`) is defined but not yet wired in; the live transport is HTTP.

### Request and real-time SSE event flow

<!-- DIAGRAM PLACEHOLDER: replace docs/diagrams/request-sse-flow.png -->
![Request and SSE Event Flow](docs/diagrams/request-sse-flow.png)

A chat message is non-blocking end-to-end. The orchestrator persists the user message and an empty
assistant placeholder, enqueues a job, and returns `200` immediately. A worker dequeues the job and
calls the AI service; the AI service runs the ReAct loop and publishes each agent event to the Redis
channel `agent:events:<session_id>`. The orchestrator's SSE hub subscribes to that channel and fans
events out to the browser over a long-lived SSE connection. Because `EventSource` cannot set headers,
the JWT is passed as `?token=` on the SSE URL, which the orchestrator accepts alongside the
`Authorization` header. On the terminal `done` event (with `paused=false`), the worker saves the
assistant reply to PostgreSQL and the stream closes.

Event types: `thought`, `tool_call`, `tool_result` (with cited sources), `slide_finalized`,
`file_generated`, `progress`, `error`, `question`, `timer_update`, `session_title_updated`, `done`.

### ReAct agent loop and tool pipeline

<!-- DIAGRAM PLACEHOLDER: replace docs/diagrams/agent-loop-tools.png -->
![ReAct Agent Loop and Tool Pipeline](docs/diagrams/agent-loop-tools.png)

The agent loop (`services/ai/app/agent/loop.py`) runs up to 25 Reason-Act-Observe iterations and
cannot exit until both `finalize_and_render_pptx` and `validate_rendered_output` have run (the
latter is bounded to 3 attempts so an unfixable overflow can't loop forever). It checkpoints a
`WorkingState` to Redis (`working_state:<session_id>`, 1h TTL) so an `ask_user_question` pause can
resume later. Tools map to logical phases: `content_analysis` -> `structure_planning` ->
`layout_selection` -> `content_generation` -> `validation` -> `rendering` -> `validation` again
(plus `user_input`).

Twelve registered tools: `analyze_document` (also catalogs embedded document images for reuse),
`extract_key_data`, `extract_template_design_system`, `web_search` (capped at 3 calls/run),
`plan_presentation_structure`, `select_layout`, `design_slide_content`, `validate_slide_json`,
`select_source_image` (reuses an existing document/web image only when confidently relevant -
never generates one), `finalize_and_render_pptx`, `validate_rendered_output` (ground-truth check
against the actual LibreOffice-rendered output), and `ask_user_question`.

See `docs/architecture-diagram-prompts.md`'s "Design Decisions" section for why images are reused
rather than generated, and why output validation targets LibreOffice only.

### Data model

<!-- DIAGRAM PLACEHOLDER: replace docs/diagrams/data-model.png -->
![Data Model](docs/diagrams/data-model.png)

PostgreSQL with JSONB, UUID, ARRAY, and pgvector. Core tables: `organisations`, `users`,
`templates`, `design_system_bundles`, `chat_sessions`, `chat_messages`, `agent_events` (with a
`raw_event` JSONB column so the live event stream and cited sources can be reconstructed on
reload), `generated_files`, `ingested_documents`, and `document_chunks`
(`embedding vector(1536)` with an ivfflat cosine index for RAG similarity search). The orchestrator
writes auth, session, message, file, and template rows via pgx; the AI service writes template
design-system bundles, agent events, generated files, and RAG chunks via asyncpg.

### Template ingestion and PPTX rendering pipeline

<!-- DIAGRAM PLACEHOLDER: replace docs/diagrams/rendering-pipeline.png -->
![Template Ingestion and PPTX Rendering Pipeline](docs/diagrams/rendering-pipeline.png)

Template design systems are extracted at ingestion time: PPTX/POTX templates are parsed from their
slide XML into a `DesignSystemBundle`, while PDF templates go through a vision LLM. Rendering uses an
XML-clone approach - it clones real layout XML and fills placeholder `<a:t>` text nodes **without
touching `<a:rPr>` run properties**, preserving template design fidelity. Previews are generated via
LibreOffice headless (PPTX -> PDF) and PyMuPDF (PDF -> PNG at 2x). The shared `@thinkslides/schemas`
package (TypeScript + Pydantic mirror) is the contract between the agent loop, the renderer, and the
frontend.

## Quick Start

### Prerequisites
- Docker & Docker Compose v2+
- Node.js 20+ (for local frontend dev)
- Python 3.11+ (for local AI service dev)
- Go 1.22+ (for local orchestrator dev)
- Make

### Run the full stack

```bash
# Copy environment template
cp .env.example .env

# Fill in your API keys
# LLM_PROVIDER=zhipu
# ZHIPU_API_KEY=your_key_here

# Start all services
make dev

# Run database migrations
make migrate

# Frontend:  http://localhost:3000
# Orchestrator: http://localhost:8080
# AI Service:  http://localhost:8000
```

### Individual service development

```bash
# Frontend only
make dev-web

# AI service only
make dev-ai

# Orchestrator only
make dev-orchestrator
```

### Build & Test

```bash
make build          # Build all Docker images
make test           # Run all tests
make test-web       # Test frontend
make test-ai        # Test AI service
make test-orchestrator  # Test orchestrator
```

## Technology Stack

| Layer | Technologies |
|---|---|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS, Zustand, Framer Motion, EventSource SSE |
| Orchestrator | Go 1.24, Gin, pgx/v5, go-redis/v9, golang-jwt/v5, bcrypt |
| AI Service | Python 3.11, FastAPI, Uvicorn, SQLAlchemy 2 (async), asyncpg, Alembic, structlog |
| LLMs | Zhipu GLM (default: `glm-4.7` text, `glm-4.5-flash` planning, `glm-4.6v` vision); Azure OpenAI (`gpt-4`, `gpt-4o`) swappable |
| Embeddings / Search | Zhipu `embedding-2` / OpenAI `text-embedding-ada-002`; web search via Zhipu `search_std` or Tavily |
| Rendering | python-pptx, lxml (XML-clone), LibreOffice headless, PyMuPDF |
| Data | PostgreSQL 16 + pgvector, Redis 7 |
| Shared contracts | `@thinkslides/schemas` (TypeScript + Pydantic mirror) |

## Project Structure

```
.
├── apps/
│   └── web/                    # Next.js 14 frontend
│       ├── src/
│       │   ├── app/            # App Router pages + API proxy route handlers
│       │   ├── components/     # React components (workspace, chat, layout, preview)
│       │   ├── hooks/          # Custom hooks (SSE, chat, auth)
│       │   ├── lib/            # API clients, auth, SSE client
│       │   ├── stores/         # Zustand state stores (chat, layout)
│       │   └── types/          # TypeScript types
│       └── Dockerfile
├── services/
│   ├── ai/                     # Python FastAPI AI service
│   │   ├── app/
│   │   │   ├── agent/          # ReAct agent loop + tool registry + tools
│   │   │   ├── api/            # FastAPI routes (/api/v1)
│   │   │   ├── db/             # SQLAlchemy async models
│   │   │   ├── ingestion/      # Document parsing + chunking + embeddings (RAG)
│   │   │   ├── llm/            # LLM provider abstraction (Zhipu / Azure OpenAI)
│   │   │   ├── renderer/       # PPTX XML-clone renderer + preview generator
│   │   │   └── observability/  # structlog JSON logging
│   │   ├── alembic/            # DB migrations
│   │   └── Dockerfile
│   └── orchestrator/           # Go Gin orchestrator
│       ├── cmd/server/         # Entry point
│       ├── internal/           # handler, queue, sse, worker, middleware, client, repository
│       ├── proto/              # gRPC AgentService definitions (spec, not yet wired)
│       └── Dockerfile
├── packages/
│   └── schemas/                # Shared type schemas (TS + Python)
│       ├── src/                # TypeScript schemas
│       └── python/             # Python Pydantic schemas
├── db/
│   └── migrations/             # SQL migration files (run by Postgres entrypoint)
├── docs/
│   ├── architecture-diagram-prompts.md   # Prompts to regenerate the diagrams above
│   └── diagrams/               # Generated diagram images (replace placeholders)
├── docker-compose.yml
├── Makefile
└── .env.example
```

## Key Design Decisions

1. **Template-agnostic**: Users manage their own PPTX/POTX templates; the system extracts design
   systems at ingestion time and persists them as `DesignSystemBundle` rows.
2. **XML-clone rendering**: The PPTX renderer clones real layout XML and fills placeholders without
   touching `rPr` formatting, preserving template fidelity.
3. **ReAct agent loop**: Structured tool registry with phase mapping, working-state checkpointing,
   and a mandatory finalize guardrail.
4. **Decoupled streaming**: Generation is fire-and-forget HTTP; results travel back via Redis
   pub/sub to a multi-hop SSE path (AI -> Redis -> orchestrator hub -> browser).
5. **LLM abstraction**: Defaults to Zhipu GLM with tiered models (text / flash / vision); Azure
   OpenAI is swappable via env vars.
6. **Multi-tenant from day one**: JWT (HS256, 72h) auth with org-level isolation; orchestrator
   enforces tenancy inline from JWT claims.
7. **Shared contracts**: A single `@thinkslides/schemas` package mirrors TypeScript and Pydantic
   types so the frontend, agent loop, and renderer agree on shape.
