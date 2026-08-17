"""
Storage, redaction and resolution of per-organisation connections.

The suite has no database, so this file stands in a small in-memory session for
one and patches the two loader functions to read it. That is a deliberate line:
what is under test is this module's decisions - what gets encrypted, what gets
handed back, what "not configured" resolves to - and not SQLAlchemy's ability to
write a row. The migration is checked separately, by shape, in
``test_connection_migration.py``.
"""

import base64
import os
import uuid

import pytest

from app.connections import store
from app.connections.catalog import CONNECTIONS_BY_ID, MCP_PRESETS_BY_ID
from app.db.models import OrganisationConnection
from app.security.crypto import ACTIVE_KEY_ENV, reset_cipher_cache

pytestmark = pytest.mark.unconfigured_workspace

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv(ACTIVE_KEY_ENV, base64.b64encode(os.urandom(32)).decode())
    reset_cipher_cache()
    yield
    reset_cipher_cache()


class FakeSession:
    """Enough of AsyncSession for this module: add, commit, refresh, delete."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.commits = 0

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        return row

    async def delete(self, row):
        self.rows.remove(row)


@pytest.fixture
def db(monkeypatch):
    session = FakeSession()

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
    return session


async def _save_mcp(db, **overrides):
    kwargs = {
        "connection_id": "mcp_http",
        "label": "Company wiki",
        "config": {"url": "https://mcp.example.com/mcp"},
        "secrets": {"token": "tok-abcd-1234"},
    }
    kwargs.update(overrides)
    return await store.save_connection(db, ORG_ID, **kwargs)


# ── The round trip ─────────────────────────────────────────────────────


class TestASavedConnectionRoundTrips:
    async def test_it_comes_back_with_its_config_and_label(self, db):
        row = await _save_mcp(db)

        assert row.connection_id == "mcp_http"
        assert row.label == "Company wiki"
        assert row.config["url"] == "https://mcp.example.com/mcp"
        assert row.enabled is True

    async def test_the_credential_is_unreadable_in_the_row(self, db):
        row = await _save_mcp(db)

        stored = row.secrets_encrypted["token"]
        assert "tok-abcd-1234" not in stored
        assert "tok-abcd-1234" not in str(row.config)
        assert "tok-abcd-1234" not in str(row.secret_hints)

    async def test_it_decrypts_back_to_what_was_saved(self, db):
        row = await _save_mcp(db)

        assert store.decrypt_secrets(row) == {"token": "tok-abcd-1234"}

    async def test_the_hint_is_the_last_four_and_nothing_more(self, db):
        row = await _save_mcp(db)

        assert row.secret_hints["token"].endswith("1234")
        assert "abcd" not in row.secret_hints["token"]

    async def test_a_slug_is_derived_from_the_label(self, db):
        row = await _save_mcp(db)

        assert row.slug == "company_wiki"

    async def test_a_second_connection_with_the_same_name_gets_its_own_slug(self, db):
        first = await _save_mcp(db)
        second = await _save_mcp(db)

        assert first.slug != second.slug
        assert second.slug.startswith("company_wiki")


class TestTheAadBinding:
    """
    Wider than the settings table's, and this is why. An organisation can hold
    several MCP servers, so a binding that stopped at the field name would let
    one server's token be copied into another's row and still open.
    """

    def test_the_context_names_the_table_the_org_the_row_and_the_field(self):
        context = store.encryption_context(ORG_ID, "row-1", "token")

        assert "organisation_connections" in context
        assert str(ORG_ID) in context
        assert "row-1" in context
        assert "token" in context

    async def test_a_ciphertext_moved_to_another_row_does_not_open(self, db):
        source = await _save_mcp(db, label="Wiki A")
        target = await _save_mcp(db, label="Wiki B")

        target.secrets_encrypted = dict(source.secrets_encrypted)

        assert store.decrypt_secrets(target) == {}

    async def test_a_ciphertext_moved_to_another_field_does_not_open(self, db):
        row = await store.save_connection(
            db, ORG_ID,
            connection_id="figure_figurelabs",
            label="Figures",
            secrets={"api_key": "fig-secret-5678"},
        )

        row.secrets_encrypted = {"token": row.secrets_encrypted["api_key"]}

        assert store.decrypt_secrets(row) == {}

    async def test_a_ciphertext_moved_to_another_organisation_does_not_open(self, db):
        row = await _save_mcp(db)
        moved = OrganisationConnection(
            id=row.id,
            organisation_id=OTHER_ORG,
            connection_id=row.connection_id,
            slug=row.slug,
            label=row.label,
            enabled=True,
            config=dict(row.config),
            secrets_encrypted=dict(row.secrets_encrypted),
            secret_hints=dict(row.secret_hints),
        )

        assert store.decrypt_secrets(moved) == {}

    async def test_an_unreadable_credential_degrades_to_not_configured(self, db):
        """
        Rather than raising. A rotated-away key must leave the app usable and
        the operator with a log line, not 500 every request.

        Stated on a connection whose credential is *required*, because that is
        where the difference shows: an optional one that will not open is a
        connection without it, which is a legitimate state.
        """
        row = await store.save_connection(
            db, ORG_ID, connection_id="image_openai", label="Images",
            secrets={"api_key": "sk-secret-0001"},
        )
        row.secrets_encrypted = {"api_key": "not-an-envelope"}

        assert store.decrypt_secrets(row) == {}
        assert store.resolve_row(row) is None

    async def test_an_unopenable_optional_credential_is_a_connection_without_one(
        self, db
    ):
        """An MCP token is optional - open servers exist - so this still resolves."""
        row = await _save_mcp(db)
        row.secrets_encrypted = {"token": "not-an-envelope"}

        resolved = store.resolve_row(row)

        assert resolved is not None
        assert resolved.secrets == {}


class TestTheTriStateSecret:
    async def test_omitting_a_secret_keeps_the_stored_one(self, db):
        row = await _save_mcp(db)
        before = row.secrets_encrypted["token"]

        await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="Company wiki",
            config={"url": "https://mcp.example.com/other"}, secrets={},
            row_id=row.id,
        )

        assert row.secrets_encrypted["token"] == before
        assert row.config["url"] == "https://mcp.example.com/other"

    async def test_an_empty_string_clears_it(self, db):
        row = await _save_mcp(db)

        await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="Company wiki",
            config={}, secrets={"token": ""}, row_id=row.id,
        )

        assert "token" not in row.secrets_encrypted
        assert "token" not in row.secret_hints

    async def test_a_new_value_replaces_it(self, db):
        row = await _save_mcp(db)

        await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="Company wiki",
            config={}, secrets={"token": "tok-wxyz-9999"}, row_id=row.id,
        )

        assert store.decrypt_secrets(row) == {"token": "tok-wxyz-9999"}

    async def test_a_required_secret_already_stored_satisfies_an_update(self, db):
        """
        The UI cannot re-send a credential it can never read, so an update that
        omits it must not be refused as incomplete.
        """
        row = await store.save_connection(
            db, ORG_ID, connection_id="image_openai", label="Images",
            secrets={"api_key": "sk-first-0001"},
        )

        await store.save_connection(
            db, ORG_ID, connection_id="image_openai", label="Renamed",
            secrets={}, row_id=row.id,
        )

        assert row.label == "Renamed"
        assert store.decrypt_secrets(row) == {"api_key": "sk-first-0001"}


class TestWhatTheStoreRefuses:
    async def test_an_unknown_connection_id(self, db):
        with pytest.raises(store.ConnectionConfigError):
            await store.save_connection(
                db, ORG_ID, connection_id="mcp_telepathy", label="x",
            )

    async def test_an_mcp_url_the_catalogue_check_rejects(self, db):
        with pytest.raises(store.ConnectionConfigError) as exc:
            await _save_mcp(db, config={"url": "http://10.0.0.5/mcp"})

        assert "https" in str(exc.value).lower()

    async def test_an_mcp_connection_with_no_url_at_all(self, db):
        with pytest.raises(store.ConnectionConfigError):
            await _save_mcp(db, config={})

    async def test_a_missing_required_credential(self, db):
        with pytest.raises(store.ConnectionConfigError) as exc:
            await store.save_connection(
                db, ORG_ID, connection_id="image_openai", label="Images",
                secrets={},
            )

        assert "required" in str(exc.value).lower()

    async def test_an_update_to_a_row_in_another_organisation_is_not_found(self, db):
        row = await _save_mcp(db)
        row.organisation_id = OTHER_ORG

        with pytest.raises(store.ConnectionConfigError):
            await store.save_connection(
                db, ORG_ID, connection_id="mcp_http", label="x", row_id=row.id,
            )

    async def test_a_secret_arriving_in_config_is_dropped_not_stored(self, db):
        """
        config is stored in the clear and handed back by the API, so the
        allowlist is what stops a token being written to a readable column.
        """
        row = await _save_mcp(db, config={
            "url": "https://mcp.example.com/mcp",
            "token": "tok-should-not-land-here",
        })

        assert "token" not in row.config
        assert "should-not-land-here" not in str(row.config)


class TestThePresets:
    async def test_a_preset_fills_the_endpoint_from_the_catalogue(self, db):
        row = await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="GitHub",
            config={"preset": "github"}, secrets={"token": "ghp-0001"},
        )

        assert row.config["url"] == MCP_PRESETS_BY_ID["github"].url

    async def test_a_url_sent_alongside_a_preset_does_not_win(self, db):
        """
        Otherwise a preset is a way to have a vetted-looking connection point
        anywhere.
        """
        row = await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="GitHub",
            config={"preset": "github", "url": "https://evil.example.com/mcp"},
            secrets={"token": "ghp-0001"},
        )

        assert row.config["url"] == MCP_PRESETS_BY_ID["github"].url


class TestRedaction:
    async def test_the_payload_carries_a_hint_and_a_boolean_never_the_secret(self, db):
        row = await _save_mcp(db)

        payload = store.redacted_connection(row)

        assert payload["secrets"]["token"]["hasValue"] is True
        assert payload["secrets"]["token"]["hint"].endswith("1234")
        assert "tok-abcd-1234" not in str(payload)

    async def test_config_is_readable_because_it_has_to_be_editable(self, db):
        row = await _save_mcp(db)

        payload = store.redacted_connection(row)

        assert payload["config"]["url"] == "https://mcp.example.com/mcp"

    async def test_an_unset_secret_reads_as_absent_rather_than_missing(self, db):
        row = await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="Open server",
            config={"url": "https://mcp.example.com/mcp"}, secrets={},
        )

        payload = store.redacted_connection(row)

        assert payload["secrets"]["token"] == {"hasValue": False, "hint": None}

    def test_a_row_whose_catalogue_entry_has_gone_is_still_listed(self):
        """A row nobody can see is a row nobody can delete."""
        row = OrganisationConnection(
            id=uuid.uuid4(), organisation_id=ORG_ID,
            connection_id="mcp_retired", slug="old", label="Old thing",
            enabled=True, config={}, secrets_encrypted={}, secret_hints={},
        )

        payload = store.redacted_connection(row)

        assert payload["known"] is False
        assert payload["label"] == "Old thing"

    async def test_the_list_payload_covers_every_row(self, db):
        await _save_mcp(db, label="A")
        await _save_mcp(db, label="B")

        payload = store.redacted_payload(db.rows)

        assert [c["label"] for c in payload["connections"]] == ["A", "B"]


class TestResolution:
    async def test_an_organisation_with_nothing_saved_resolves_to_nothing(self, db):
        """
        The decision worth repeating from ai_settings: there is no environment
        fallback, so "not configured" is distinguishable from "configured".
        """
        assert await store.resolve_enabled(db, ORG_ID) == []

    async def test_an_environment_variable_does_not_stand_in(self, db, monkeypatch):
        monkeypatch.setenv("MCP_SERVER_URL", "https://mcp.from-the-env.com/mcp")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-env")

        assert await store.resolve_enabled(db, ORG_ID) == []

    async def test_an_enabled_connection_resolves_with_its_secrets_open(self, db):
        await _save_mcp(db)

        resolved = await store.resolve_enabled(db, ORG_ID)

        assert len(resolved) == 1
        assert resolved[0].slug == "company_wiki"
        assert resolved[0].secrets == {"token": "tok-abcd-1234"}
        assert resolved[0].kind == "mcp"

    async def test_a_disabled_connection_does_not_resolve(self, db):
        row = await _save_mcp(db)
        row.enabled = False

        assert await store.resolve_enabled(db, ORG_ID) == []

    async def test_resolution_can_be_narrowed_to_one_kind(self, db):
        await _save_mcp(db)
        await store.save_connection(
            db, ORG_ID, connection_id="image_openai", label="Images",
            secrets={"api_key": "sk-0001"},
        )

        assert len(await store.resolve_enabled(db, ORG_ID, kind="mcp")) == 1
        assert len(await store.resolve_enabled(db, ORG_ID, kind="image")) == 1
        assert len(await store.resolve_enabled(db, ORG_ID, kind="figure")) == 0

    async def test_another_organisations_connection_is_not_resolved(self, db):
        row = await _save_mcp(db)
        row.organisation_id = OTHER_ORG

        assert await store.resolve_enabled(db, ORG_ID) == []

    async def test_a_resolved_connection_can_find_its_own_spec(self, db):
        await _save_mcp(db)

        resolved = (await store.resolve_enabled(db, ORG_ID))[0]

        assert resolved.spec is CONNECTIONS_BY_ID["mcp_http"]


class TestDeletion:
    async def test_it_removes_the_row(self, db):
        row = await _save_mcp(db)

        assert await store.delete_connection(db, ORG_ID, row.id) is True
        assert db.rows == []

    async def test_deleting_something_that_is_not_there_says_so(self, db):
        assert await store.delete_connection(db, ORG_ID, uuid.uuid4()) is False

    async def test_it_cannot_reach_another_organisations_row(self, db):
        row = await _save_mcp(db)
        row.organisation_id = OTHER_ORG

        assert await store.delete_connection(db, ORG_ID, row.id) is False
        assert db.rows == [row]


class TestSlugs:
    @pytest.mark.parametrize("label,expected", [
        ("Company wiki", "company_wiki"),
        ("GitHub", "github"),
        ("  Spaces  ", "spaces"),
        ("Dots.and-dashes", "dots_and_dashes"),
        ("!!!", ""),
        ("Ünïcödé", "unicode"),
        ("Bürgerbüro", "burgerburo"),
        ("公司维基", ""),
        ("a" * 100, "a" * 64),
    ])
    def test_a_label_reduces_to_something_safe_in_a_tool_name(self, label, expected):
        assert store.slugify(label) == expected

    def test_a_slug_never_starts_or_ends_with_an_underscore(self):
        for label in ("_leading", "trailing_", "!!edges!!"):
            slug = store.slugify(label)
            assert not slug.startswith("_")
            assert not slug.endswith("_")

    async def test_an_unslugifiable_label_still_gets_a_usable_slug(self, db):
        """
        The slug becomes part of a tool name the model is asked to call, so an
        empty one is not an option.
        """
        row = await _save_mcp(db, label="!!!")

        assert row.slug
        assert store.slugify(row.slug) == row.slug

    async def test_renaming_a_connection_leaves_its_slug_alone(self, db):
        """A tool name that moves breaks the transcript of every past run."""
        row = await _save_mcp(db)
        original = row.slug

        await store.save_connection(
            db, ORG_ID, connection_id="mcp_http", label="Something else",
            row_id=row.id,
        )

        assert row.slug == original
        assert row.label == "Something else"


class TestJsonbIsReassignedNotMutated:
    """
    SQLAlchemy does not track in-place changes to a JSONB dict, so mutating one
    commits nothing - a silent save failure, which is the worst kind.
    """

    async def test_writing_a_secret_replaces_the_dict_object(self, db):
        row = await _save_mcp(db)
        before = row.secrets_encrypted

        store._write_secrets(row, {"token": "tok-new-0002"})

        assert row.secrets_encrypted is not before
