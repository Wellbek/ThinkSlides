"""Tests for the envelope encryption used to store provider credentials."""

import base64
import os

import pytest

from app.security.crypto import (
    ACTIVE_KEY_ENV,
    RETIRED_KEYS_ENV,
    DecryptionError,
    EncryptionNotConfiguredError,
    SecretCipher,
    get_cipher,
    redact_secret,
    reset_cipher_cache,
)


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _cipher(retired: list[str] | None = None) -> SecretCipher:
    return SecretCipher(
        base64.b64decode(_KEY_A),
        [base64.b64decode(k) for k in (retired or [])],
    )


_KEY_A = _key()
_KEY_B = _key()

CONTEXT = "organisation_ai_settings:11111111-1111-1111-1111-111111111111:chat_api_key"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts from a known key ring and leaves no cached cipher behind."""
    monkeypatch.delenv(ACTIVE_KEY_ENV, raising=False)
    monkeypatch.delenv(RETIRED_KEYS_ENV, raising=False)
    reset_cipher_cache()
    yield
    reset_cipher_cache()


def test_round_trip_returns_the_original_secret():
    """A sealed secret opens back to exactly the plaintext that went in."""
    cipher = _cipher()
    envelope = cipher.encrypt("sk-ant-super-secret", CONTEXT)

    assert cipher.decrypt(envelope, CONTEXT) == "sk-ant-super-secret"


def test_ciphertext_never_contains_the_plaintext():
    """The stored envelope leaks nothing of the secret it protects."""
    cipher = _cipher()
    envelope = cipher.encrypt("sk-ant-super-secret", CONTEXT)

    assert "super-secret" not in envelope
    assert envelope.startswith("ts1:")


def test_encrypting_twice_produces_different_envelopes():
    """Fresh data keys and nonces mean identical secrets are not linkable."""
    cipher = _cipher()

    assert cipher.encrypt("same-key", CONTEXT) != cipher.encrypt("same-key", CONTEXT)


def test_decrypting_with_a_different_context_is_refused():
    """AAD binding stops a ciphertext being replayed into another org or column."""
    cipher = _cipher()
    envelope = cipher.encrypt("sk-ant-super-secret", CONTEXT)

    other = "organisation_ai_settings:22222222-2222-2222-2222-222222222222:chat_api_key"
    with pytest.raises(DecryptionError):
        cipher.decrypt(envelope, other)


def test_tampered_ciphertext_is_refused():
    """AES-GCM authentication rejects any modification of the stored envelope."""
    cipher = _cipher()
    envelope = cipher.encrypt("sk-ant-super-secret", CONTEXT)
    parts = envelope.split(":")
    parts[5] = parts[5][:-2] + ("aa" if not parts[5].endswith("aa") else "bb")

    with pytest.raises(DecryptionError):
        cipher.decrypt(":".join(parts), CONTEXT)


def test_malformed_envelope_is_refused():
    """Anything that is not a recognized envelope fails loudly, not silently."""
    cipher = _cipher()

    with pytest.raises(DecryptionError):
        cipher.decrypt("not-an-envelope", CONTEXT)


def test_retired_key_still_decrypts_and_is_flagged_for_rewrap():
    """Rotation: secrets sealed under the old key stay readable and are re-wrappable."""
    old = SecretCipher(base64.b64decode(_KEY_B))
    envelope = old.encrypt("sk-old-key", CONTEXT)

    rotated = SecretCipher(base64.b64decode(_KEY_A), [base64.b64decode(_KEY_B)])

    assert rotated.decrypt(envelope, CONTEXT) == "sk-old-key"
    assert rotated.needs_rewrap(envelope) is True
    assert rotated.needs_rewrap(rotated.encrypt("sk-new-key", CONTEXT)) is False


def test_key_no_longer_on_the_ring_gives_an_actionable_error():
    """A dropped key produces a message telling the operator how to recover."""
    old = SecretCipher(base64.b64decode(_KEY_B))
    envelope = old.encrypt("sk-old-key", CONTEXT)

    with pytest.raises(DecryptionError) as exc:
        SecretCipher(base64.b64decode(_KEY_A)).decrypt(envelope, CONTEXT)

    assert RETIRED_KEYS_ENV in str(exc.value)


def test_empty_secret_is_rejected():
    """Encrypting nothing is a caller bug, not an empty envelope."""
    with pytest.raises(ValueError):
        _cipher().encrypt("", CONTEXT)


def test_get_cipher_without_a_key_refuses_rather_than_storing_plaintext():
    """Missing key material fails closed so credentials are never stored in the clear."""
    with pytest.raises(EncryptionNotConfiguredError):
        get_cipher()


def test_get_cipher_rejects_a_key_of_the_wrong_length(monkeypatch):
    """A short key would silently weaken AES-256; it is refused up front."""
    monkeypatch.setenv(ACTIVE_KEY_ENV, base64.b64encode(os.urandom(16)).decode())

    with pytest.raises(EncryptionNotConfiguredError):
        get_cipher()


def test_get_cipher_builds_a_ring_from_the_environment(monkeypatch):
    """The active key encrypts and every retired key still decrypts."""
    monkeypatch.setenv(ACTIVE_KEY_ENV, _KEY_A)
    monkeypatch.setenv(RETIRED_KEYS_ENV, f"{_KEY_B}, ")

    envelope_under_old = SecretCipher(base64.b64decode(_KEY_B)).encrypt("sk-old", CONTEXT)
    cipher = get_cipher()

    assert cipher.decrypt(envelope_under_old, CONTEXT) == "sk-old"
    assert cipher.decrypt(cipher.encrypt("sk-new", CONTEXT), CONTEXT) == "sk-new"


def test_invalid_retired_key_does_not_break_startup(monkeypatch):
    """A malformed retired key costs only the secrets sealed under it."""
    monkeypatch.setenv(ACTIVE_KEY_ENV, _KEY_A)
    monkeypatch.setenv(RETIRED_KEYS_ENV, "not-base64-at-all")

    cipher = get_cipher()

    assert cipher.decrypt(cipher.encrypt("sk-new", CONTEXT), CONTEXT) == "sk-new"


@pytest.mark.parametrize(
    "plaintext,expected",
    [
        ("", ""),
        ("short", "****"),
        ("sk-ant-api03-abcd", "****abcd"),
    ],
)
def test_redact_secret_reveals_at_most_the_last_four_characters(plaintext, expected):
    """The display hint identifies a key without narrowing down its value."""
    assert redact_secret(plaintext) == expected
