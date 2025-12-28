"""
Key exchange helpers for ChatApp.

This module implements X25519-based key exchange and derives a symmetric
key usable with Fernet (i.e. a URL-safe base64-encoded 32-byte key).

Functions:
- generate_keypair() -> (private_key, public_key_bytes)
- serialize_public_key(pub: X25519PublicKey or bytes) -> str (urlsafe-base64)
- load_public_key(b64: str or bytes) -> X25519PublicKey
- derive_shared_key(priv: X25519PrivateKey, peer_public_b64: str|bytes) -> bytes
    -> returns a Fernet-compatible key (urlsafe-base64-encoded 32 bytes)

Errors:
All failures raise KeyExchangeError.
"""

from __future__ import annotations

import base64
from typing import Tuple, Union

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

from .exceptions import KeyExchangeError


# HKDF parameters
_HKDF_HASH = hashes.SHA256()
_HKDF_LENGTH = 32  # 32 bytes (Fernet expects 32 bytes before base64)
_HKDF_INFO = b"chatapp-x25519-v1"  # application-specific context
_HKDF_SALT = None  # optional: you could set a salt for extra security


def generate_keypair() -> Tuple[X25519PrivateKey, bytes]:
    """
    Generate an X25519 keypair.

    Returns:
        (private_key, public_key_raw_bytes)

    - private_key: X25519PrivateKey instance (keep this private)
    - public_key_raw_bytes: raw 32-byte public key (not base64-encoded)

    Use serialize_public_key(...) to convert public_key_raw_bytes into a
    transmittable string.
    """
    try:
        priv = X25519PrivateKey.generate()
        pub = priv.public_key()
        # Raw public bytes (32 bytes)
        pub_bytes = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return priv, pub_bytes
    except Exception as exc:  # broad to convert any backend error
        raise KeyExchangeError(f"Failed to generate keypair: {exc}") from exc


def serialize_public_key(pub: Union[X25519PublicKey, bytes, bytearray, memoryview]) -> str:
    """
    Return a urlsafe-base64 string representation of the public key.

    Accepts either an X25519PublicKey object or raw public key bytes.
    """
    try:
        if isinstance(pub, X25519PublicKey):
            raw = pub.public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
            )
        elif isinstance(pub, (bytes, bytearray, memoryview)):
            raw = bytes(pub)
        else:
            raise TypeError("pub must be X25519PublicKey or raw bytes")

        return base64.urlsafe_b64encode(raw).decode("ascii")
    except Exception as exc:
        raise KeyExchangeError(f"Failed to serialize public key: {exc}") from exc


def load_public_key(b64: Union[str, bytes]) -> X25519PublicKey:
    """
    Load a urlsafe-base64-encoded public key and return an X25519PublicKey object.
    """
    try:
        if isinstance(b64, str):
            b64 = b64.encode("ascii")
        raw = base64.urlsafe_b64decode(b64)
        if len(raw) != 32:
            raise KeyExchangeError("Invalid public key length after base64 decoding")
        return X25519PublicKey.from_public_bytes(raw)
    except KeyExchangeError:
        raise
    except Exception as exc:
        raise KeyExchangeError(f"Failed to load public key: {exc}") from exc


def derive_shared_key(
    priv: X25519PrivateKey, peer_public_b64: Union[str, bytes]
) -> bytes:
    """
    Derive a symmetric key suitable for Fernet from an X25519 private key and
    the peer's public key (provided as urlsafe-base64).

    Returns:
        fernet_key (bytes): urlsafe-base64-encoded 32-byte key (ready to pass to Fernet)

    Example:
        priv, pub_bytes = generate_keypair()
        pub_b64 = serialize_public_key(pub_bytes)
        # on other side: derive_shared_key(other_priv, pub_b64)
    """
    try:
        # Convert peer public to X25519PublicKey
        peer_pub = load_public_key(peer_public_b64)

        # Raw ECDH exchange (32 bytes)
        shared_secret = priv.exchange(peer_pub)

        # Derive a 32-byte symmetric key with HKDF-SHA256
        hkdf = HKDF(
            algorithm=_HKDF_HASH,
            length=_HKDF_LENGTH,
            salt=_HKDF_SALT,
            info=_HKDF_INFO,
        )
        key_bytes = hkdf.derive(shared_secret)  # 32 bytes

        # Fernet requires a urlsafe-base64-encoded 32-byte key (as bytes)
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        if len(base64.urlsafe_b64decode(fernet_key)) != _HKDF_LENGTH:
            raise KeyExchangeError("Derived key is not the expected length")

        return fernet_key
    except KeyExchangeError:
        raise
    except Exception as exc:
        raise KeyExchangeError(f"Failed to derive shared key: {exc}") from exc


# Convenience: helper to get public key b64 from private key directly
def get_public_key_b64_from_private(priv: X25519PrivateKey) -> str:
    """
    Given a private key, return the public key as urlsafe-base64 string.
    """
    try:
        pub = priv.public_key()
        return serialize_public_key(pub)
    except Exception as exc:
        raise KeyExchangeError(f"Failed to get public key from private key: {exc}") from exc
