from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_text(value: str, secret: str) -> str:
    if not secret:
        raise ValueError("APP_ENCRYPTION_KEY is required to encrypt stored secrets.")
    return _fernet(secret).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str, secret: str) -> str:
    if not secret:
        return value
    try:
        return _fernet(secret).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return value