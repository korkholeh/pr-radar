"""Encryption of GitHub tokens at rest (ADR 0004). Model-free so migrations and management
commands can import it without pulling in apps/connections/models.py."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _derive_fernet_key(raw_key: str) -> bytes:
    """FIELD_ENCRYPTION_KEYS entries are arbitrary secret strings, not necessarily
    already base64-encoded 32-byte Fernet keys, so derive a valid key deterministically."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet() -> MultiFernet:
    raw_keys = settings.FIELD_ENCRYPTION_KEYS
    if not raw_keys:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS is empty; set at least one key before storing a token."
        )
    return MultiFernet([Fernet(_derive_fernet_key(key)) for key in raw_keys])


def encrypt_token(plaintext: str) -> bytes:
    return get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes) -> str:
    return get_fernet().decrypt(bytes(ciphertext)).decode("utf-8")


def token_last4(plaintext: str) -> str:
    return plaintext[-4:] if len(plaintext) >= 4 else plaintext


def is_encrypted_with_primary_key(ciphertext: bytes) -> bool:
    """True when ciphertext already decrypts under key #0 alone (nothing to rotate)."""
    raw_keys = settings.FIELD_ENCRYPTION_KEYS
    primary = Fernet(_derive_fernet_key(raw_keys[0]))
    try:
        primary.decrypt(bytes(ciphertext))
    except InvalidToken:
        return False
    return True
