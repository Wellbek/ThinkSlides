"""
Per-organisation connections: storage, redaction, and resolution.

Three concerns, in the same arrangement ``app/ai_settings.py`` uses, so the two
read the same way:

1. **Storage** - reading and writing ``organisation_connections``, encrypting
   secret fields on the way in and decrypting them only at the moment they are
   used. Plaintext credentials never leave this module except as resolved
   connections.
2. **Redaction** - the read payload the API returns carries a last-four hint
   and a boolean per secret field, never the credential.
3. **Resolution** - turning stored rows into the concrete things the agent loop
   needs: the MCP endpoints to handshake, the image provider to call.

**No environment fallback.** This is the one decision worth repeating from
``ai_settings``, where its absence was hard-won:

    There was an environment fallback, and it made "not configured"
    indistinguishable from "configured": every workspace resolved to whatever
    ``.env`` held, so the setup banner never appeared, generations silently ran
    on a key nobody had chosen in the UI, and that key's billing errors
    surfaced as the run's own failure.

The same reasoning applies with more force here, because a connection is a
*third party's* system. An organisation with nothing saved resolves to nothing,
and the interface says so.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.catalog import (
    CONNECTIONS_BY_ID,
    KIND_MCP,
    MCP_PRESETS_BY_ID,
    ConnectionSpec,
    check_mcp_url,
    config_fields,
    secret_fields,
)
from app.db.models import OrganisationConnection
from app.observability.logging import get_logger
from app.security.crypto import (
    DecryptionError,
    EncryptionNotConfiguredError,
    get_cipher,
    redact_secret,
)

logger = get_logger(__name__)

TABLE = "organisation_connections"

# A slug becomes the <server> in mcp__<server>__<tool>, so it has to be safe in
# a function name the model will be shown and asked to call. slugify is what
# guarantees that; nothing else may write the column.
MAX_SLUG_LENGTH = 64


class ConnectionConfigError(ValueError):
    """A connection that cannot be saved, with a reason meant for the user."""


def encryption_context(
    org_id: uuid.UUID | str, row_id: uuid.UUID | str, field_name: str
) -> str:
    """AAD binding a ciphertext to one organisation, one row, and one field.

    Wider than ``ai_settings.encryption_context``, which binds to organisation
    and column. It has to be: an organisation can hold several MCP servers, so
    binding to the field name alone would let one server's token be copied into
    another's row and still open. The row id closes that.
    """
    return f"{TABLE}:{org_id}:{row_id}:{field_name}"


@dataclass(frozen=True)
class ResolvedConnection:
    """One enabled connection with its credentials opened, ready to be used."""

    id: uuid.UUID
    connection_id: str
    kind: str
    slug: str
    label: str
    config: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)

    @property
    def spec(self) -> ConnectionSpec | None:
        return CONNECTIONS_BY_ID.get(self.connection_id)


# ── Storage ────────────────────────────────────────────────────────────


async def load_rows(
    db: AsyncSession, org_id: uuid.UUID
) -> list[OrganisationConnection]:
    """Every configured connection for one organisation, enabled or not."""
    result = await db.execute(
        select(OrganisationConnection)
        .where(OrganisationConnection.organisation_id == org_id)
        .order_by(OrganisationConnection.created_at)
    )
    return list(result.scalars())


async def load_row(
    db: AsyncSession, org_id: uuid.UUID, row_id: uuid.UUID
) -> OrganisationConnection | None:
    """One connection, scoped to the organisation that owns it.

    Scoped deliberately: a row id from another tenant must read as "not found"
    rather than as someone else's connection.
    """
    result = await db.execute(
        select(OrganisationConnection).where(
            OrganisationConnection.organisation_id == org_id,
            OrganisationConnection.id == row_id,
        )
    )
    return result.scalar_one_or_none()


def slugify(text: str) -> str:
    """A label reduced to something safe in a tool name.

    Accents are folded rather than replaced, so "Bürgerbüro" becomes
    ``burgerburo`` and not ``b_rgerb_ro``. A label in a script that has no latin
    decomposition reduces to nothing, and the caller falls back to the
    connection's own name - which is right: a transliteration nobody recognises
    is no more useful than the default.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    lowered = re.sub(r"[^a-z0-9]+", "_", ascii_only.strip().lower())
    return lowered.strip("_")[:MAX_SLUG_LENGTH].strip("_")


