from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

import uvicorn
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.mission_control_transition.process_manager import (
    get_script_status,
    shutdown_all_scripts,
    start_script,
    stop_script,
)
from src.settings import Settings, settings as default_settings
from src.web_auth import (
    browser_session_access as _browser_session_access,
    clear_session_cookie as _clear_session_cookie,
    constant_time_secret_match as _constant_time_secret_match,
    enforce_admin_rate_limit as _enforce_admin_rate_limit,
    request_settings,
    require_admin,
    require_allowed_origin as _require_allowed_origin,
    require_remote_access,
    rotate_session_after_success as _rotate_session_after_success,
    session_store as _session_store,
    set_session_cookie as _set_session_cookie,
)
from src.web_data import (
    get_db,
    page_bounds as _page_bounds,
    router as data_router,
    table_exists,
)
from src.web_graph import router as graph_router
from src.web_history import router as history_router
from src.web_sessions import (
    AdminAccess,
    AdminRateLimiter,
    AdminSessionStore,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
)


__all__ = [
    "AdminAccess",
    "AdminRateLimiter",
    "AdminSessionStore",
    "DASHBOARD_CSP",
    "SECURITY_HEADERS",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_PATH",
    "_constant_time_secret_match",
    "_page_bounds",
    "app",
    "create_app",
    "get_db",
    "request_settings",
    "router",
    "run_dashboard",
    "secrets",
    "table_exists",
]


DASHBOARD_SCRIPT_HASH = "ATPUJAUfzc1fzQSj49Sy4FuMUdG0pX6wTovPRVFYi5A="
DASHBOARD_CSP = (
    "default-src 'none'; "
    f"script-src 'sha256-{DASHBOARD_SCRIPT_HASH}'; "
    "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
    "font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; manifest-src 'none'; worker-src 'none'; "
    "media-src 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": DASHBOARD_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

router = APIRouter()
router.include_router(data_router)
router.include_router(history_router)
router.include_router(graph_router)


class AdminLoginRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=4096)


@router.get("/", response_class=HTMLResponse)
async def get_dashboard() -> str:
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")


@router.post("/api/admin/session/login")
async def login_admin_session(
    payload: AdminLoginRequest, request: Request, response: Response
) -> dict[str, str | int]:
    configured = request_settings(request)
    _require_allowed_origin(request, required=True)
    _enforce_admin_rate_limit(request, category="login")
    if configured.admin_token is None or not _constant_time_secret_match(
        payload.capability, configured.admin_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authorization required.",
        )
    store = _session_store(request)
    store.invalidate(request.cookies.get(SESSION_COOKIE_NAME))
    cookie, csrf = store.create()
    _set_session_cookie(response, configured, cookie)
    return {"csrf_token": csrf, "expires_in": configured.admin_session_ttl_seconds}


@router.post("/api/admin/session/csrf")
async def bootstrap_session_csrf(request: Request) -> dict[str, str]:
    _require_allowed_origin(request, required=True)
    _enforce_admin_rate_limit(request, category="session")
    digest = _session_store(request).validate_cookie(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if digest is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Admin authorization required."
        )
    csrf = _session_store(request).rotate_csrf(digest)
    if csrf is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Admin authorization required."
        )
    return {"csrf_token": csrf}


@router.delete("/api/admin/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout_admin_session(
    request: Request,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    _browser_session_access(request, csrf_token)
    configured = request_settings(request)
    _session_store(request).invalidate(request.cookies.get(SESSION_COOKIE_NAME))
    request.state.admin_csrf_token = None
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response, configured)
    return response


@router.post("/api/admin/scripts/{script_name}/start")
async def start_script_endpoint(
    script_name: str,
    request: Request,
    response: Response,
    access: Annotated[AdminAccess, Depends(require_admin)],
) -> object:
    try:
        result = await start_script(script_name)
    except ValueError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Managed script not found."
        ) from None
    _rotate_session_after_success(request, response, access)
    return result


@router.post("/api/admin/scripts/{script_name}/stop")
async def stop_script_endpoint(
    script_name: str,
    request: Request,
    response: Response,
    access: Annotated[AdminAccess, Depends(require_admin)],
) -> object:
    try:
        result = await stop_script(script_name)
    except ValueError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Managed script not found."
        ) from None
    _rotate_session_after_success(request, response, access)
    return result


@router.get("/api/admin/scripts/{script_name}/status")
async def get_script_status_endpoint(script_name: str) -> object:
    return get_script_status(script_name)


def create_app(configuration: Settings | None = None) -> FastAPI:
    configured = configuration or default_settings

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        yield
        await shutdown_all_scripts()

    application = FastAPI(title="Karst Dashboard", lifespan=lifespan)
    application.state.settings = configured
    application.state.admin_rate_limiter = AdminRateLimiter(
        configured.admin_requests_per_minute
    )
    application.state.admin_session_store = AdminSessionStore(
        configured.admin_session_ttl_seconds
    )
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(configured.allowed_hosts)
    )

    @application.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["Cache-Control"] = "no-store"
        replacement_csrf = getattr(request.state, "admin_csrf_token", None)
        if isinstance(replacement_csrf, str):
            response.headers["X-CSRF-Token"] = replacement_csrf
        return response

    application.include_router(router, dependencies=[Depends(require_remote_access)])
    return application


app = create_app()


def run_dashboard(configuration: Settings = default_settings) -> None:
    uvicorn.run(
        create_app(configuration),
        host=configuration.dashboard_host,
        port=configuration.dashboard_port,
        ssl_certfile=str(configuration.tls_certfile)
        if configuration.tls_certfile
        else None,
        ssl_keyfile=str(configuration.tls_keyfile)
        if configuration.tls_keyfile
        else None,
    )


if __name__ == "__main__":
    run_dashboard()
