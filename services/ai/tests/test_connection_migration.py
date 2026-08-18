"""
The two migration sources and the model agree on ``organisation_connections``.

This project maintains the schema twice - Alembic for deployments, plain SQL in
``db/migrations/`` for Docker initialisation - so the two can drift, and when
they do the symptom is a column that exists in development and not in
production. Nothing else in the suite checks that, and there is no database
here to check it against, so this compares the three declarations by name.
"""

import pathlib
import re

from app.db.models import OrganisationConnection

REPO = pathlib.Path(__file__).resolve().parents[3]
ALEMBIC = REPO / "services/ai/alembic/versions/009_add_organisation_connections.py"
SQL = REPO / "db/migrations/012_create_organisation_connections.sql"

TABLE = "organisation_connections"


def _model_columns() -> set[str]:
    return {c.name for c in OrganisationConnection.__table__.columns}


def _alembic_columns() -> set[str]:
    text = ALEMBIC.read_text()
    return set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', text))


def _sql_columns() -> set[str]:
    """Column names from the CREATE TABLE body, ignoring constraints."""
    text = SQL.read_text()
    body = text.split(f"CREATE TABLE IF NOT EXISTS {TABLE} (", 1)[1]
    body = body.split(");", 1)[0]
    names = set()
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        first = stripped.split()[0]
        if first.upper() in {"CONSTRAINT", "UNIQUE", "PRIMARY", "FOREIGN", "CHECK"}:
            continue
        names.add(first)
    return names


class TestTheThreeDeclarationsAgree:
    def test_both_migrations_exist(self):
        assert ALEMBIC.is_file()
        assert SQL.is_file()

    def test_alembic_declares_exactly_the_model_columns(self):
        assert _alembic_columns() == _model_columns()

    def test_the_sql_declares_exactly_the_model_columns(self):
        assert _sql_columns() == _model_columns()

    def test_the_model_carries_the_columns_this_step_is_for(self):
        """Stated explicitly so a rename has to be deliberate."""
        assert {
            "id", "organisation_id", "connection_id", "slug", "label",
            "enabled", "config", "secrets_encrypted", "secret_hints",
            "created_at", "updated_at",
        } == _model_columns()


class TestTheMigrationChainIsIntact:
    def test_it_revises_the_previous_head(self):
        """
        A migration whose down_revision points at a revision that is no longer
        the head is a branch, and Alembic refuses to upgrade through one.
        """
        text = ALEMBIC.read_text()
        revision = re.search(r'^revision: str = "(\d+)"', text, re.M).group(1)
        down = re.search(r'^down_revision: Union\[str, None\] = "(\d+)"',
                         text, re.M).group(1)

        versions = ALEMBIC.parent
        revisions = {
            re.search(r'^revision: str = "(\d+)"', p.read_text(), re.M).group(1)
            for p in versions.glob("[0-9]*.py")
        }
        assert down in revisions, f"{down} is not a revision"
        # The head is the highest revision that is not this one. A migration
        # revising anything else is a branch, and Alembic refuses to upgrade
        # through one.
        assert down == max(revisions - {revision})


class TestBothMigrationsCarryTheConstraintTheCodeRelieson:
    """
    ``(organisation_id, connection_id, slug)`` unique is not decoration: the
    slug is the ``<server>`` in ``mcp__<server>__<tool>``, so two connections
    sharing one would register two different servers' tools under the same
    names.
    """

    NAME = "uq_organisation_connections_slug"

    def test_alembic_declares_it(self):
        assert self.NAME in ALEMBIC.read_text()

    def test_the_sql_declares_it(self):
        assert self.NAME in SQL.read_text()

    def test_the_model_declares_it(self):
        names = {c.name for c in OrganisationConnection.__table__.constraints}

        assert self.NAME in names

    def test_it_covers_the_same_three_columns_everywhere(self):
        constraint = next(
            c for c in OrganisationConnection.__table__.constraints
            if c.name == self.NAME
        )

        assert {c.name for c in constraint.columns} == {
            "organisation_id", "connection_id", "slug",
        }
