.PHONY: dev dev-web dev-ai dev-orchestrator build test migrate clean

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
include .env

DOCKER_COMPOSE = docker compose

# ──────────────────────────────────────────────
# Development (all services)
# ──────────────────────────────────────────────
dev: .env
	$(DOCKER_COMPOSE) up --build

dev-detach: .env
	$(DOCKER_COMPOSE) up --build -d

# ──────────────────────────────────────────────
# Individual service development
# ──────────────────────────────────────────────
dev-web:
	cd apps/web && npm run dev

dev-ai:
	cd services/ai && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-orchestrator:
	cd services/orchestrator && go run cmd/server/main.go

# ──────────────────────────────────────────────
# Build
# ──────────────────────────────────────────────
build:
	$(DOCKER_COMPOSE) build

build-web:
	cd apps/web && docker build -t thinkslides-web .

build-ai:
	cd services/ai && docker build -t thinkslides-ai .

build-orchestrator:
	cd services/orchestrator && docker build -t thinkslides-orchestrator .

# ──────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────
test: test-web test-ai test-orchestrator

test-web:
	cd apps/web && npm test

test-ai:
	cd services/ai && python -m pytest tests/ -v

test-orchestrator:
	cd services/orchestrator && go test ./... -v

# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
migrate:
	$(DOCKER_COMPOSE) exec ai-service alembic upgrade head

migrate-down:
	$(DOCKER_COMPOSE) exec ai-service alembic downgrade -1

migrate-create:
	cd services/ai && alembic revision --autogenerate -m "$(msg)"

# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────
clean:
	$(DOCKER_COMPOSE) down -v --remove-orphans
	rm -rf node_modules apps/web/node_modules apps/web/.next
	rm -rf services/ai/__pycache__ services/ai/.pytest_cache

logs:
	$(DOCKER_COMPOSE) logs -f

logs-ai:
	$(DOCKER_COMPOSE) logs -f ai-service

logs-orchestrator:
	$(DOCKER_COMPOSE) logs -f orchestrator

.env:
	cp .env.example .env

# ──────────────────────────────────────────────
# Infrastructure only (no app services)
# ──────────────────────────────────────────────
infra:
	$(DOCKER_COMPOSE) up -d postgres redis

infra-down:
	$(DOCKER_COMPOSE) stop postgres redis