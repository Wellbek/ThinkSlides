"""
Envelope encryption for provider credentials stored at rest.

API keys for the configurable chat / deep-search providers are user-supplied
secrets that live in Postgres. They are never stored in plaintext and never
leave the service in plaintext: the settings API returns only a short,
non-reversible hint (the last four characters).

Scheme (AES-256-GCM envelope encryption):

1. A random 256-bit **data encryption key** (DEK) is generated per secret.
2. The plaintext is sealed with AES-256-GCM under the DEK, with a fresh
   96-bit nonce and an *associated data* (AAD) context string.
3. The DEK is itself sealed with AES-256-GCM under the **key encryption key**
   (KEK), again with a fresh nonce and the same AAD context.
4. Only the wrapped DEK and the ciphertext are persisted. The KEK never
   touches the database.

Why a per-secret DEK rather than encrypting directly under the KEK?
It bounds how much data any single key/nonce pair ever protects (AES-GCM
degrades badly on nonce reuse), and it makes KEK rotation a cheap re-wrap of
a 32-byte blob rather than a decrypt/re-encrypt of every secret.

The AAD binds each ciphertext to the exact row and column it belongs to
(``organisation_ai_settings:<org_id>:<field>``). An attacker with write access
to the database therefore cannot copy another organisation's encrypted key
into their own row - authentication fails and decryption is refused.

Key material comes from the environment, never from the database:

- ``SETTINGS_ENCRYPTION_KEY`` - the active KEK, base64-encoded 32 bytes.
  Used for all new encryptions.
- ``SETTINGS_ENCRYPTION_KEYS_RETIRED`` - comma-separated, previously-active
  KEKs. Decrypt-only, so a key can be rotated without downtime: set the new
  key as active, move the old one here, re-save the settings, then drop it.

Each envelope records a short key id so decryption selects the right KEK
directly instead of trial-decrypting the whole key ring.

Generate a key with::

    python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Envelope format marker. Bump if the layout ever changes so old values stay
# readable - the version is the first field of every stored envelope.
ENVELOPE_VERSION = "ts1"

# AES-GCM standard nonce length. 96 bits is the only size AES-GCM is properly
# specified (and fastest) for.
NONCE_BYTES = 12

# AES-256 key length, for both the KEK and every DEK.
KEY_BYTES = 32

# Env var names holding key material.
ACTIVE_KEY_ENV = "SETTINGS_ENCRYPTION_KEY"
RETIRED_KEYS_ENV = "SETTINGS_ENCRYPTION_KEYS_RETIRED"


class EncryptionNotConfiguredError(RuntimeError):
    """Raised when no usable KEK is present in the environment."""


class DecryptionError(RuntimeError):
    """Raised when a stored envelope cannot be authenticated or decrypted."""


def _b64e(raw: bytes) -> str:
    """URL-safe base64 without padding, so envelopes never contain ``:`` or ``=``."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _parse_key(material: str) -> bytes:
    """Decode one base64 KEK and verify it is exactly 32 bytes.

    Passphrases are deliberately not accepted: a stretched low-entropy
    passphrase would be the weakest link in the whole scheme, and the failure
    would be silent. Operators must supply real random key material.
    """
    cleaned = material.strip()
    if not cleaned:
        raise ValueError("empty key material")
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except Exception:
        try:
            raw = _b64d(cleaned)
        except Exception as exc:
            raise ValueError(f"key is not valid base64: {exc}") from exc
    if len(raw) != KEY_BYTES:
        raise ValueError(
            f"key must decode to exactly {KEY_BYTES} bytes, got {len(raw)}. "
            "Generate one with: python -c \"import os,base64; "
            "print(base64.b64encode(os.urandom(32)).decode())\""
        )
    return raw


def _key_id(raw_key: bytes) -> str:
    """Short, non-secret identifier for a KEK.

    Domain-separated so the stored id can never be confused with a hash of the
    key used for any other purpose. Truncated to 16 hex chars - it only needs
    to distinguish the handful of keys in a rotation window.
    """
    digest = hashlib.sha256(b"thinkslides-settings-kek-id\x00" + raw_key).hexdigest()
    return digest[:16]


