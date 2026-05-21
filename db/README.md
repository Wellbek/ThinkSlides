# Database Migrations

SQL migration files executed in alphabetical order by Docker entrypoint.

## Files

1. `001_create_extensions.sql` — Enable pgvector and uuid-ossp
2. `002_create_core_tables.sql` — Organisations, users, templates, design system bundles
3. `003_create_chat_tables.sql` — Chat sessions, messages, agent events, generated files, documents, chunks

## Manual Migration

```bash
# Using Alembic (AI service)
cd services/ai && alembic upgrade head

# Using psql directly
psql -U slideagent -d slideagent -f db/migrations/001_create_extensions.sql
psql -U slideagent -d slideagent -f db/migrations/002_create_core_tables.sql
psql -U slideagent -d slideagent -f db/migrations/003_create_chat_tables.sql
```