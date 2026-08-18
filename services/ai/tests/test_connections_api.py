"""
The connections API, exercised through the routes rather than around them.

This is the acceptance criterion for the storage step stated literally: a
connection saved through the API round-trips, its credential is unreadable, and
the read payload is redacted. Going through the routes is what catches the
things a direct call to the store cannot - a field name the request model spells
differently, a store error that becomes a 500 instead of a 400, a router that
was never registered.

``get_db`` and ``resolve_org_id`` are overridden because the suite has no
database. Everything between the request and the store is real.
"""

import base64
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.tenancy import resolve_org_id
from app.connections import store
from app.db.session import get_db
from app.main import app
from app.security.crypto import ACTIVE_KEY_ENV, reset_cipher_cache

pytestmark = pytest.mark.unconfigured_workspace

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
BASE = "/api/v1/connections"

WIKI = {
    "connectionId": "mcp_http",
    "label": "Company wiki",
    "config": {"url": "https://mcp.example.com/mcp"},
    "secrets": {"token": "tok-abcd-1234"},
}


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv(ACTIVE_KEY_ENV, base64.b64encode(os.urandom(32)).decode())
    reset_cipher_cache()
    yield
    reset_cipher_cache()


class _Session:
    """The same stand-in the store tests use, behind the dependency."""

    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        pass

    async def refresh(self, row):
        return row

    async def delete(self, row):
        self.rows.remove(row)


@pytest.fixture
def client(monkeypatch):
    session = _Session()

    async def load_rows(_db, org_id):
        return [r for r in session.rows if r.organisation_id == org_id]

    async def load_row(_db, org_id, row_id):
        return next(
            (r for r in session.rows
             if r.organisation_id == org_id and r.id == row_id),
            None,
        )

    monkeypatch.setattr(store, "load_rows", load_rows)
    monkeypatch.setattr(store, "load_row", load_row)

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[resolve_org_id] = lambda: ORG_ID
    # Constructed without entering it as a context manager, so the app's
    # lifespan does not run: startup calls init_db, and there is no database
    # here to connect to. Routing, validation and the routes themselves are
    # unaffected - only the startup hooks are skipped.
    test_client = TestClient(app)
    test_client.rows = session.rows
    yield test_client
    app.dependency_overrides.clear()


class TestTheRoundTrip:
    def test_a_saved_connection_reads_back_redacted(self, client):
        created = client.post(BASE, json=WIKI)
        assert created.status_code == 200, created.text

        listed = client.get(BASE)
        assert listed.status_code == 200
        connections = listed.json()["connections"]

        assert len(connections) == 1
        one = connections[0]
        assert one["label"] == "Company wiki"
        assert one["kind"] == "mcp"
        assert one["config"]["url"] == "https://mcp.example.com/mcp"
        assert one["secrets"]["token"]["hasValue"] is True
        assert one["secrets"]["token"]["hint"].endswith("1234")

    def test_no_response_anywhere_carries_the_credential(self, client):
        created = client.post(BASE, json=WIKI)
        listed = client.get(BASE)

        assert "tok-abcd-1234" not in created.text
        assert "tok-abcd-1234" not in listed.text

    def test_the_credential_is_unreadable_in_the_stored_row(self, client):
        client.post(BASE, json=WIKI)

        row = client.rows[0]

        assert "tok-abcd-1234" not in str(row.secrets_encrypted)
        assert "tok-abcd-1234" not in str(row.config)

    def test_an_update_that_omits_the_credential_keeps_it(self, client):
        row_id = client.post(BASE, json=WIKI).json()["id"]

        updated = client.put(f"{BASE}/{row_id}", json={
            "connectionId": "mcp_http",
            "label": "Internal wiki",
            "config": {"url": "https://mcp.example.com/v2"},
        })

        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["label"] == "Internal wiki"
        assert body["config"]["url"] == "https://mcp.example.com/v2"
        assert body["secrets"]["token"]["hasValue"] is True

    def test_disabling_a_connection_round_trips(self, client):
        row_id = client.post(BASE, json=WIKI).json()["id"]

        client.put(f"{BASE}/{row_id}", json={
            "connectionId": "mcp_http", "label": "Company wiki", "enabled": False,
        })

        assert client.get(BASE).json()["connections"][0]["enabled"] is False

    def test_deleting_removes_it_from_the_list(self, client):
        row_id = client.post(BASE, json=WIKI).json()["id"]

        assert client.delete(f"{BASE}/{row_id}").status_code == 200
        assert client.get(BASE).json()["connections"] == []

    def test_deleting_something_that_is_not_there_is_a_404(self, client):
        assert client.delete(f"{BASE}/{uuid.uuid4()}").status_code == 404

    def test_an_organisation_with_nothing_saved_gets_an_empty_list(self, client):
        """Not an error, and not something out of the environment."""
        assert client.get(BASE).json() == {"connections": []}


class TestARefusalIsA400WithTheStoresOwnWords:
    """
    The store raises with a message written for the user, which is the whole
    reason it raises rather than returning a bool. A 500 here would throw that
    away and show a stack trace instead.
    """

    def test_a_private_address_is_refused(self, client):
        response = client.post(BASE, json={**WIKI, "config": {
            "url": "https://10.0.0.5/mcp",
        }})

        assert response.status_code == 400
        assert "network" in response.json()["detail"].lower()

    def test_plain_http_is_refused(self, client):
        response = client.post(BASE, json={**WIKI, "config": {
            "url": "http://mcp.example.com/mcp",
        }})

        assert response.status_code == 400
        assert "https" in response.json()["detail"].lower()

    def test_an_unknown_connection_id_is_refused(self, client):
        response = client.post(BASE, json={**WIKI, "connectionId": "mcp_telepathy"})

        assert response.status_code == 400
        assert "mcp_telepathy" in response.json()["detail"]

    def test_a_missing_required_credential_is_refused(self, client):
        response = client.post(BASE, json={
            "connectionId": "image_openai", "label": "Images", "secrets": {},
        })

        assert response.status_code == 400
        assert "required" in response.json()["detail"].lower()

    def test_updating_a_connection_that_does_not_exist_is_refused(self, client):
        response = client.put(f"{BASE}/{uuid.uuid4()}", json=WIKI)

        assert response.status_code == 400


class TestTheCatalogueIsServedAlongsideTheProviders:
    def test_one_fetch_returns_both(self, client):
        response = client.get("/api/v1/settings/catalog")

        assert response.status_code == 200
        body = response.json()
        assert "providers" in body
        assert "connections" in body
        assert "mcpPresets" in body

    def test_a_preset_can_be_saved_by_name_without_sending_a_url(self, client):
        """The endpoint comes from the catalogue, so it cannot be mistyped."""
        response = client.post(BASE, json={
            "connectionId": "mcp_http",
            "label": "GitHub",
            "config": {"preset": "github"},
            "secrets": {"token": "ghp-0001"},
        })

        assert response.status_code == 200, response.text
        assert response.json()["config"]["url"].startswith("https://")
