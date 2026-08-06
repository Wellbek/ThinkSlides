"""
Settings API: configure the chat model, the Deep Thinking model, and the
deep-search backend, and verify credentials before relying on them.

API keys are write-only across this boundary. A read returns a last-four hint
and a boolean; a write either supplies a new key or omits the field to keep the
stored one. There is no endpoint that returns key material.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_settings
from app.api.tenancy import resolve_org_id
from app.db.session import get_db
from app.llm.catalog import PROVIDER_IDS, SEARCH_BACKEND_IDS, catalog_payload
from app.llm.provider import ProviderCredentials, build_chat_provider
from app.observability.logging import get_logger
from app.security.crypto import EncryptionNotConfiguredError

logger = get_logger(__name__)

router = APIRouter(tags=["settings"])

# Kept short so a credential check fails fast instead of hanging the UI.
TEST_PROMPT = "Reply with exactly: OK"
TEST_MAX_TOKENS = 32


class ModelRoleInput(BaseModel):
    """One configurable model role (generic chat, or Deep Thinking).

    ``apiKey`` is tri-state: omitted keeps the stored key, ``""`` clears it,
    anything else replaces it.
    """

    provider: str | None = None
    model: str | None = None
    baseUrl: str | None = None
    apiKey: str | None = None


class SearchBackendInput(BaseModel):
    backend: str | None = None
    baseUrl: str | None = None
    apiKey: str | None = None


class AISettingsUpdate(BaseModel):
    chat: ModelRoleInput | None = None
    deepSearch: ModelRoleInput | None = None
    searchBackend: SearchBackendInput | None = None


class TestChatRequest(BaseModel):
    """Test a candidate config, defaulting each field to what is stored.

    Lets the user click "Test" before saving, without ever re-sending a key
    they cannot read back.
    """

    provider: str | None = None
    model: str | None = None
    baseUrl: str | None = None
    apiKey: str | None = None
    role: str = Field(default="chat", pattern="^(chat|deepSearch)$")


def _validate_role(role: ModelRoleInput | None, label: str) -> None:
    if role is None:
        return
    if role.provider is not None and role.provider not in PROVIDER_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown {label} provider '{role.provider}'. "
                   f"Expected one of: {', '.join(PROVIDER_IDS)}.",
        )


def _to_section(role: ModelRoleInput | None) -> dict | None:
    """Translate the camelCase API shape into the storage shape.

    Only fields the client actually sent are included, so an omitted field
    leaves the stored value alone.
    """
    if role is None:
        return None
    fields = role.model_fields_set
    section: dict = {}
    if "provider" in fields:
        section["provider"] = role.provider
    if "model" in fields:
        section["model"] = role.model
    if "baseUrl" in fields:
        section["base_url"] = role.baseUrl
    if "apiKey" in fields:
        section["api_key"] = role.apiKey
    return section


def describe_provider_error(exc: Exception) -> str:
    """Turn a provider SDK exception into something a user can act on.

    Provider SDKs raise wildly different exception types; what they share is an
    HTTP status. Mapping the common ones to plain guidance is far more useful
    than surfacing a stack-trace-flavoured repr.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)

    detail = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    detail = detail.strip()

    hints = {
        401: "Authentication failed - the API key was rejected.",
        403: "The API key is valid but not permitted to use this model.",
        404: "The model was not found. Check the model id and the base URL.",
        422: "The provider rejected the request parameters.",
        429: "Rate limited or out of quota.",
    }
    if status in hints:
        return f"{hints[status]} ({detail})"
    if status and status >= 500:
        return f"The provider returned a server error ({status}). Try again shortly. ({detail})"
    if "connect" in detail.lower() or "timeout" in detail.lower():
        return f"Could not reach the provider endpoint. Check the base URL and network access. ({detail})"
    return detail


@router.get("/catalog")
async def get_catalog() -> dict:
    """Selectable providers and deep-search backends, for the Settings UI."""
    return catalog_payload()


