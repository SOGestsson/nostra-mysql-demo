from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any

from fastapi import HTTPException, Request

from app import auth as auth_module

_DEFAULT_CORS_ORIGINS = [
    "https://petur.nostradamus-api.com",
    "https://consumables.nostradamus-api.com",
    "https://demo.nostradamus-api.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 10
_login_attempts: dict[str, list[float]] = defaultdict(list)


def docs_enabled() -> bool:
    return os.getenv("ENABLE_DOCS", "").strip().lower() in {"1", "true", "yes"}


def cors_allow_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return list(_DEFAULT_CORS_ORIGINS)


def require_request_user(
    authorization: str,
    db_name: str | None = None,
    *,
    admin: bool = False,
) -> dict[str, Any]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        if admin:
            user = auth_module.require_admin(token)
            if db_name:
                auth_module.require_db_users_access(token, db_name)
            return user
        if db_name:
            return auth_module.require_db_users_access(token, db_name)
        return auth_module.verify_token(token)
    except ValueError as exc:
        message = str(exc)
        if message in {"Token expired", "Invalid token"}:
            raise HTTPException(status_code=401, detail=message) from exc
        if message in {"Admin access required", "Access denied for this database"}:
            raise HTTPException(status_code=403, detail=message) from exc
        if message == "db is required":
            raise HTTPException(status_code=400, detail=message) from exc
        raise HTTPException(status_code=401, detail=message) from exc


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def check_login_rate(ip: str) -> None:
    now = time.time()
    key = ip or "unknown"
    recent = [stamp for stamp in _login_attempts[key] if now - stamp < LOGIN_WINDOW_SECONDS]
    if len(recent) >= LOGIN_MAX_ATTEMPTS:
        _login_attempts[key] = recent
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    recent.append(now)
    _login_attempts[key] = recent
