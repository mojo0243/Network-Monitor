"""Password hashing for the dashboard login.

Uses passlib's pbkdf2_sha256 handler specifically because it is pure Python --
no bcrypt/argon2 C extension to compile on a Raspberry Pi.
"""
from __future__ import annotations

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)