@router.get("/ai")
async def get_ai_settings(
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Current settings with API keys redacted, plus the readiness summary."""
    row = await ai_settings.load_row(db, org_id)
    payload = ai_settings.redacted_payload(row)
    payload["status"] = ai_settings.configuration_status(row)
    return payload


@router.put("/ai")
async def update_ai_settings(
    update: AISettingsUpdate,
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply a partial settings update."""
    _validate_role(update.chat, "chat")
    _validate_role(update.deepSearch, "deep search")

    backend_section = None
    if update.searchBackend is not None:
        backend = update.searchBackend
        if backend.backend is not None and backend.backend not in SEARCH_BACKEND_IDS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown search backend '{backend.backend}'. "
                       f"Expected one of: {', '.join(SEARCH_BACKEND_IDS)}.",
            )
        fields = backend.model_fields_set
        backend_section = {}
        if "backend" in fields:
            backend_section["backend"] = backend.backend
        if "baseUrl" in fields:
            backend_section["backend_base_url"] = backend.baseUrl
        if "apiKey" in fields:
            backend_section["api_key"] = backend.apiKey

    try:
        row = await ai_settings.save_settings(
            db,
            org_id,
            chat=_to_section(update.chat),
            deep_search=_to_section(update.deepSearch),
            search_backend=backend_section,
        )
    except EncryptionNotConfiguredError as exc:
        # Fail closed: never fall back to storing credentials in plaintext.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    payload = ai_settings.redacted_payload(row)
    payload["status"] = ai_settings.configuration_status(row)
    return payload


@router.post("/ai/test-chat")
async def test_chat_credentials(
    req: TestChatRequest,
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send a minimal prompt to the configured model and report the result.

    Never raises on a provider failure - the error is parsed into ``error`` so
    the UI can show the user what to fix.
    """
    row = await ai_settings.load_row(db, org_id)

    if req.role == "deepSearch":
        stored = ai_settings.resolve_deep_search_model(row)
        stored_prefix, key_field = "search_", ai_settings.SEARCH_KEY_FIELD
    else:
        stored = ai_settings.resolve_chat_credentials(row)
        stored_prefix, key_field = "chat_", ai_settings.CHAT_KEY_FIELD

    provider = req.provider or (stored.provider if stored else None)
    model = req.model or (stored.model if stored else None)
    base_url = req.baseUrl if req.baseUrl is not None else (stored.base_url if stored else None)

    api_key = req.apiKey
    if not api_key:
        # The user did not retype the key - use the stored one for this role,
        # falling back to whatever resolution produced (e.g. the chat key when
        # deep search inherits it).
        api_key = ai_settings.decrypt_stored_key(row, key_field)
        if not api_key and stored is not None and stored.provider == provider:
            api_key = stored.api_key

    missing = [
        name for name, value in
        (("provider", provider), ("model", model), ("API key", api_key))
        if not value
    ]
    if missing:
        return {
            "ok": False,
            "error": f"Nothing to test yet - missing {', '.join(missing)}.",
        }

    try:
        client = build_chat_provider(
            ProviderCredentials(
                provider=provider, model=model, api_key=api_key, base_url=base_url
            )
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    start = time.time()
    try:
        response = await client.chat(
            messages=[{"role": "user", "content": TEST_PROMPT}],
            temperature=0.0,
            max_tokens=TEST_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - every SDK raises its own types
        logger.warning(
            "credential_test_failed",
            organisation_id=str(org_id),
            provider=provider,
            model=model,
            error=str(exc),
        )
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "error": describe_provider_error(exc),
        }

    reply = (response.get("message") or {}).get("content", "").strip()
    logger.info(
        "credential_test_succeeded",
        organisation_id=str(org_id),
        provider=provider,
        model=model,
    )
    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "latencyMs": int((time.time() - start) * 1000),
        "reply": reply[:200],
    }
