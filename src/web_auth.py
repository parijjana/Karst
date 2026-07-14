from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request, Response, status

from src.settings import Settings
from src.web_sessions import (
    AdminAccess,
    AdminRateLimiter,
    AdminSessionStore,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
)


def request_settings(request: Request) -> Settings:
    configured = request.app.state.settings
    if not isinstance(configured, Settings):
        raise RuntimeError("Application settings are unavailable.")
    return configured


def session_store(request: Request) -> AdminSessionStore:
    store = request.app.state.admin_session_store
    if not isinstance(store, AdminSessionStore):
        raise RuntimeError("Admin session store is unavailable.")
    return store


def constant_time_secret_match(supplied: str, expected: str) -> bool:
    supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    return secrets.compare_digest(supplied_digest, expected_digest)


def require_allowed_origin(request: Request, required: bool) -> None:
    origin = request.headers.get("origin")
    configured = request_settings(request)
    allowed = {item.rstrip("/") for item in configured.allowed_origins}
    if origin is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Same-origin admin request required.",
            )
        return
    if origin.rstrip("/") not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin admin request denied.",
        )


def enforce_admin_rate_limit(request: Request, category: str = "mutation") -> None:
    client_identity = request.client.host if request.client else "local"
    limiter = request.app.state.admin_rate_limiter
    rate_key = f"{category}:{client_identity}"
    if not isinstance(limiter, AdminRateLimiter) or not limiter.allow(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Admin request rate exceeded.",
        )


def browser_session_access(request: Request, csrf_token: str | None) -> AdminAccess:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authorization required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    require_allowed_origin(request, required=True)
    consumed = session_store(request).consume_and_rotate(cookie, csrf_token)
    if consumed is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed.",
        )
    session_digest, next_csrf = consumed
    request.state.admin_csrf_token = next_csrf
    return AdminAccess(
        mode="session",
        session_digest=session_digest,
        next_csrf=next_csrf,
    )


async def require_admin(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AdminAccess:
    configured = request_settings(request)
    if configured.admin_token is None or configured.csrf_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin mutations are disabled.",
        )
    if authorization is None:
        access = browser_session_access(request, csrf_token)
    else:
        if not authorization.startswith("Bearer ") or not constant_time_secret_match(
            authorization.removeprefix("Bearer "), configured.admin_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Admin authorization required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        require_allowed_origin(request, required=False)
        if csrf_token is None or not constant_time_secret_match(
            csrf_token, configured.csrf_token
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed.",
            )
        access = AdminAccess(mode="bearer")
    enforce_admin_rate_limit(request)
    return access


async def require_remote_access(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    configured = request_settings(request)
    if configured.is_dashboard_loopback:
        return
    if request.url.path in {"/", "/api/admin/session/login"}:
        return
    if authorization is not None:
        if not authorization.startswith("Bearer ") or configured.admin_token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Remote dashboard authorization required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        supplied = authorization.removeprefix("Bearer ")
        if not constant_time_secret_match(supplied, configured.admin_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Remote dashboard authorization required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        origin = request.headers.get("origin")
        allowed = {item.rstrip("/") for item in configured.allowed_origins}
        if origin is None or origin.rstrip("/") not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Remote dashboard Origin required.",
            )
        return
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if session_store(request).validate_cookie(cookie) is not None:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Remote dashboard authorization required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def set_session_cookie(response: Response, configured: Settings, cookie: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie,
        max_age=configured.admin_session_ttl_seconds,
        path=SESSION_COOKIE_PATH,
        secure=not configured.is_dashboard_loopback,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response, configured: Settings) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=SESSION_COOKIE_PATH,
        secure=not configured.is_dashboard_loopback,
        httponly=True,
        samesite="strict",
    )


def rotate_session_after_success(
    request: Request, response: Response, access: AdminAccess
) -> None:
    if access.mode != "session" or access.next_csrf is None:
        return
    response.headers["X-CSRF-Token"] = access.next_csrf