def _decrypt_one(
    row: OrganisationConnection, field_name: str, envelope: str | None
) -> str:
    """Open one stored secret, returning "" rather than raising on a bad envelope.

    A credential that cannot be decrypted - rotated-away key, tampered row -
    must degrade to "not configured" so the app stays usable and the operator
    gets a clear log line, instead of every request 500ing. Same choice as
    ``ai_settings._decrypt``, same reason.
    """
    if not envelope:
        return ""
    try:
        return get_cipher().decrypt(
            envelope, encryption_context(row.organisation_id, row.id, field_name)
        )
    except (DecryptionError, EncryptionNotConfiguredError) as exc:
        logger.error(
            "connection_secret_unreadable",
            organisation_id=str(row.organisation_id),
            connection=str(row.id),
            field=field_name,
            error=str(exc),
        )
        return ""


def decrypt_secrets(row: OrganisationConnection) -> dict:
    """Every stored secret for one row, by field name."""
    stored = dict(row.secrets_encrypted or {})
    return {
        name: value
        for name, envelope in stored.items()
        if (value := _decrypt_one(row, name, envelope))
    }


def _validate(spec: ConnectionSpec, config: dict, secrets: dict, *,
              existing_secrets: dict) -> None:
    """Refuse a connection the catalogue says is incomplete or out of scope."""
    if not spec.available:
        raise ConnectionConfigError(
            spec.unavailable_reason or f"{spec.label} cannot be configured yet."
        )

    if spec.kind == KIND_MCP:
        ok, reason = check_mcp_url(str(config.get("url") or ""))
        if not ok:
            raise ConnectionConfigError(reason)

    for f in spec.fields:
        if not f.required:
            continue
        if f.secret:
            # A required secret is satisfied by one already stored, which is
            # what lets the UI render a credential it can never read back.
            if not (secrets.get(f.name) or existing_secrets.get(f.name)):
                raise ConnectionConfigError(f"{f.label} is required.")
        elif not str(config.get(f.name) or "").strip():
            raise ConnectionConfigError(f"{f.label} is required.")


def _apply_preset(config: dict) -> dict:
    """Fill an MCP endpoint in from a vetted preset, if one was named.

    The URL comes from the catalogue rather than from the request, so a preset
    cannot be used as a way to have a checked-looking connection point
    somewhere else.
    """
    preset = MCP_PRESETS_BY_ID.get(str(config.get("preset") or ""))
    if preset is None:
        return config
    return {**config, "url": preset.url, "transport": preset.transport}


async def save_connection(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    connection_id: str,
    label: str,
    config: dict | None = None,
    secrets: dict | None = None,
    enabled: bool | None = None,
    row_id: uuid.UUID | None = None,
) -> OrganisationConnection:
    """Create or update one connection.

    Secrets are tri-state, exactly as in ``ai_settings.save_settings``:

    - field absent / ``None`` -> leave the stored credential untouched
    - field ``""``            -> clear it
    - any other string        -> replace it

    This is what lets the UI render a credential it can never read back: it
    omits the field unless the user typed a new one.
    """
    spec = CONNECTIONS_BY_ID.get(connection_id)
    if spec is None:
        raise ConnectionConfigError(f"Unknown connection '{connection_id}'.")

    row = None
    if row_id is not None:
        row = await load_row(db, org_id, row_id)
        if row is None:
            raise ConnectionConfigError("That connection no longer exists.")

    incoming_config = _sanitise_config(spec, dict(config or {}))
    if spec.kind == KIND_MCP:
        incoming_config = _apply_preset(incoming_config)

    merged_config = {**(row.config if row else {}), **incoming_config}
    incoming_secrets = {
        name: value for name, value in (secrets or {}).items()
        if name in set(secret_fields(spec)) and value is not None
    }

    _validate(spec, merged_config, incoming_secrets,
              existing_secrets=dict(row.secrets_encrypted or {}) if row else {})

    if row is None:
        row = OrganisationConnection(
            id=uuid.uuid4(),
            organisation_id=org_id,
            connection_id=connection_id,
            slug=await _unique_slug(db, org_id, connection_id, label, spec),
            label=label.strip() or spec.label,
            enabled=True,
            config={},
            secrets_encrypted={},
            secret_hints={},
        )
        db.add(row)

    row.config = merged_config
    if label.strip():
        # The label is what the user calls it; the slug stays put, because a
        # tool name that moves breaks every past run's transcript.
        row.label = label.strip()
    if enabled is not None:
        row.enabled = enabled

    _write_secrets(row, incoming_secrets)

    await db.commit()
    await db.refresh(row)
    logger.info(
        "connection_saved",
        organisation_id=str(org_id),
        connection=connection_id,
        slug=row.slug,
    )
    return row


def _sanitise_config(spec: ConnectionSpec, config: dict) -> dict:
    """Only the non-secret fields the catalogue declares, plus a preset id.

    An allowlist rather than a filter: config is stored as JSONB and read back
    in the clear, so anything accepted here is something the API will hand back
    out. A secret arriving in ``config`` is dropped rather than stored, which is
    the failure that would otherwise write a token to a readable column.
    """
    allowed = set(config_fields(spec))
    if spec.kind == KIND_MCP:
        allowed |= {"preset", "transport"}
    return {
        name: value for name, value in config.items()
        if name in allowed and value is not None
    }


