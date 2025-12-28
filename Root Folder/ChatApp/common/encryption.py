"""
Encryption utilities for ChatApp.

Provides:
- generate_fernet_key() -> bytes
- encrypt_message(key: bytes, plaintext: bytes) -> bytes
- decrypt_message(key: bytes, ciphertext: bytes) -> bytes

All functions:
- Expect bytes
- Return bytes
- Are JSON-safe via URL-safe base64
- Raise EncryptionError on any failure
"""

from __future__ import annotations

import base64
from cryptography.fernet import Fernet, InvalidToken

from .exceptions import EncryptionError


# ----------------------------------------------------------
# Generate a Fernet-compatible key
# ----------------------------------------------------------
def generate_fernet_key() -> bytes:
    """
    Generate a new Fernet key (urlsafe-base64-encoded 32 bytes).

    Returns:
        key (bytes): safe to store, transmit, or pass directly to Fernet()
    """
    try:
        return Fernet.generate_key()
    except Exception as exc:
        raise EncryptionError(f"Failed to generate Fernet key: {exc}") from exc


# ----------------------------------------------------------
# Encryption
# ----------------------------------------------------------
def encrypt_message(key: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt bytes using Fernet.

    Args:
        key (bytes): urlsafe-base64-encoded 32-byte key
        plaintext (bytes): raw bytes to encrypt

    Returns:
        ciphertext (bytes): encrypted bytes suitable for JSON transport
                            (already URL-safe encoded by Fernet)

    Raises:
        EncryptionError
    """
    try:
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes")

        f = Fernet(key)
        token = f.encrypt(bytes(plaintext))  # always bytes
        return token
    except Exception as exc:
        raise EncryptionError(f"Encryption failed: {exc}") from exc


# ----------------------------------------------------------
# Decryption
# ----------------------------------------------------------
def decrypt_message(key: bytes, ciphertext: bytes) -> bytes:
    """
    Decrypt bytes using Fernet.

    Args:
        key (bytes): same key used for encryption
        ciphertext (bytes): raw encrypted bytes

    Returns:
        plaintext (bytes)

    Raises:
        EncryptionError
    """
    try:
        if not isinstance(ciphertext, (bytes, bytearray)):
            raise TypeError("ciphertext must be bytes")

        f = Fernet(key)
        plaintext = f.decrypt(bytes(ciphertext))
        return plaintext
    except InvalidToken:
        raise EncryptionError("Invalid token or corrupted ciphertext.")
    except Exception as exc:
        raise EncryptionError(f"Decryption failed: {exc}") from exc


# ----------------------------------------------------------
# JSON SAFE HELPERS
# ----------------------------------------------------------
def encode_for_json(data: bytes) -> str:
    """
    Convert raw bytes to a JSON-safe urlsafe-base64 string.
    """
    try:
        return base64.urlsafe_b64encode(data).decode("ascii")
    except Exception as exc:
        raise EncryptionError(f"Failed to encode for JSON: {exc}") from exc


def decode_from_json(data_b64: str) -> bytes:
    """
    Convert urlsafe-base64 string back to raw bytes.
    """
    try:
        if isinstance(data_b64, str):
            data_b64 = data_b64.encode("ascii")

        return base64.urlsafe_b64decode(data_b64)
    except Exception as exc:
        raise EncryptionError(f"Failed to decode JSON-safe base64: {exc}") from exc
