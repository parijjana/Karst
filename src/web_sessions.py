from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock


SESSION_COOKIE_NAME = "karst_admin_session"
SESSION_COOKIE_PATH = "/"


class AdminRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, client_identity: str, now: float | None = None) -> bool:
        current_time = time.monotonic() if now is None else now
        cutoff = current_time - 60.0
        with self._lock:
            events = self._events[client_identity]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                return False
            events.append(current_time)
            return True


@dataclass(frozen=True, slots=True)
class _SessionRecord:
    csrf_digest: bytes
    expires_at: float


class AdminSessionStore:
    """Bounded in-memory store keyed only by a digest of an opaque cookie."""

    def __init__(self, ttl_seconds: int, maximum_sessions: int = 64) -> None:
        self._ttl_seconds = ttl_seconds
        self._maximum_sessions = maximum_sessions
        self._sessions: dict[bytes, _SessionRecord] = {}
        self._lock = Lock()

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()

    def create(self, now: float | None = None) -> tuple[str, str]:
        current_time = time.monotonic() if now is None else now
        cookie = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        cookie_digest = self._digest(cookie)
        with self._lock:
            self._remove_expired(current_time)
            if len(self._sessions) >= self._maximum_sessions:
                oldest = min(
                    self._sessions,
                    key=lambda key: self._sessions[key].expires_at,
                )
                del self._sessions[oldest]
            self._sessions[cookie_digest] = _SessionRecord(
                csrf_digest=self._digest(csrf),
                expires_at=current_time + self._ttl_seconds,
            )
        return cookie, csrf

    def validate_cookie(
        self, cookie: str | None, now: float | None = None
    ) -> bytes | None:
        if not cookie:
            return None
        current_time = time.monotonic() if now is None else now
        cookie_digest = self._digest(cookie)
        with self._lock:
            self._remove_expired(current_time)
            if cookie_digest not in self._sessions:
                return None
        return cookie_digest

    def validate(
        self,
        cookie: str | None,
        csrf: str | None,
        now: float | None = None,
    ) -> bytes | None:
        if not cookie or not csrf:
            return None
        current_time = time.monotonic() if now is None else now
        cookie_digest = self._digest(cookie)
        supplied_csrf_digest = self._digest(csrf)
        with self._lock:
            self._remove_expired(current_time)
            record = self._sessions.get(cookie_digest)
            if record is None or not secrets.compare_digest(
                supplied_csrf_digest, record.csrf_digest
            ):
                return None
        return cookie_digest

    def consume_and_rotate(
        self,
        cookie: str | None,
        csrf: str | None,
        now: float | None = None,
    ) -> tuple[bytes, str] | None:
        """Consume one CSRF value and publish its sole valid successor."""
        if not cookie or not csrf:
            return None
        current_time = time.monotonic() if now is None else now
        cookie_digest = self._digest(cookie)
        supplied_csrf_digest = self._digest(csrf)
        with self._lock:
            self._remove_expired(current_time)
            record = self._sessions.get(cookie_digest)
            if record is None or not secrets.compare_digest(
                supplied_csrf_digest, record.csrf_digest
            ):
                return None
            replacement = secrets.token_urlsafe(32)
            self._sessions[cookie_digest] = _SessionRecord(
                csrf_digest=self._digest(replacement),
                expires_at=record.expires_at,
            )
        return cookie_digest, replacement

    def rotate_csrf(self, cookie_digest: bytes, now: float | None = None) -> str | None:
        current_time = time.monotonic() if now is None else now
        csrf = secrets.token_urlsafe(32)
        with self._lock:
            self._remove_expired(current_time)
            record = self._sessions.get(cookie_digest)
            if record is None:
                return None
            self._sessions[cookie_digest] = _SessionRecord(
                csrf_digest=self._digest(csrf),
                expires_at=record.expires_at,
            )
        return csrf

    def invalidate(self, cookie: str | None) -> None:
        if not cookie:
            return
        cookie_digest = self._digest(cookie)
        with self._lock:
            self._sessions.pop(cookie_digest, None)

    def _remove_expired(self, now: float) -> None:
        expired = [
            key for key, record in self._sessions.items() if record.expires_at <= now
        ]
        for key in expired:
            del self._sessions[key]


@dataclass(frozen=True, slots=True)
class AdminAccess:
    mode: str
    session_digest: bytes | None = None
    next_csrf: str | None = None
