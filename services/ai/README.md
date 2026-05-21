# SlideAgent AI Service

Python FastAPI service handling all AI and document processing workloads.

## Architecture

- **Agent Loop**: ReAct (Reason-Act-Observe) pattern with structured tool registry
- **LLM Provider**: Abstracted interface defaulting to Zhipu GLM, swappable to Azure OpenAI
- **PPTX Renderer**: Clones real layout XML, fills placeholders without touching rPr
- **Document Ingestion**: Async worker pool with chunking and pgvector embedding storage

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run database migrations
alembic upgrade head
```

## Environment Variables

See `.env.example` in the project root for all configuration options.

Key variables:
- `LLM_PROVIDER` — "zhipu" or "azure_openai"
- `ZHIPU_API_KEY` — Your Zhipu API key
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string