def _write_secrets(row: OrganisationConnection, secrets: dict) -> None:
    """Seal, clear or keep each secret, per the tri-state rule."""
    envelopes = dict(row.secrets_encrypted or {})
    hints = dict(row.secret_hints or {})

    for name, value in secrets.items():
        if value == "":
            envelopes.pop(name, None)
            hints.pop(name, None)
            continue
        envelopes[name] = get_cipher().encrypt(
            value, encryption_context(row.organisation_id, row.id, name)
        )
        hints[name] = redact_secret(value)

    # Reassigned rather than mutated: SQLAlchemy does not track in-place changes
    # to a JSONB dict, so mutating would commit nothing and look like a silent
    # save failure.
    row.secrets_encrypted = envelopes
    row.secret_hints = hints


async def _unique_slug(
    db: AsyncSession, org_id: uuid.UUID, connection_id: str, label: str,
    spec: ConnectionSpec,
) -> str:
    """A slug not already taken within this organisation and connection."""
    base = slugify(label) or slugify(spec.label) or connection_id
    taken = {
        row.slug for row in await load_rows(db, org_id)
        if row.connection_id == connection_id
    }
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base}_{n}"[:MAX_SLUG_LENGTH]
        if candidate not in taken:
            return candidate
    raise ConnectionConfigError("Too many connections with that name.")


async def delete_connection(
    db: AsyncSession, org_id: uuid.UUID, row_id: uuid.UUID
) -> bool:
    """Remove one connection. Returns whether there was one to remove."""
    row = await load_row(db, org_id, row_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    logger.info("connection_deleted", organisation_id=str(org_id),
                connection=str(row_id))
    return True


# ── Redacted read payload ──────────────────────────────────────────────


def redacted_connection(row: OrganisationConnection) -> dict:
    """The client-facing view of one connection. Hints, never credentials."""
    spec = CONNECTIONS_BY_ID.get(row.connection_id)
    hints = dict(row.secret_hints or {})
    envelopes = dict(row.secrets_encrypted or {})

    return {
        "id": str(row.id),
        "connectionId": row.connection_id,
        "kind": spec.kind if spec else None,
        "slug": row.slug,
        "label": row.label,
        "enabled": bool(row.enabled),
        "config": dict(row.config or {}),
        "secrets": {
            f.name: {
                "hasValue": bool(envelopes.get(f.name)),
                "hint": hints.get(f.name),
            }
            for f in (spec.fields if spec else ())
            if f.secret
        },
        # A connection whose catalogue entry has gone is shown rather than
        # hidden: a row nobody can see is a row nobody can delete.
        "known": spec is not None,
    }


def redacted_payload(rows: list[OrganisationConnection]) -> dict:
    return {"connections": [redacted_connection(row) for row in rows]}


# ── Resolution ─────────────────────────────────────────────────────────


def resolve_row(row: OrganisationConnection) -> ResolvedConnection | None:
    """One row with its secrets opened, or None if it cannot be used.

    Returns None for a disabled row, an unknown connection id, or one whose
    required credential will not decrypt - all three of which are "not
    configured" from the loop's point of view, and none of which are worth
    failing a run over.
    """
    spec = CONNECTIONS_BY_ID.get(row.connection_id)
    if spec is None:
        logger.warning("connection_unknown_id", connection_id=row.connection_id)
        return None
    if not row.enabled:
        return None

    secrets = decrypt_secrets(row)
    missing = [
        f.label for f in spec.fields
        if f.required and f.secret and not secrets.get(f.name)
    ]
    if missing:
        logger.warning(
            "connection_incomplete",
            connection=str(row.id), missing=", ".join(missing),
        )
        return None

    return ResolvedConnection(
        id=row.id,
        connection_id=row.connection_id,
        kind=spec.kind,
        slug=row.slug,
        label=row.label,
        config=dict(row.config or {}),
        secrets=secrets,
    )


async def resolve_enabled(
    db: AsyncSession, org_id: uuid.UUID, kind: str | None = None
) -> list[ResolvedConnection]:
    """Every usable connection for one organisation, optionally of one kind.

    An organisation with nothing saved gets an empty list. There is no
    environment fallback, for the reason the module docstring gives.
    """
    resolved = []
    for row in await load_rows(db, org_id):
        one = resolve_row(row)
        if one is None:
            continue
        if kind is not None and one.kind != kind:
            continue
        resolved.append(one)
    return resolved
