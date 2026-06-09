# ThinkSlides Orchestrator

Go Gin service for high-performance task orchestration, job queue management, and SSE streaming.

## Architecture

- **HTTP API**: Gin router with JWT auth middleware
- **Job Queue**: Redis-backed LPUSH/BRPOP with job status tracking
- **SSE Hub**: In-memory pub/sub for real-time event streaming to clients
- **Job Processor**: Background worker that dequeues jobs and dispatches to the AI service

## Running Locally

```bash
# Install dependencies
go mod download

# Run the service
go run cmd/server/main.go
```

## Environment Variables

- `ORCHESTRATOR_PORT` — HTTP port (default: 8080)
- `REDIS_URL` — Redis connection string
- `AI_SERVICE_URL` — Python AI service URL
- `DATABASE_URL` — PostgreSQL connection string
- `JWT_SECRET` — Secret for JWT token signing