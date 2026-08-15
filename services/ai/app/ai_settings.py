"""
Per-organisation AI settings: storage, redaction, and resolution.

Three concerns live here so the API layer stays thin:

1. **Storage** - reading and writing ``organisation_ai_settings``, encrypting
   API keys on the way in and decrypting them only at the moment they are used.
   Plaintext keys never leave this module except as provider credentials.
2. **Redaction** - the read payload the API returns carries a last-four hint
   and a boolean, never the key.
3. **Resolution** - turning the stored rows into the concrete credentials the
   agent loop and the ``web_search`` tool need.

Resolution reads the stored rows and nothing else. There was an environment
fallback, and it made "not configured" indistinguishable from "configured":
every workspace resolved to whatever ``.env`` held, so the setup banner never
appeared, generations silently ran on a key nobody had chosen in the UI, and
that key's billing errors surfaced as the run's own failure. An organisation
with nothing saved now resolves to None, which is a state the API reports and
the interface acts on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OrganisationAISettings
from app.llm.catalog import PROVIDERS_BY_ID, SEARCH_BACKENDS_BY_ID
from app.llm.provider import ProviderCredentials
from app.observability.logging import get_logger
from app.security.crypto import (
    DecryptionError,
    EncryptionNotConfiguredError,
    get_cipher,
    redact_secret,
)

logger = get_logger(__name__)

# Field names used both as column prefixes and as the encryption context, so a
# ciphertext is bound to the exact column it was written to.
CHAT_KEY_FIELD = "chat_api_key"
SEARCH_KEY_FIELD = "search_api_key"
BACKEND_KEY_FIELD = "search_backend_api_key"


def encryption_context(org_id: uuid.UUID | str, field: str) -> str:
    """AAD binding a ciphertext to one organisation and one column."""
    return f"organisation_ai_settings:{org_id}:{field}"


@dataclass(frozen=True)
class SearchConfig:
    """Everything the ``web_search`` tool needs for one Deep Thinking run."""

    backend: str
    # Credentials of the model that performs the search. Only used by the
    # "native" backend, which searches through the provider's own API.
    model: ProviderCredentials | None = None
    # Credentials for an external backend (Tavily, Firecrawl).
    api_key: str = ""
    base_url: str | None = None


# ── Storage ────────────────────────────────────────────────────────────


async def load_row(db: AsyncSession, org_id: uuid.UUID) -> OrganisationAISettings | None:
    result = await db.execute(
        select(OrganisationAISettings).where(
            OrganisationAISettings.organisation_id == org_id
        )
    )
    return result.scalar_one_or_none()


def _decrypt(row: OrganisationAISettings, field: str, ciphertext: str | None) -> str:
    """Open one stored key, returning "" rather than raising on a bad envelope.

    A key that cannot be decrypted (rotated-away KEK, tampered row) must
    degrade to "not configured" so the app stays usable and the operator sees
    a clear log line, instead of every request 500ing.
    """
    if not ciphertext:
        return ""
    try:
        return get_cipher().decrypt(
            ciphertext, encryption_context(row.organisation_id, field)
        )
    except (DecryptionError, EncryptionNotConfiguredError) as exc:
        logger.error(
            "settings_secret_unreadable",
            organisation_id=str(row.organisation_id),
            field=field,
            error=str(exc),
        )
        return ""


def decrypt_stored_key(row: OrganisationAISettings | None, field: str) -> str:
    """Read back one stored key by field name, or "" if there is none."""
    if row is None:
        return ""
    return _decrypt(row, field, getattr(row, f"{field}_encrypted"))


def _encrypt(org_id: uuid.UUID, field: str, plaintext: str) -> tuple[str, str]:
    """Seal one key, returning ``(envelope, display_hint)``."""
    envelope = get_cipher().encrypt(plaintext, encryption_context(org_id, field))
    return envelope, redact_secret(plaintext)


async def save_settings(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    chat: dict | None = None,
    deep_search: dict | None = None,
    search_backend: dict | None = None,
) -> OrganisationAISettings:
    """Apply a partial update.

    Each section is a dict of the fields to change. For API keys the three
    states are distinct and meaningful:

    - key absent / ``None`` -> leave the stored key untouched
    - key ``""``            -> clear the stored key
    - key any other string  -> replace the stored key

    This is what lets the UI render a key it can never read back: it simply
    omits the field unless the user typed a new one.
    """
    row = await load_row(db, org_id)
    if row is None:
        row = OrganisationAISettings(organisation_id=org_id)
        db.add(row)

    def apply(section: dict | None, prefix: str, key_field: str, fields: tuple[str, ...]) -> None:
        if section is None:
            return
        for name in fields:
            if name in section:
                setattr(row, f"{prefix}{name}", (section[name] or None))
        if "api_key" not in section:
            return
        api_key = section["api_key"]
        if api_key is None:
            return
        if api_key == "":
            setattr(row, f"{key_field}_encrypted", None)
            setattr(row, f"{key_field}_hint", None)
            return
        envelope, hint = _encrypt(org_id, key_field, api_key)
        setattr(row, f"{key_field}_encrypted", envelope)
        setattr(row, f"{key_field}_hint", hint)

    apply(chat, "chat_", CHAT_KEY_FIELD, ("provider", "model", "base_url"))
    apply(deep_search, "search_", SEARCH_KEY_FIELD, ("provider", "model", "base_url"))
    apply(search_backend, "search_", BACKEND_KEY_FIELD, ("backend", "backend_base_url"))

    await db.commit()
    await db.refresh(row)
    logger.info("ai_settings_saved", organisation_id=str(org_id))
    return row


# ── Redacted read payload ──────────────────────────────────────────────


def redacted_payload(row: OrganisationAISettings | None) -> dict:
    """The client-facing view. Contains hints, never key material."""
    if row is None:
        empty_role = {"provider": None, "model": None, "baseUrl": None,
                      "apiKeyHint": None, "hasApiKey": False}
        return {
            "chat": dict(empty_role),
            "deepSearch": dict(empty_role),
            "searchBackend": {"backend": None, "baseUrl": None,
                              "apiKeyHint": None, "hasApiKey": False},
        }

    return {
        "chat": {
            "provider": row.chat_provider,
            "model": row.chat_model,
            "baseUrl": row.chat_base_url,
            "apiKeyHint": row.chat_api_key_hint,
            "hasApiKey": bool(row.chat_api_key_encrypted),
        },
        "deepSearch": {
            "provider": row.search_provider,
            "model": row.search_model,
            "baseUrl": row.search_base_url,
            "apiKeyHint": row.search_api_key_hint,
            "hasApiKey": bool(row.search_api_key_encrypted),
        },
        "searchBackend": {
            "backend": row.search_backend,
            "baseUrl": row.search_backend_base_url,
            "apiKeyHint": row.search_backend_api_key_hint,
            "hasApiKey": bool(row.search_backend_api_key_encrypted),
        },
    }


# ── Resolution ─────────────────────────────────────────────────────────


def _credentials_from_row(
    row: OrganisationAISettings, prefix: str, key_field: str
) -> ProviderCredentials | None:
    provider = getattr(row, f"{prefix}provider")
    model = getattr(row, f"{prefix}model")
    api_key = _decrypt(row, key_field, getattr(row, f"{key_field}_encrypted"))
    if not (provider and model and api_key):
        return None
    if provider not in PROVIDERS_BY_ID:
        logger.warning("settings_unknown_provider", provider=provider)
        return None
    return ProviderCredentials(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=getattr(row, f"{prefix}base_url"),
    )


def resolve_chat_credentials(row: OrganisationAISettings | None) -> ProviderCredentials | None:
    """Credentials for the generic chat model, or None if nothing is usable.

    Settings is the only source. There was an environment fallback here, and
    what it produced was a workspace that looked configured without ever having
    been: the setup banner never appeared because ``chatConfigured`` was always
    true, generations ran on whatever key ``.env`` held, and when that key was
    exhausted the failure named a provider the user had never chosen.
    """
    if row is None:
        return None
    return _credentials_from_row(row, "chat_", CHAT_KEY_FIELD)


def resolve_deep_search_model(
    row: OrganisationAISettings | None,
) -> ProviderCredentials | None:
    """Credentials for the Deep Thinking model.

    Falls back to the chat model so an organisation that only wants a search
    backend does not have to configure the same provider twice.
    """
    if row is not None:
        stored = _credentials_from_row(row, "search_", SEARCH_KEY_FIELD)
        if stored:
            return stored
    return resolve_chat_credentials(row)


def _native_search_supported(credentials: ProviderCredentials | None) -> tuple[bool, str]:
    """Whether the provider can search on its own, plus the reason if not."""
    if credentials is None:
        return False, "No Deep Thinking model is configured."
    spec = PROVIDERS_BY_ID.get(credentials.provider)
    if spec is None or not spec.native_web_search:
        return False, f"{credentials.provider} has no built-in web search."
    if spec.native_web_search_models and credentials.model not in spec.native_web_search_models:
        allowed = ", ".join(spec.native_web_search_models)
        # Name the field, because the obvious reading - "change my model to
        # one of those" - puts a search-only model in the chat role, where it
        # breaks every generation.
        return False, (
            f"Set the Deep Thinking model to one of: {allowed}. "
            f"{spec.label}'s built-in web search does not work with "
            f"'{credentials.model}'. Leave the chat model as it is - those "
            "search models cannot drive the agent."
        )
    if credentials.provider == "zhipu" and "." not in credentials.api_key:
        return False, "Zhipu web search needs an API key in 'id.secret' form."
    return True, ""


def resolve_search_config(
    row: OrganisationAISettings | None,
) -> tuple[SearchConfig | None, str]:
    """Resolve the Deep Thinking search backend.

    Returns ``(config, reason)``. ``config`` is None when Deep Thinking cannot
    run; ``reason`` then explains why, in words meant for the user.
    """
    model = resolve_deep_search_model(row)
    backend = row.search_backend if row is not None else None

    if backend is None:
        # Nothing saved. The provider's own search is the only thing left to
        # try - there is deliberately no environment fallback, for the same
        # reason resolve_chat_credentials has none.
        ok, reason = _native_search_supported(model)
        if ok:
            return SearchConfig(backend="native", model=model), ""
        return None, reason or "No deep-search backend is configured."

    if backend not in SEARCH_BACKENDS_BY_ID:
        return None, f"Unknown deep-search backend '{backend}'."

    if backend == "native":
        ok, reason = _native_search_supported(model)
        if not ok:
            return None, reason
        return SearchConfig(backend="native", model=model), ""

    api_key = _decrypt(row, BACKEND_KEY_FIELD, row.search_backend_api_key_encrypted)
    if not api_key:
        label = SEARCH_BACKENDS_BY_ID[backend].label
        return None, f"No API key is configured for the {label} search backend."
    return SearchConfig(
        backend=backend, api_key=api_key, base_url=row.search_backend_base_url
    ), ""


def configuration_status(row: OrganisationAISettings | None) -> dict:
    """Summary the UI uses to gate Deep Thinking and raise the setup banner."""
    chat = resolve_chat_credentials(row)
    search_config, search_reason = resolve_search_config(row)

    return {
        "chatConfigured": chat is not None,
        "chatProvider": chat.provider if chat else None,
        "chatModel": chat.model if chat else None,
        "deepThinkingAvailable": search_config is not None,
        "deepSearchBackend": search_config.backend if search_config else None,
        "deepThinkingUnavailableReason": search_reason or None,
        "encryptionAvailable": _encryption_available(),
    }


def _encryption_available() -> bool:
    try:
        get_cipher()
        return True
    except EncryptionNotConfiguredError:
        return False
