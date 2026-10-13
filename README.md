# ThinkSlides

<p align="center">
  <a href="#overview">Overview</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#features">Features</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#development">Development</a>
</p>

## Overview

ThinkSlides is a multi-tenant web application that builds branded PowerPoint decks from a chat.
Users describe what they need, attach documents and optionally a PPTX or POTX template, and get back an editable `.pptx`.

The pipeline is designed to run mostly on small, inexpensive models, while matching frontier large model output quality.
Models answer narrow, typed requests; code handles layout, checks and repairs.
Progress streams to the browser while the deck is built, and every step is traced with its inputs, model, tokens and cost.

This README describes the system as designed.

## Quick Start

Requires [Docker](https://www.docker.com/), [Docker Compose](https://docs.docker.com/compose/) and [Make](https://www.gnu.org/software/make/).

```bash
git clone https://github.com/Wellbek/ThinkSlides.git
cd ThinkSlides
cp .env.example .env    # add your model provider keys
make dev                # start all services
make migrate            # run database migrations
```

Then open **http://localhost:3000**.

| Service | URL |
| --- | --- |
| Web UI | `http://localhost:3000` |
| Orchestrator API | `http://localhost:8080` |
| AI service | `http://localhost:8000` |

## Features

- **Chat to deck**: describe the deck in plain language; ThinkSlides asks a question only when the answer changes the result.
- **Storyline review**: the outline (one claim per slide, with its evidence) is shown for approval before any slide is built.
- **Templates**: upload a PPTX or POTX template and the deck follows its layouts, colours and fonts. Without one, three design options are generated and shown as sample slides to choose from.
- **Research and sources**: web search, fetched pages, uploaded documents, pasted text and MCP sources. Every number on a slide links back to the passage it came from, and the deck ends with a sources page.
- **Native output**: editable text and native PowerPoint tables; charts and diagrams drawn in the deck's own colours.
- **Visual checks**: every rendered page is checked for overflow, overlap and contrast, then reviewed by a vision model; problems are fixed automatically, at most twice per page.
- **Follow-up edits**: after delivery, ask for changes. Slide edits rebuild only that slide; deck-wide requests such as a logo, footer, page numbers, watermark, QR code, recolour or links are applied as typed operations and kept on every re-render.
- **Models**: OpenAI, Anthropic, Z.ai (Zhipu GLM) and Azure OpenAI, configured per workspace and per role (small, medium, frontier, vision).
- **Multi-tenant**: organisations, users and JWT auth; per-organisation limits on concurrent runs and tokens; provider keys encrypted at rest.
- **Tracing**: every run is recorded as a span tree in PostgreSQL, with optional OpenTelemetry export to Langfuse.

## Architecture

### System

![ThinkSlides system architecture](docs/diagrams/system-architecture.png)

*Figure 1: System architecture.*

- **Web UI** (`apps/web`): Next.js 14, TypeScript, Tailwind CSS, Zustand.
- **Orchestrator** (`services/orchestrator`): Go and Gin. Auth, admission control, the session and file API, and the SSE hub.
- **AI service** (`services/ai`): Python and FastAPI. Run workers execute the pipeline; rendering (python-pptx, charts, LibreOffice, PyMuPDF) runs in a separate process pool.
- **PostgreSQL 16 + pgvector**: sessions, runs, events, spans and the evidence store.
- **Redis 7**: the run queue (a stream with a consumer group), event pub/sub and caches.

Runs are queued on a Redis stream and acknowledged when they pause or finish, so a run whose worker crashes is picked up by another worker and resumed from its last checkpoint.

### Request and event flow

![Request and event flow](docs/diagrams/request-sse-flow.png)

*Figure 2: From a chat message to the closed event stream.*

The orchestrator stores the message, queues the run and returns `202 Accepted`.
The browser then opens an SSE connection (the JWT is passed as `?token=` because `EventSource` cannot set headers).
Each event is saved to `agent_events` with a per-session sequence number before it is published, and the sequence is sent as the SSE event `id`.
After a dropped connection, the browser reconnects with `Last-Event-ID` and receives the events it missed.

Event types: `thought`, `tool_call`, `tool_result`, `slide_finalized`, `file_generated`, `progress`, `question`, `timer_update`, `session_title_updated`, `error`, `done`.

### Generation pipeline

![The generation pipeline](docs/diagrams/pipeline.png)

*Figure 3: The generation pipeline. Stacked boxes run in parallel.*

The pipeline is a fixed sequence of phases run by code (`services/ai/app/agent/pipeline.py`).
At each phase a model answers a typed request and code validates the answer.

1. **Intake**: read the brief; slide count, sections and exclusions are parsed in code, the deck format by a small model.
2. **Consult**: one optional question round.
3. **Evidence**: plan the search queries, then search, fetch and index in parallel.
4. **Storyline**: one call to a medium model: an action title, claim, evidence and exhibit for each slide, validated against the brief.
5. **Outline review**: the user approves or edits the outline.
6. **Design language**: the uploaded template, or one of three generated designs.
7. **Compile**: all slides at once, one call per slide on a small model, sharing a cached prompt prefix.
8. **Gates**: schema, grounding, chart checks and fit; text that does not fit is split or moved, never shrunk below the minimum size.
9. **Bind and render**: each page is placed on a layout and rendered.
10. **Check**: rendered geometry first, then a vision review of changed pages.
11. **Repair**: nudge a frame, move the page to another layout, or redraft the slide; at most two rounds.
12. **Deliver**: the `.pptx`, a sources page, the run trace and a summary of any open issues.

A slide that fails the same check twice is compiled again on the next model tier.

### Design and layout

![How a deck's design language and each page's layout are chosen](docs/diagrams/design-selection.png)

*Figure 4: Choosing the deck's design (left) and each page's layout (right).*

Models never output coordinates.
They choose the content; code decides every position.

- **Deck design**: an uploaded template is used as-is. Otherwise a model proposes three designs (type scale, colours, page types). Each is checked for contrast, minimum text size and whether every page type can be built, and shown as three sample slides with the same text so the user compares only the design.
- **Page layout**: each slide is matched against every layout in the design. Layouts that would leave out content or make a chart unreadable are excluded; the rest are scored on fit, density, reading order and overflow, and the best one is used. If nothing fits, the slide is split; if it still does not fit, a plain layout is used.

### Editing a finished deck

![Amending a finished deck](docs/diagrams/amending.png)

*Figure 5: Follow-up requests after a deck is delivered.*

A message after delivery edits the existing deck instead of starting a new one.

- **Slide content** (text, a chart, a figure): the named slides are rebuilt and re-rendered; the rest of the deck is unchanged.
- **Deck-wide changes** (logo, footer, page numbers, watermark, QR code, progress bar, recolour, links): applied as typed operations, placed clear of existing content, and kept on every re-render.
- **Anything else** (moving, rotating or restyling an element): a short Python script runs in a sandbox with no network access, an import allowlist and resource limits. The script gets a `layout.json` with every shape, free area, colour and minimum text size on each page.

### Evidence store

![The evidence store](docs/diagrams/evidence.png)

*Figure 6: Gathering, storing and retrieving sources.*

- **Gather**: search queries are planned in one call; searches and page fetches run in parallel, alongside uploads, pasted text and MCP sources.
- **Store**: each source is registered, extracted (text, tables, figures) and split into sections and paragraphs, with character offsets kept for citations.
- **Retrieve**: PostgreSQL full-text search and a pgvector HNSW index, combined with reciprocal rank fusion. The storyline and each slide retrieve only what they need.
- **Ground**: a number on a slide must link to a source passage, or it is not drawn.

### Data model

![Data model](docs/diagrams/data-model.png)

*Figure 7: PostgreSQL tables.*

All tenant data is scoped by `organisation_id`.
A run stores its checkpointed state in `runs`, its events in `agent_events` (with a per-session `seq`), and its trace in `run_spans`.
Provider keys and connection secrets are encrypted at rest.

### Evaluation

![How a run is measured](docs/diagrams/evaluation.png)

*Figure 8: The benchmark harness.*

`python -m app.research` runs benchmark tasks without the UI, in four modes:

| Mode | Model answers come from | Use |
| --- | --- | --- |
| `claude-code` | a Claude Code session | frontier reference, no API cost |
| `replay` | recorded cassettes | regression tests |
| `record` | real providers, saved as cassettes | building fixtures |
| `live` | real providers, per-role models | real runs, capped by a spend ledger |

Runs are scored by deterministic checks (geometry, overflow, minimum text size, grounding, slide count) and by judged grades (task checklists, pairwise comparison against reference decks), with each dimension reported separately.

## Development

### Prerequisites

- Node.js 20+ (web UI)
- Python 3.11+ (AI service)
- Go 1.24+ (orchestrator)
- Docker and Docker Compose, Make

### Run services individually

```bash
make infra              # PostgreSQL and Redis only
make dev-web            # web UI
make dev-ai             # AI service
make dev-orchestrator   # orchestrator
```

### Test

```bash
make test               # all suites
make test-ai            # AI service
make test-orchestrator  # orchestrator
make test-web           # web UI
```

### Run a task headless

```bash
cd services/ai
PYTHONPATH=. .venv/bin/python -m app.research run ../../benchmark/tasks/technical/http-caching.yaml --mode replay \
    --cassette ../../benchmark/evidence/technical-http-caching/20261012-162837-claude-code/cassette.jsonl
```

### Figures

The figures in this README are generated by `docs/diagrams/src/figures.py`:

```bash
services/ai/.venv/bin/python docs/diagrams/src/figures.py    # needs rsvg-convert (librsvg)
```

Logos are from [Simple Icons](https://simpleicons.org/) and Wikimedia Commons; sources are listed in `docs/diagrams/src/logos/logos.json`.

### Project structure

```
.
├── apps/web/                 Next.js web UI
├── services/
│   ├── orchestrator/         Go: auth, admission, API, run queue, SSE hub
│   └── ai/
│       ├── app/agent/        pipeline, compile, tools, sandbox
│       ├── app/design/       designs, layouts, selection, repair
│       ├── app/renderer/     rendering, deck operations, charts, diagrams, previews
│       ├── app/research/     headless runner, checks, grading
│       └── tests/
├── packages/schemas/         shared TypeScript and Pydantic types
├── db/migrations/            SQL migrations
├── benchmark/                tasks and recorded runs
└── docs/
    ├── PROJECT_PLAN.md       roadmap, decisions and status
    └── diagrams/             README figures and their source
```

### Technology

| Layer | Stack |
| --- | --- |
| Web UI | Next.js 14, React, TypeScript, Tailwind CSS, Zustand |
| Orchestrator | Go, Gin, pgx, go-redis, golang-jwt |
| AI service | Python 3.11, FastAPI, Pydantic, SQLAlchemy 2, asyncpg, Alembic, structlog |
| Models | OpenAI, Anthropic, Z.ai (Zhipu GLM), Azure OpenAI |
| Search | Tavily or provider-native search, MCP |
| Rendering | python-pptx, lxml, matplotlib, LibreOffice, PyMuPDF |
| Data | PostgreSQL 16 + pgvector, Redis 7 |
| Tracing | PostgreSQL spans, OpenTelemetry, Langfuse |
