from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import FrozenInstanceError
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.query_cursor import (
    MAX_CURSOR_ASCII_BYTES,
    InvalidCursorError,
    StaleCursorError,
    SymbolFilters,
    SymbolPageCursorCodec,
    SymbolPageKeyset,
    symbol_filter_binding,
)
from src.text_contracts import (
    MAX_NAME_UTF8_BYTES,
    MAX_QUALIFIED_NAME_UTF8_BYTES,
    MAX_RELATIVE_PATH_UTF8_BYTES,
)


KEY = bytearray(b"k" * 32)
PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:cursor"))
OTHER_PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:other"))
SYMBOL_ID = str(uuid5(NAMESPACE_URL, "symbol:cursor"))
FILTERS = SymbolFilters(
    kind="function",
    name="run",
    qualified_name="module.run",
    relative_path="src/module.py",
)
AFTER = SymbolPageKeyset("src/module.py", 17, "module.run", SYMBOL_ID)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _payload() -> dict[str, Any]:
    return {
        "v": 1,
        "t": "symbol_page",
        "p": hashlib.sha256(PROJECT_ID.encode()).hexdigest(),
        "g": 7,
        "f": symbol_filter_binding(FILTERS, 50),
        "a": ["src/module.py", 17, "module.run", SYMBOL_ID],
    }


