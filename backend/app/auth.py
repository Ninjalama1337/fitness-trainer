"""Authentifizierung: Argon2-Hashing, Session-Cookies, CSRF-Schutz, Rate-Limit."""
import threading
import time
from collections import defaultdict, deque

import argon2
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request
from sqlmodel import select

from . import config, crypto, db
from .models import User

SESSION_COOKIE = "ft_session"

ph = argon2.PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

# Einfaches In-Memory Rate-Limit: pro IP+Username → Zeitstempel der letzten Versuche
_rate_lock = threading.Lock()
_rate: dict[tuple, deque] = defaultdict(deque)


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def rate_limited(key: tuple, max_attempts: int = 5, window: int = 60) -> bool:
    now = time.time()
    with _rate_lock:
        dq = _rate[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= max_attempts:
            return True
        dq.append(now)
        return False


def get_user_by_username(username: str) -> User | None:
    with db.session() as s:
        return s.exec(select(User).where(User.username == username)).first()


def get_user(user_id: int) -> User | None:
    with db.session() as s:
        return s.get(User, user_id)


def authenticate(username: str, password: str) -> User | None:
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def set_session_cookie(response, user: User):
    token = crypto.create_session_token(user.id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=crypto.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # hinter Reverse-Proxy via X-Forwarded-Proto angepasst
    )


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE)


def get_current_user(
    request: Request,
    session: str = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    user = _resolve_user(session)
    if not user:
        raise HTTPException(401, "Nicht eingeloggt")
    return user


def _resolve_user(session_token: str | None) -> User | None:
    if not session_token:
        return None
    user_id = crypto.read_session_token(session_token)
    if not user_id:
        return None
    return get_user(user_id)


def get_optional_user(
    request: Request,
    session: str = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    return _resolve_user(session)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin-Rechte erforderlich")
    return user


def assert_csrf_valid(request: Request) -> None:
    """Origin/Referer-Check für state-ändernde Requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        raise HTTPException(403, "CSRF: fehlender Origin")
    try:
        from urllib.parse import urlparse

        origin_host = urlparse(origin).netloc
        host = request.headers.get("host", "")
    except Exception:
        raise HTTPException(403, "CSRF: ungültiger Origin")
    if origin_host != host:
        raise HTTPException(403, "CSRF: Origin stimmt nicht überein")


def ensure_admin_exists() -> User | None:
    """Legt beim Erststart den Admin aus der .env an (falls noch kein User existiert)."""
    with db.session() as s:
        existing = s.exec(select(User)).first()
        if existing:
            return existing
    username = config.get("ADMIN_USER") or "admin"
    password = config.get("ADMIN_PASSWORD")
    if not password:
        return None
    with db.session() as s:
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name="Admin",
            is_admin=True,
        )
        s.add(user)
        s.commit()
        return user
