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

    # Azure OpenAI
    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_deployment: str = "gpt-4"
    azure_openai_api_version: str = "2024-02-15-preview"

    # ── Vision LLM (for PDF/image-based template analysis) ──
    vision_llm_provider: Literal["zhipu", "azure_openai"] = "zhipu"
    zhipu_vision_model: str = "glm-4.6v"
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
