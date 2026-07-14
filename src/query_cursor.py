from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.index_identity import _require_uuid5
from src.text_contracts import (
    require_kind,
    require_name,
    require_qualified_name,
    require_relative_posix_path,
)


MAX_CURSOR_ASCII_BYTES = 4096
SYMBOL_PAGE_SORT_VERSION = 1
_DOMAIN = b"karst:symbol-page:v1\0"
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PAYLOAD_KEYS = frozenset({"v", "t", "p", "g", "f", "a"})


class InvalidCursorError(ValueError):
    pass


class StaleCursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SymbolFilters:
    kind: str | None = None
    name: str | None = None
    qualified_name: str | None = None
    relative_path: str | None = None

    def __post_init__(self) -> None:
        validators = (
            ("kind", require_kind),
            ("name", require_name),
            ("qualified_name", require_qualified_name),
            ("relative_path", require_relative_posix_path),
        )
        for attribute, validator in validators:
            value = getattr(self, attribute)
            if value is not None:
                validator(value)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class SymbolPageKeyset:
    relative_path: str
    start_line: int
    qualified_name: str
    stable_symbol_id: str

    def __post_init__(self) -> None:
        require_relative_posix_path(self.relative_path)
        _require_positive_int(self.start_line, "start_line")
        require_qualified_name(self.qualified_name)
        _require_uuid5(self.stable_symbol_id, "stable_symbol_id")

    def as_list(self) -> list[str | int]:
        return [
            self.relative_path,
            self.start_line,
            self.qualified_name,
            self.stable_symbol_id,
        ]


def _require_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def symbol_filter_binding(
    filters: SymbolFilters,
    limit: int,
    *,
    sort_version: int = SYMBOL_PAGE_SORT_VERSION,
) -> str:
    if not isinstance(filters, SymbolFilters):
        raise ValueError("filters must be SymbolFilters.")
    requested_limit = _require_positive_int(limit, "limit")
    version = _require_positive_int(sort_version, "sort_version")
    material = {
        "filters": filters.as_dict(),
        "limit": requested_limit,
        "sort_version": version,
    }
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_segment(segment: str) -> bytes:
    if _SEGMENT.fullmatch(segment) is None or len(segment) % 4 == 1:
        raise InvalidCursorError("Cursor encoding is invalid.")
    padding = "=" * (-len(segment) % 4)
    try:
        decoded = base64.b64decode(segment + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidCursorError("Cursor encoding is invalid.") from error
    if not hmac.compare_digest(_b64url(decoded), segment):
        raise InvalidCursorError("Cursor encoding is invalid.")
    return decoded


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}.")


def _parse_payload(raw: bytes) -> tuple[str, int, str, SymbolPageKeyset]:
    try:
        text = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise InvalidCursorError("Cursor payload is invalid.") from error
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise InvalidCursorError("Cursor payload is invalid.")
    if _canonical_json(payload) != raw:
        raise InvalidCursorError("Cursor payload is not canonical.")
    if type(payload["v"]) is not int or payload["v"] != 1:
        raise InvalidCursorError("Cursor version is invalid.")
    if payload["t"] != "symbol_page":
        raise InvalidCursorError("Cursor endpoint is invalid.")
    project_digest = payload["p"]
    binding = payload["f"]
    if not isinstance(project_digest, str) or _SHA256.fullmatch(project_digest) is None:
        raise InvalidCursorError("Cursor project binding is invalid.")
    if not isinstance(binding, str) or _SHA256.fullmatch(binding) is None:
        raise InvalidCursorError("Cursor filter binding is invalid.")
    try:
        generation_id = _require_positive_int(payload["g"], "generation_id")
        after = payload["a"]
        if not isinstance(after, list) or len(after) != 4:
            raise ValueError("after must contain four values.")
        keyset = SymbolPageKeyset(after[0], after[1], after[2], after[3])
    except (TypeError, ValueError) as error:
        raise InvalidCursorError("Cursor keyset is invalid.") from error
    return project_digest, generation_id, binding, keyset