class SecretCipher:
    """Seals and opens individual secrets using a KEK key ring."""

    def __init__(self, active_key: bytes, retired_keys: list[bytes] | None = None) -> None:
        self._active_key = active_key
        self._active_key_id = _key_id(active_key)
        # Key id -> raw key, covering the active key and every retired key.
        self._ring: dict[str, bytes] = {self._active_key_id: active_key}
        for key in retired_keys or []:
            self._ring.setdefault(_key_id(key), key)

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def encrypt(self, plaintext: str, context: str) -> str:
        """Seal ``plaintext`` and return the serialized envelope.

        Args:
            plaintext: The secret to protect. Must be non-empty.
            context: AAD binding the ciphertext to its logical location, e.g.
                ``organisation_ai_settings:<org_id>:chat_api_key``. The exact
                same string must be supplied to :meth:`decrypt`.
        """
        if not plaintext:
            raise ValueError("refusing to encrypt an empty secret")

        aad = context.encode("utf-8")

        dek = os.urandom(KEY_BYTES)
        data_nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(data_nonce, plaintext.encode("utf-8"), aad)

        wrap_nonce = os.urandom(NONCE_BYTES)
        wrapped_dek = AESGCM(self._active_key).encrypt(wrap_nonce, dek, aad)

        return ":".join([
            ENVELOPE_VERSION,
            self._active_key_id,
            _b64e(wrap_nonce),
            _b64e(wrapped_dek),
            _b64e(data_nonce),
            _b64e(ciphertext),
        ])

    def decrypt(self, envelope: str, context: str) -> str:
        """Open an envelope produced by :meth:`encrypt`.

        Raises:
            DecryptionError: If the envelope is malformed, was sealed under a
                key that is no longer on the ring, or fails authentication
                (including when ``context`` does not match the sealing AAD).
        """
        parts = envelope.split(":")
        if len(parts) != 6 or parts[0] != ENVELOPE_VERSION:
            raise DecryptionError("stored secret is not a recognized envelope")

        _, key_id, wrap_nonce_b64, wrapped_dek_b64, data_nonce_b64, ciphertext_b64 = parts

        kek = self._ring.get(key_id)
        if kek is None:
            raise DecryptionError(
                f"secret was encrypted with key '{key_id}', which is not in the current key ring. "
                f"Add it to {RETIRED_KEYS_ENV} to decrypt, or re-enter the credential."
            )

        aad = context.encode("utf-8")
        try:
            dek = AESGCM(kek).decrypt(_b64d(wrap_nonce_b64), _b64d(wrapped_dek_b64), aad)
            plaintext = AESGCM(dek).decrypt(_b64d(data_nonce_b64), _b64d(ciphertext_b64), aad)
        except InvalidTag as exc:
            raise DecryptionError(
                "stored secret failed authentication - it was tampered with, "
                "or belongs to a different organisation or field"
            ) from exc
        except Exception as exc:
            raise DecryptionError(f"stored secret could not be decrypted: {exc}") from exc

        return plaintext.decode("utf-8")

    def needs_rewrap(self, envelope: str) -> bool:
        """Whether an envelope was sealed under a retired key and should be re-saved."""
        parts = envelope.split(":")
        if len(parts) != 6 or parts[0] != ENVELOPE_VERSION:
            return False
        return parts[1] != self._active_key_id


# ── Module-level key ring ──────────────────────────────────────────────

_cipher: SecretCipher | None = None
_cipher_loaded = False


def get_cipher() -> SecretCipher:
    """Return the process-wide cipher, building it from the environment once.

    Raises:
        EncryptionNotConfiguredError: If no valid active key is configured. The
            settings API turns this into a clear operator-facing error rather
            than silently storing credentials in the clear.
    """
    global _cipher, _cipher_loaded

    if _cipher_loaded and _cipher is not None:
        return _cipher

    active_material = os.environ.get(ACTIVE_KEY_ENV, "").strip()
    if not active_material:
        raise EncryptionNotConfiguredError(
            f"{ACTIVE_KEY_ENV} is not set. Provider credentials cannot be stored "
            "because they must be encrypted at rest. Generate a key with: "
            'python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"'
        )

    try:
        active_key = _parse_key(active_material)
    except ValueError as exc:
        raise EncryptionNotConfiguredError(f"{ACTIVE_KEY_ENV} is invalid: {exc}") from exc

    retired: list[bytes] = []
    for material in os.environ.get(RETIRED_KEYS_ENV, "").split(","):
        if not material.strip():
            continue
        try:
            retired.append(_parse_key(material))
        except ValueError as exc:
            # A bad retired key must not take the service down - it only costs
            # the ability to read secrets sealed under it.
            logger.warning("retired_encryption_key_invalid", error=str(exc))

    _cipher = SecretCipher(active_key, retired)
    _cipher_loaded = True
    logger.info(
        "secret_cipher_initialized",
        active_key_id=_cipher.active_key_id,
        retired_key_count=len(retired),
    )
    return _cipher


def reset_cipher_cache() -> None:
    """Drop the cached cipher so the next call re-reads the environment (tests)."""
    global _cipher, _cipher_loaded
    _cipher = None
    _cipher_loaded = False


def encryption_available() -> bool:
    """Whether credential storage is usable, without raising."""
    try:
        get_cipher()
        return True
    except EncryptionNotConfiguredError:
        return False


def redact_secret(plaintext: str) -> str:
    """Return the non-reversible display hint kept alongside a stored secret.

    Only the last four characters survive, and only when the secret is long
    enough that they do not meaningfully narrow it down. This is what the UI
    renders so a user can tell *which* key is configured without it ever being
    readable back out of the API.
    """
    if not plaintext:
        return ""
    if len(plaintext) <= 8:
        return "****"
    return f"****{plaintext[-4:]}"
