from pydantic_settings import BaseSettings
from typing import Literal, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Database ────────────────────────────────
    database_url: str = "postgresql+asyncpg://thinkslides:thinkslides_dev_password@postgres:5432/thinkslides"

    # ── Redis ───────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── LLM Provider ────────────────────────────
    llm_provider: Literal["zhipu", "azure_openai"] = "zhipu"

    # Zhipu GLM
    zhipu_api_key: str = ""
    zhipu_model: str = "glm-4.7"
    zhipu_flash_model: str = "glm-4.5-flash"  # Cheap model for title generation
    # Zhipu web search engine name (required by the web_search agent tool).
    # Configurable so it can be adjusted without a code change if Zhipu renames it.
    zhipu_web_search_engine: str = "search_std"
    # Zhipu web search content size: "low" | "medium" | "high". Higher returns
    # longer snippets so the agent has more to ground slide content on.
    zhipu_web_search_content_size: str = "high"

    # ── Web Search Backend ──────────────────────
    # Which backend the web_search agent tool uses. Zhipu's search API is
    # China-focused (English factual queries mostly return Chinese sources),
    # so a Western backend (Tavily) is offered as an alternative for better
    # source quality on non-Chinese content. The Zhipu backend remains the
    # default so existing deployments keep working unchanged.
    web_search_provider: Literal["zhipu", "tavily"] = "zhipu"
    # Tavily API key (required when WEB_SEARCH_PROVIDER=tavily).
    tavily_api_key: str = ""
    # Tavily search depth: "basic" (faster, cheaper) | "advanced" (more
    # thorough, longer snippets). Defaults to advanced so factual grounding is
    # as strong as possible.
    tavily_search_depth: Literal["basic", "advanced"] = "advanced"
    # Azure OpenAI
    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_deployment: str = "gpt-4"
    azure_openai_api_version: str = "2024-02-15-preview"

    # ── Vision LLM (for PDF/image-based template analysis) ──
    # Which model looks at an image is the workspace's choice in Settings,
    # like every other model in the product - see build_vision_provider. There
    # is deliberately no environment default: one here meant a workspace that
    # had configured nothing still read the user's own template on somebody
    # else's key.
    azure_openai_vision_deployment: str = "gpt-4o"

    # ── Storage ─────────────────────────────────
    upload_dir: str = "/app/uploads"
    template_dir: str = "/app/templates"
    output_dir: str = "/app/output"
    max_upload_size_mb: int = 50

    # ── Observability ───────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"

    # ── JWT ─────────────────────────────────────
    jwt_secret: str = "thinkslides-dev-jwt-secret-change-in-production"
    jwt_algorithm: str = "HS256"

    # `extra="ignore"` is load-bearing, not tidiness. `.env` at the repo root
    # is the single file the setup docs tell you to create, and it carries the
    # web and orchestrator settings too (PORT, AI_SERVICE_URL,
    # NEXT_PUBLIC_*, ...). Pydantic's default is `extra="forbid"`, so every one
    # of those raised `extra_forbidden` and `Settings()` - constructed at
    # import time on the last line of this module - aborted the import of
    # anything under `app.`. That made the whole AI service unimportable from
    # the repo root: tests, the renderer, `uvicorn app.main:app`. It only
    # worked in Docker because the container's WORKDIR has no `.env`.
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
