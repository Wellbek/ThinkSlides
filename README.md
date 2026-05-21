# ThinkSlides

Enterprise-grade, multi-tenant web application that generates fully branded, template-faithful
PowerPoint presentations through a conversational LLM chat interface combined with document
and data ingestion.

## Architecture

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────────┐
│  Next.js 14  │────▶│   Go Orchestrator │────▶│  Python AI Svc   │
│  App Router  │◀────│   (Gin + SSE)     │◀────│  (FastAPI+Agent)  │
│  Frontend    │     │   Redis Queue     │     │  Zhipu GLM LLM   │
└──────────────┘     └───────────────────┘     └──────────────────┘
       │                     │                          │
       │              ┌──────┴──────┐          ┌───────┴───────┐
       │              │    Redis    │          │  PostgreSQL   │
       │              │  (Jobs+SSE) │          │  + pgvector   │
       │              └─────────────┘          └───────────────┘
```

**Three-tier system:**
- **Frontend** (`apps/web`): Next.js 14 App Router, TypeScript, Tailwind CSS
- **AI Service** (`services/ai`): Python FastAPI, ReAct agent loop, LLM provider abstraction
- **Orchestrator** (`services/orchestrator`): Go Gin, SSE streaming, Redis job queue

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

## Project Structure

```
.
├── apps/
│   └── web/                    # Next.js 14 frontend
│       ├── src/
│       │   ├── app/            # App Router pages
│       │   ├── components/     # React components
│       │   ├── hooks/          # Custom hooks (SSE, chat, auth)
│       │   ├── lib/            # API clients, auth, SSE
│       │   ├── stores/         # Zustand state stores
│       │   └── types/          # TypeScript types
│       └── Dockerfile
├── services/
│   ├── ai/                     # Python FastAPI AI service
│   │   ├── app/
│   │   │   ├── agent/          # ReAct agent loop + tools
│   │   │   ├── api/            # FastAPI routes
│   │   │   ├── db/             # SQLAlchemy models
│   │   │   ├── ingestion/      # Document parsing workers
│   │   │   ├── llm/            # LLM provider abstraction
│   │   │   ├── renderer/       # PPTX XML renderer
│   │   │   └── observability/  # Structured logging
│   │   ├── alembic/            # DB migrations
│   │   └── Dockerfile
│   └── orchestrator/           # Go Gin orchestrator
│       ├── cmd/server/         # Entry point
│       ├── internal/           # Internal packages
│       │   ├── handler/        # HTTP handlers
│       │   ├── queue/          # Redis job queue
│       │   ├── sse/            # SSE emitter
│       │   └── middleware/     # Auth, CORS
│       ├── proto/              # gRPC definitions
│       └── Dockerfile
├── packages/
│   └── schemas/                # Shared type schemas (TS + Python)
│       ├── src/                # TypeScript schemas
│       └── python/             # Python Pydantic schemas
├── db/
│   └── migrations/             # SQL migration files
├── docker-compose.yml
├── Makefile
└── .env.example
```

## Key Design Decisions

1. **Template-agnostic**: Users manage their own PPTX/POTX templates; the system extracts design systems at ingestion time
2. **XML-clone rendering**: The PPTX renderer clones real layout XML and fills placeholders without touching rPr formatting
3. **ReAct agent loop**: Structured tool registry with parallel execution where independent
4. **LLM abstraction**: Defaults to Zhipu GLM (glm-4), swap to Azure OpenAI via env vars
5. **SSE streaming**: All agent thoughts, tool calls, and results stream to the UI in real time
6. **Multi-tenant**: JWT auth with org-level isolation from day one
