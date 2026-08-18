"""
Connections API: list, save, enable and remove an organisation's connections.

Credentials are write-only across this boundary, exactly as in the settings API.
A read returns a last-four hint and a boolean per secret field; a write either
supplies a new credential or omits the field to keep the stored one. There is no
endpoint that returns credential material.

The catalogue itself is served by ``GET /settings/catalog``, which merges it with
the provider catalogue - one fetch, because a user configuring a workspace does
not care which module a choice came from.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenancy import resolve_org_id
from app.connections import store
from app.db.session import get_db
from app.observability.logging import get_logger
from app.security.crypto import EncryptionNotConfiguredError

logger = get_logger(__name__)

router = APIRouter(tags=["connections"])


class ConnectionInput(BaseModel):
    """One connection to create or update.

    ``secrets`` is tri-state per field, the same as ``apiKey`` in the settings
    API: a field omitted keeps the stored credential, ``""`` clears it, anything
    else replaces it. That is what lets the UI render a credential it can never
    read back.
    """

    connectionId: str
    label: str = ""
    config: dict = {}
    secrets: dict = {}
    enabled: bool | None = None


@router.get("")
async def list_connections(
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Every configured connection, with credentials redacted."""
    rows = await store.load_rows(db, org_id)
    return store.redacted_payload(rows)


@router.post("")
async def create_connection(
    body: ConnectionInput,
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _save(db, org_id, body, row_id=None)


@router.put("/{connection_row_id}")
async def update_connection(
    connection_row_id: uuid.UUID,
    body: ConnectionInput,
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _save(db, org_id, body, row_id=connection_row_id)


@router.delete("/{connection_row_id}")
async def delete_connection(
    connection_row_id: uuid.UUID,
    org_id: uuid.UUID = Depends(resolve_org_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    removed = await store.delete_connection(db, org_id, connection_row_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such connection.")
    return {"deleted": True}


async def _save(
    db: AsyncSession,
    org_id: uuid.UUID,
    body: ConnectionInput,
    *,
    row_id: uuid.UUID | None,
) -> dict:
    try:
        row = await store.save_connection(
            db, org_id,
            connection_id=body.connectionId,
            label=body.label,
            config=body.config,
            secrets=body.secrets,
            enabled=body.enabled,
            row_id=row_id,
        )
    except store.ConnectionConfigError as exc:
        # The message is already written for the user - the whole point of
        # raising it from the store rather than returning a bool.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EncryptionNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Credential encryption is not configured on this deployment, so "
                "a connection cannot be saved. Set the encryption key."
            ),
        ) from exc
    return store.redacted_connection(row)