@dataclass(frozen=True, slots=True, init=False)
class SymbolPageCursorCodec:
    _key: bytes = field(repr=False)
    _sort_version: int

    def __init__(
        self,
        key: bytes | bytearray | memoryview,
        *,
        sort_version: int = SYMBOL_PAGE_SORT_VERSION,
    ) -> None:
        if not isinstance(key, (bytes, bytearray, memoryview)):
            raise ValueError("cursor key must be bytes.")
        try:
            owned_key = memoryview(key).tobytes()
        except (TypeError, ValueError) as error:
            raise ValueError("cursor key must be bytes.") from error
        if len(owned_key) < 32:
            raise ValueError("cursor key must contain at least 32 bytes.")
        object.__setattr__(self, "_key", owned_key)
        object.__setattr__(
            self,
            "_sort_version",
            _require_positive_int(sort_version, "sort_version"),
        )

    def encode(
        self,
        *,
        project_stable_id: str,
        generation_id: int,
        filters: SymbolFilters,
        limit: int,
        after: SymbolPageKeyset,
    ) -> str:
        project_id = _require_uuid5(project_stable_id, "project_stable_id")
        generation = _require_positive_int(generation_id, "generation_id")
        if not isinstance(after, SymbolPageKeyset):
            raise ValueError("after must be a SymbolPageKeyset.")
        payload = {
            "v": 1,
            "t": "symbol_page",
            "p": hashlib.sha256(project_id.encode("utf-8")).hexdigest(),
            "g": generation,
            "f": symbol_filter_binding(filters, limit, sort_version=self._sort_version),
            "a": after.as_list(),
        }
        payload_segment = _b64url(_canonical_json(payload))
        tag = hmac.new(
            self._key,
            _DOMAIN + payload_segment.encode("ascii"),
            hashlib.sha256,
        ).digest()
        token = f"{payload_segment}.{_b64url(tag)}"
        if len(token.encode("ascii")) > MAX_CURSOR_ASCII_BYTES:
            raise ValueError("cursor exceeds the 4096-byte ASCII limit.")
        return token

    def decode(
        self,
        token: str,
        *,
        expected_project_stable_id: str,
        expected_generation_id: int,
        filters: SymbolFilters,
        limit: int,
    ) -> SymbolPageKeyset:
        try:
            project_id = _require_uuid5(
                expected_project_stable_id, "expected_project_stable_id"
            )
            expected_generation = _require_positive_int(
                expected_generation_id, "expected_generation_id"
            )
            expected_binding = symbol_filter_binding(
                filters, limit, sort_version=self._sort_version
            )
        except ValueError as error:
            raise InvalidCursorError("Cursor context is invalid.") from error
        raw = self._authenticate(token)
        project_digest, generation, binding, keyset = _parse_payload(raw)
        expected_project = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(project_digest, expected_project):
            raise InvalidCursorError("Cursor project binding does not match.")
        if not hmac.compare_digest(binding, expected_binding):
            raise InvalidCursorError("Cursor filter binding does not match.")
        if generation != expected_generation:
            raise StaleCursorError("Cursor generation is stale.")
        return keyset

    def _authenticate(self, token: str) -> bytes:
        if not isinstance(token, str):
            raise InvalidCursorError("Cursor token must be text.")
        try:
            encoded = token.encode("ascii", errors="strict")
        except UnicodeEncodeError as error:
            raise InvalidCursorError("Cursor token must be ASCII.") from error
        if not encoded or len(encoded) > MAX_CURSOR_ASCII_BYTES:
            raise InvalidCursorError("Cursor token size is invalid.")
        parts = token.split(".")
        if len(parts) != 2:
            raise InvalidCursorError("Cursor token must have two segments.")
        payload_segment, tag_segment = parts
        payload = _decode_segment(payload_segment)
        tag = _decode_segment(tag_segment)
        if len(tag) != hashlib.sha256().digest_size:
            raise InvalidCursorError("Cursor tag size is invalid.")
        expected = hmac.new(
            self._key,
            _DOMAIN + payload_segment.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected):
            raise InvalidCursorError("Cursor authentication failed.")
        return payload
