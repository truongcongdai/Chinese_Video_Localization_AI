# src/universal_video_ai/web/auth.py
"""
Minimal session-cookie auth for the web UI.

Deliberately simple: one signed cookie holding the user id (via
itsdangerous), bcrypt-hashed passwords (via passlib), no external identity
provider. Good enough for "a small number of trusted people log into our own
server"; if you need SSO/multi-tenant auth later, swap this module out.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Request, HTTPException, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext

from .store import Store

COOKIE_NAME = "vai_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_secret_key() -> str:
    secret = os.environ.get("WEB_SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "WEB_SESSION_SECRET is not set. Set it to a long random string in your .env "
            "(e.g. `openssl rand -hex 32`) before starting the web server — without it, "
            "login sessions can't be signed securely."
        )
    return secret


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def create_session_cookie_value(user_id: int) -> str:
    serializer = URLSafeTimedSerializer(_get_secret_key())
    return serializer.dumps({"user_id": user_id})


def read_session_cookie_value(value: str) -> Optional[int]:
    serializer = URLSafeTimedSerializer(_get_secret_key())
    try:
        data = serializer.loads(value, max_age=SESSION_MAX_AGE_SECONDS)
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def get_current_user_id(request: Request) -> int:
    """FastAPI dependency: returns the logged-in user's id or raises 401."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    user_id = read_session_cookie_value(raw)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user_id


def require_admin(request: Request, store: Store) -> int:
    """Like `get_current_user_id`, but also requires `is_admin`. Since this
    needs the Store instance (not itself injectable without app state), the
    FastAPI route wires it as `Depends(lambda r=Request: require_admin(r, store))`
    from app.py where `store` is in scope — see `_require_admin_dep` there."""
    user_id = get_current_user_id(request)
    user = store.get_user_by_id(user_id)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Yêu cầu quyền admin")
    return user_id
