"""Secret-Verschlüsselung (Fernet) + Session-Tokens auf Basis des SECRET_KEY."""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _load_or_create_key() -> bytes:
    env_key = config.get("SECRET_KEY")
    if env_key:
        return base64.urlsafe_b64encode(hashlib.sha256(env_key.encode()).digest())
    key_file = config.DATA_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    os.chmod(key_file, 0o600)
    return key


_fernet = Fernet(_load_or_create_key())

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 Tage


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def create_session_token(user_id: int) -> str:
    payload = json.dumps(
        {"uid": user_id, "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS}
    )
    return _fernet.encrypt(payload.encode()).decode()


def read_session_token(token: str) -> int | None:
    try:
        data = json.loads(_fernet.decrypt(token.encode()).decode())
        if int(data.get("exp", 0)) < time.time():
            return None
        return int(data.get("uid", 0))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return None
