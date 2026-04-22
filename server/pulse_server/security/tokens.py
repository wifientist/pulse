"""Token generation and verification.

Plaintext tokens exist briefly at issuance and then only on the holder (agent disk, admin
env var). The database stores argon2 hashes.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

_TOKEN_BYTES = 32


def new_token() -> str:
    """Generate a URL-safe random token string."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_token(plaintext: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
