"""
Who owns a chat exchange's rows.

Two services used to persist the same exchange independently. The orchestrator
inserts a user message and an empty assistant placeholder for every incoming
message, including the one answering a question; the AI service's
``send_message``/``answer_question`` then inserted their own pair; and the
question event was stored as a further assistant row on top.

A session with two clarifying questions accumulated several redundant rows.
They are invisible in the live UI, which works from the optimistic/SSE stream,
but they are what a page reload reads back - and what the orchestrator's
``saveAssistantMessage`` searches when it looks for "the newest assistant row
with empty content". With several placeholders in flight it can fill the wrong
one.

The orchestrator owns the rows; the AI service adopts them when told which they
are, and writes its own only when it is driven directly.
"""

import uuid

import pytest

from app.api.generate import _exchange_rows


class _FakeSession:
    """Records what would have been written, without a database."""

    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        # Mimic the identity a flush would assign.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return obj

    @property
    def roles(self):
        return [obj.role for obj in self.added]


async def test_supplied_ids_are_adopted_without_writing_rows():
    db = _FakeSession()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    assistant_id = uuid.uuid4()

    got_user, got_assistant = await _exchange_rows(
        db,
        session_id,
        content="Build me a deck",
        attachments=[],
        user_message_id=str(user_id),
        assistant_message_id=str(assistant_id),
    )

    assert got_user == user_id
    assert got_assistant == assistant_id
    assert db.added == [], f"wrote {db.roles} on top of the orchestrator's rows"
    assert db.commits == 0


async def test_rows_are_written_when_no_ids_are_supplied():
    """Driven directly, the AI service still owns the exchange."""
    db = _FakeSession()

    user_id, assistant_id = await _exchange_rows(
        db,
        uuid.uuid4(),
        content="Build me a deck",
        attachments=[],
        user_message_id=None,
        assistant_message_id=None,
    )

    assert db.roles == ["user", "assistant"]
    assert db.added[0].content == "Build me a deck"
    assert db.added[1].content == ""
    assert user_id == db.added[0].id
    assert assistant_id == db.added[1].id


@pytest.mark.parametrize(
    "user_id,assistant_id",
    [
        (str(uuid.uuid4()), None),
        (None, str(uuid.uuid4())),
        ("", ""),
    ],
)
async def test_a_half_supplied_pair_falls_back_to_writing_rows(user_id, assistant_id):
    """Adopting one row and inventing the other would be worse than either."""
    db = _FakeSession()

    await _exchange_rows(
        db,
        uuid.uuid4(),
        content="hello",
        attachments=[],
        user_message_id=user_id,
        assistant_message_id=assistant_id,
    )

    assert db.roles == ["user", "assistant"]


async def test_malformed_ids_do_not_take_down_the_run():
    """A caller sending nonsense gets rows written rather than a 500."""
    db = _FakeSession()

    user_id, assistant_id = await _exchange_rows(
        db,
        uuid.uuid4(),
        content="hello",
        attachments=[],
        user_message_id="not-a-uuid",
        assistant_message_id="also-not-a-uuid",
    )

    assert db.roles == ["user", "assistant"]
    assert isinstance(user_id, uuid.UUID)
    assert isinstance(assistant_id, uuid.UUID)


async def test_attachments_default_to_empty_rather_than_null():
    db = _FakeSession()

    await _exchange_rows(
        db,
        uuid.uuid4(),
        content="hello",
        attachments=None,
        user_message_id=None,
        assistant_message_id=None,
    )

    assert db.added[0].attachments == []