def _forge(*, payload: object | None = None, raw: bytes | None = None) -> str:
    if raw is None:
        raw = json.dumps(
            _payload() if payload is None else payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    segment = _b64url(raw)
    tag = hmac.new(
        bytes(KEY), b"karst:symbol-page:v1\0" + segment.encode(), hashlib.sha256
    ).digest()
    return f"{segment}.{_b64url(tag)}"


def _decode(codec: SymbolPageCursorCodec, token: str) -> SymbolPageKeyset:
    return codec.decode(
        token,
        expected_project_stable_id=PROJECT_ID,
        expected_generation_id=7,
        filters=FILTERS,
        limit=50,
    )


def test_round_trip_is_canonical_deterministic_and_owns_the_key() -> None:
    source_key = bytearray(KEY)
    codec = SymbolPageCursorCodec(source_key)
    token = codec.encode(
        project_stable_id=PROJECT_ID,
        generation_id=7,
        filters=FILTERS,
        limit=50,
        after=AFTER,
    )
    source_key[:] = b"x" * len(source_key)

    assert token == _forge()
    assert token == SymbolPageCursorCodec(KEY).encode(
        project_stable_id=PROJECT_ID,
        generation_id=7,
        filters=FILTERS,
        limit=50,
        after=AFTER,
    )
    assert token.isascii() and "=" not in token
    assert len(token.encode("ascii")) <= MAX_CURSOR_ASCII_BYTES
    assert _decode(codec, token) == AFTER
    with pytest.raises(FrozenInstanceError):
        AFTER.start_line = 18  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        FILTERS.name = "other"  # type: ignore[misc]


def test_filter_binding_is_exact_case_sensitive_and_covers_limit_and_sort() -> None:
    baseline = symbol_filter_binding(FILTERS, 50)
    variants = (
        SymbolFilters(**{**FILTERS.as_dict(), "kind": "method"}),
        SymbolFilters(**{**FILTERS.as_dict(), "name": "Run"}),
        SymbolFilters(**{**FILTERS.as_dict(), "qualified_name": "Module.run"}),
        SymbolFilters(**{**FILTERS.as_dict(), "relative_path": "src/Module.py"}),
    )

    assert all(symbol_filter_binding(item, 50) != baseline for item in variants)
    assert symbol_filter_binding(FILTERS, 49) != baseline
    assert symbol_filter_binding(FILTERS, 50, sort_version=2) != baseline


def test_filter_unicode_boundaries_use_shared_utf8_contracts() -> None:
    exact_name = "é" * (MAX_NAME_UTF8_BYTES // 2)
    exact_qualified_name = "é" * (MAX_QUALIFIED_NAME_UTF8_BYTES // 2)
    exact_path = "é" * 2047 + "ab"
    exact = SymbolFilters(
        kind="function",
        name=exact_name,
        qualified_name=exact_qualified_name,
        relative_path=exact_path,
    )

    assert len(exact_path.encode()) == MAX_RELATIVE_PATH_UTF8_BYTES
    assert symbol_filter_binding(exact, 1)
    with pytest.raises(ValueError):
        SymbolFilters(name=exact_name + "é")
    with pytest.raises(ValueError):
        SymbolFilters(qualified_name=exact_qualified_name + "é")
    with pytest.raises(ValueError):
        SymbolFilters(relative_path=exact_path + "c")
    for kwargs in ({"kind": "Function"}, {"relative_path": "../a.py"}):
        with pytest.raises(ValueError):
            SymbolFilters(**kwargs)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: {**value, "v": 2},
        lambda value: {**value, "v": True},
        lambda value: {**value, "t": "snippet"},
        lambda value: {**value, "p": "A" * 64},
        lambda value: {**value, "g": 0},
        lambda value: {**value, "g": True},
        lambda value: {**value, "f": "0" * 63},
        lambda value: {**value, "a": "not-an-array"},
        lambda value: {**value, "a": ["src/a.py", 1, "a", SYMBOL_ID, "column"]},
        lambda value: {**value, "a": ["../a.py", 1, "a", SYMBOL_ID]},
        lambda value: {**value, "a": ["a.py", True, "a", SYMBOL_ID]},
        lambda value: {**value, "a": ["a.py", 1, "a", PROJECT_ID.upper()]},
        lambda value: {**value, "column": "qualified_name"},
        lambda value: {key: item for key, item in value.items() if key != "a"},
    ],
)
def test_authenticated_wrong_shape_and_components_are_invalid(mutate: Any) -> None:
    with pytest.raises(InvalidCursorError):
        _decode(SymbolPageCursorCodec(KEY), _forge(payload=mutate(_payload())))


def test_duplicate_keys_and_noncanonical_json_are_rejected_after_authentication() -> (
    None
):
    canonical = json.dumps(_payload(), separators=(",", ":"), sort_keys=True)
    duplicate = canonical.replace('"v":1', '"v":1,"v":1')
    spaced = json.dumps(_payload(), sort_keys=True)

    for raw in (duplicate.encode(), spaced.encode()):
        with pytest.raises(InvalidCursorError):
            _decode(SymbolPageCursorCodec(KEY), _forge(raw=raw))


def test_tamper_truncate_wrong_key_and_strict_token_syntax_are_invalid() -> None:
    token = _forge()
    payload, tag = token.split(".")
    attacks = (
        f"{'A' if payload[0] != 'A' else 'B'}{payload[1:]}.{tag}",
        token[:-1],
        f"{payload}.{tag}=",
        f"{payload}..{tag}",
        f"{payload}+.${tag}",
        "é." + tag,
        "A" * (MAX_CURSOR_ASCII_BYTES + 1),
    )
    for attack in attacks:
        with pytest.raises(InvalidCursorError):
            _decode(SymbolPageCursorCodec(KEY), attack)
    with pytest.raises(InvalidCursorError):
        _decode(SymbolPageCursorCodec(b"z" * 32), token)


def test_context_mismatch_is_invalid_but_authenticated_generation_is_stale() -> None:
    codec = SymbolPageCursorCodec(KEY)
    token = _forge()
    with pytest.raises(InvalidCursorError):
        codec.decode(
            token,
            expected_project_stable_id=OTHER_PROJECT_ID,
            expected_generation_id=7,
            filters=FILTERS,
            limit=50,
        )
    for filters, limit in ((SymbolFilters(name="other"), 50), (FILTERS, 51)):
        with pytest.raises(InvalidCursorError):
            codec.decode(
                token,
                expected_project_stable_id=PROJECT_ID,
                expected_generation_id=7,
                filters=filters,
                limit=limit,
            )
    with pytest.raises(StaleCursorError):
        codec.decode(
            token,
            expected_project_stable_id=PROJECT_ID,
            expected_generation_id=8,
            filters=FILTERS,
            limit=50,
        )


def test_key_and_encode_components_are_strict_and_token_size_is_bounded() -> None:
    for key in (b"short", "k" * 32):
        with pytest.raises(ValueError):
            SymbolPageCursorCodec(key)  # type: ignore[arg-type]
    codec = SymbolPageCursorCodec(KEY)
    with pytest.raises(ValueError):
        codec.encode(
            project_stable_id=PROJECT_ID,
            generation_id=True,  # type: ignore[arg-type]
            filters=FILTERS,
            limit=50,
            after=AFTER,
        )
    oversized_after = SymbolPageKeyset(
        "x/" + "a" * (MAX_RELATIVE_PATH_UTF8_BYTES - 2),
        1,
        "q" * MAX_QUALIFIED_NAME_UTF8_BYTES,
        SYMBOL_ID,
    )
    with pytest.raises(ValueError, match="4096"):
        codec.encode(
            project_stable_id=PROJECT_ID,
            generation_id=7,
            filters=FILTERS,
            limit=50,
            after=oversized_after,
        )
