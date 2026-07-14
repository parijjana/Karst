from __future__ import annotations

import ipaddress
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRUSTED_LOCAL_OWNER = "local-stdio"


class SettingsError(ValueError):
    """Raised when Karst cannot start with a safe configuration."""


def _absolute_from_project(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve(strict=False)


def _split_values(value: str, separator: str = ",") -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(separator) if part.strip())


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError("Invalid boolean security setting.")


def _parse_int(value: str, error_message: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise SettingsError(error_message) from error


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_tls_pair(certificate: Path, private_key: Path) -> None:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=certificate, keyfile=private_key)
    except (OSError, ssl.SSLError) as error:
        raise SettingsError("Dashboard TLS credentials are invalid.") from error


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration for one mutually trusted local stdio deployment.

    The current launcher supplies no authenticated per-client identity. All MCP
    clients therefore share one project namespace and one local trust domain.
    """

    data_dir: Path
    db_path: Path
    allowed_roots: tuple[Path, ...]
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8085
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8085",
        "http://localhost:8085",
        "http://[::1]:8085",
    )
    admin_token: str | None = None
    csrf_token: str | None = None
    allow_remote_dashboard: bool = False
    admin_requests_per_minute: int = 10
    admin_session_ttl_seconds: int = 900
    dashboard_default_page_size: int = 100
    dashboard_max_page_size: int = 250
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None

    def __post_init__(self) -> None:
        data_dir = _absolute_from_project(self.data_dir)
        db_path = _absolute_from_project(self.db_path)
        canonical_roots: list[Path] = []
        for root in self.allowed_roots:
            candidate = _absolute_from_project(root)
            if not candidate.is_dir():
                raise SettingsError("An allowed root is unavailable.")
            canonical = candidate.resolve(strict=True)
            if canonical not in canonical_roots:
                canonical_roots.append(canonical)

        if not canonical_roots:
            raise SettingsError("At least one allowed root is required.")
        if not 1 <= self.dashboard_port <= 65535:
            raise SettingsError("Dashboard port is invalid.")
        if not self.allowed_hosts or any(
            not host or len(host) > 253 for host in self.allowed_hosts
        ):
            raise SettingsError("Trusted hosts are invalid.")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise SettingsError("An allowed origin is invalid.")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise SettingsError("An allowed origin is invalid.")
        if (self.admin_token is None) != (self.csrf_token is None):
            raise SettingsError(
                "Admin and CSRF capabilities must be configured together."
            )
        if self.admin_token is not None:
            if len(self.admin_token) < 32 or len(self.csrf_token or "") < 32:
                raise SettingsError("Admin capabilities are too short.")
            if self.admin_token == self.csrf_token:
                raise SettingsError("Admin and CSRF capabilities must be distinct.")
        if (self.tls_certfile is None) != (self.tls_keyfile is None):
            raise SettingsError("Dashboard TLS certificate and key are both required.")
        tls_certfile = (
            _absolute_from_project(self.tls_certfile)
            if self.tls_certfile is not None
            else None
        )
        tls_keyfile = (
            _absolute_from_project(self.tls_keyfile)
            if self.tls_keyfile is not None
            else None
        )
        if tls_certfile is not None and tls_keyfile is not None:
            if not tls_certfile.is_file() or not tls_keyfile.is_file():
                raise SettingsError("Dashboard TLS credentials are unavailable.")
            _validate_tls_pair(tls_certfile, tls_keyfile)
        if not self.is_dashboard_loopback and not self.allow_remote_dashboard:
            raise SettingsError("Remote dashboard binding is disabled.")
        if not self.is_dashboard_loopback:
            if tls_certfile is None or tls_keyfile is None:
                raise SettingsError("Remote dashboard requires TLS credentials.")
            if self.admin_token is None or self.csrf_token is None:
                raise SettingsError("Remote dashboard requires admin capabilities.")
            if "*" in self.allowed_hosts:
                raise SettingsError("Wildcard trusted hosts are not allowed remotely.")
            if any(
                urlsplit(origin).scheme != "https" for origin in self.allowed_origins
            ):
                raise SettingsError("Remote dashboard origins must use HTTPS.")
        if not 1 <= self.admin_requests_per_minute <= 120:
            raise SettingsError("Admin rate limit is invalid.")
        if not 60 <= self.admin_session_ttl_seconds <= 3600:
            raise SettingsError("Admin session lifetime is invalid.")
        if not 1 <= self.dashboard_default_page_size <= self.dashboard_max_page_size:
            raise SettingsError("Dashboard default page size is invalid.")
        if not 1 <= self.dashboard_max_page_size <= 500:
            raise SettingsError("Dashboard maximum page size is invalid.")

        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "db_path", db_path)
        object.__setattr__(self, "allowed_roots", tuple(canonical_roots))
        object.__setattr__(self, "tls_certfile", tls_certfile)
        object.__setattr__(self, "tls_keyfile", tls_keyfile)

    @property
    def is_dashboard_loopback(self) -> bool:
        return _is_loopback_host(self.dashboard_host)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        if "KARST_OWNER_ID" in source:
            raise SettingsError(
                "Karst supports one trusted local stdio domain; client owners are unsupported."
            )
        data_dir = _absolute_from_project(source.get("KARST_DATA_DIR", "data"))
        db_path = _absolute_from_project(
            source.get("KARST_DB_PATH", str(data_dir / "knowledge_graph.db"))
        )
        raw_roots = source.get("KARST_ALLOWED_ROOTS")
        allowed_roots = (
            tuple(
                _absolute_from_project(item.strip())
                for item in raw_roots.split(os.pathsep)
                if item.strip()
            )
            if raw_roots
            else (PROJECT_ROOT,)
        )
        host = source.get("KARST_DASHBOARD_HOST", "127.0.0.1").strip()
        port_text = source.get("KARST_DASHBOARD_PORT", "8085")
        port = _parse_int(port_text, "Dashboard port is invalid.")
        default_hosts = (
            (host, "localhost", "127.0.0.1", "::1")
            if _is_loopback_host(host)
            else (host,)
        )
        allowed_hosts = _split_values(
            source.get("KARST_ALLOWED_HOSTS", ",".join(default_hosts))
        )
        default_origins = (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            f"http://[::1]:{port}",
        )
        allowed_origins = _split_values(
            source.get("KARST_ALLOWED_ORIGINS", ",".join(default_origins))
        )
        admin_token = source.get("KARST_ADMIN_TOKEN") or None
        csrf_token = source.get("KARST_CSRF_TOKEN") or None

        return cls(
            data_dir=data_dir,
            db_path=db_path,
            allowed_roots=allowed_roots,
            dashboard_host=host,
            dashboard_port=port,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            admin_token=admin_token,
            csrf_token=csrf_token,
            allow_remote_dashboard=_parse_bool(
                source.get("KARST_ALLOW_REMOTE_DASHBOARD")
            ),
            admin_requests_per_minute=_parse_int(
                source.get("KARST_ADMIN_REQUESTS_PER_MINUTE", "10"),
                "Admin rate limit is invalid.",
            ),
            admin_session_ttl_seconds=_parse_int(
                source.get("KARST_ADMIN_SESSION_TTL_SECONDS", "900"),
                "Admin session lifetime is invalid.",
            ),
            dashboard_default_page_size=_parse_int(
                source.get("KARST_DASHBOARD_DEFAULT_PAGE_SIZE", "100"),
                "Dashboard default page size is invalid.",
            ),
            dashboard_max_page_size=_parse_int(
                source.get("KARST_DASHBOARD_MAX_PAGE_SIZE", "250"),
                "Dashboard maximum page size is invalid.",
            ),
            tls_certfile=(
                Path(source["KARST_TLS_CERTFILE"])
                if source.get("KARST_TLS_CERTFILE")
                else None
            ),
            tls_keyfile=(
                Path(source["KARST_TLS_KEYFILE"])
                if source.get("KARST_TLS_KEYFILE")
                else None
            ),
        )


settings = Settings.from_env()
