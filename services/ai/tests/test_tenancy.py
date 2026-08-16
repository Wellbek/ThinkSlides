"""
Which organisation a request belongs to, and what happens when it belongs to
one that is gone.

A JWT outlives its database. Reset the volume, delete the organisation, and
the identity in the token stays cryptographically valid - so nothing
downstream had any reason to doubt it. What that produced was a workspace
that read as "nothing configured", because a SELECT for an absent
organisation returns no rows, and then answered the setup it had just asked
for with a bare 500, because writing the settings row violated a foreign key
nobody had checked.

The reads and the writes have to agree, and they only can if the identity is
checked once, where it enters.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api import tenancy

pytestmark = pytest.mark.unconfigured_workspace

ORG_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Db:
    """Just enough AsyncSession to answer "does this organisation exist"."""

    def __init__(self, exists: bool):
        self._exists = exists
        self.queries = 0

    async def execute(self, _statement):
        self.queries += 1
        return _Result(ORG_ID if self._exists else None)


class _Request:
    def __init__(self, **headers):
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


class TestAnIdentityThatIsStillReal:
    @pytest.mark.asyncio
    async def test_the_orchestrator_header_is_used(self):
        db = _Db(exists=True)

        assert await tenancy.resolve_org_id(_Request(X_Org_Id=str(ORG_ID)), db) == ORG_ID

    @pytest.mark.asyncio
    async def test_a_bearer_token_is_used_when_there_is_no_header(self):
        from jose import jwt

        from app.config import settings

        token = jwt.encode(
            {"org_id": str(ORG_ID)}, settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        resolved = await tenancy.resolve_org_id(
            _Request(Authorization=f"Bearer {token}"), _Db(exists=True)
        )

        assert resolved == ORG_ID


class TestAnIdentityThatIsNot:
    @pytest.mark.asyncio
    async def test_a_workspace_that_no_longer_exists_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await tenancy.resolve_org_id(
                _Request(X_Org_Id=str(ORG_ID)), _Db(exists=False)
            )

        assert raised.value.status_code == 401
        assert "no longer exists" in raised.value.detail

    @pytest.mark.asyncio
    async def test_it_is_refused_the_same_way_whichever_way_it_arrived(self):
        """A read that answers "nothing configured" and a write that 500s are
        the same stale identity - they have to fail identically."""
        from jose import jwt

        from app.config import settings

        token = jwt.encode(
            {"org_id": str(ORG_ID)}, settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(HTTPException) as raised:
            await tenancy.resolve_org_id(
                _Request(Authorization=f"Bearer {token}"), _Db(exists=False)
            )

        assert raised.value.status_code == 401

    @pytest.mark.asyncio
    async def test_the_message_tells_the_user_what_to_do(self):
        """401 specifically: the web client clears the token and returns to
        the login page on one, which is exactly the recovery."""
        assert "Sign in again" in tenancy.STALE_ORGANISATION_DETAIL


class TestNoIdentityAtAll:
    @pytest.mark.asyncio
    async def test_it_falls_back_to_the_default_organisation(self, monkeypatch):
        """Direct calls in development and the background job worker."""
        default = uuid.uuid4()

        async def fake(db):
            return default

        monkeypatch.setattr(tenancy, "get_or_create_default_org_id", fake)

        assert await tenancy.resolve_org_id(_Request(), _Db(exists=False)) == default

    @pytest.mark.asyncio
    async def test_an_unparseable_id_is_treated_as_none_rather_than_refused(
            self, monkeypatch):
        async def fake(db):
            return ORG_ID

        monkeypatch.setattr(tenancy, "get_or_create_default_org_id", fake)

        assert await tenancy.resolve_org_id(
            _Request(X_Org_Id="not-a-uuid"), _Db(exists=False)
        ) == ORG_ID
