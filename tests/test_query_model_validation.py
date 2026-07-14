from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from pydantic import ValidationError

from src.query_models import (
    MAX_CURSOR_UTF8_BYTES,
    MAX_ERROR_MESSAGE_UTF8_BYTES,
    MAX_PAGE_ITEMS,
    MAX_SNIPPET_UTF8_BYTES,
    ApiError,
    QueryErrorCode,
    Snippet,
    SymbolPage,
    SymbolRef,
)
from src.text_contracts import (
    MAX_NAME_UTF8_BYTES,
    MAX_QUALIFIED_NAME_UTF8_BYTES,
    MAX_RELATIVE_PATH_UTF8_BYTES,
    MAX_SIGNATURE_UTF8_BYTES,
)


SYMBOL_ID = str(uuid5(NAMESPACE_URL, "symbol:example.Example"))
SOURCE_HASH = "a" * 64


def symbol(**overrides: Any) -> SymbolRef:
    values: dict[str, Any] = {
        "stable_symbol_id": SYMBOL_ID,
        "kind": "function",
        "name": "example",
        "qualified_name": "module.example",
        "signature": None,
        "relative_path": "src/module.py",
        "start_line": 1,
        "end_line": 2,
        "source_sha256": SOURCE_HASH,
        "generation_id": 3,
    }
    values.update(overrides)
    return SymbolRef(**values)


@pytest.mark.parametrize(
    "relative_path",
    [
        "/secret.py",
        "C:/secret.py",
        r"C:\secret.py",
        r"\\server\share\secret.py",
        "file:/secret.py",
        "file:///secret.py",
        "../secret.py",
        "src/../secret.py",
        "./src/module.py",
    ],
)
def test_symbol_ref_rejects_noncanonical_or_absolute_paths(
    relative_path: str,
) -> None:
    with pytest.raises(ValidationError, match="relative POSIX path|file URI"):
        symbol(relative_path=relative_path)


@pytest.mark.parametrize(
    "message",
    [
        "Failed:/home/user/file.py",
        "/secret.py",
        r"C:\secret.py",
        r"\\server\share\secret.py",
        "file:secret.py",
        "file:///home/user/file.py",
    ],
)
def test_api_error_rejects_path_leakage(message: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        ApiError(
            code=QueryErrorCode.SYMBOL_NOT_FOUND,
            message=message,
            retryable=False,
        )


def test_api_error_message_is_bounded_safe_utf8() -> None:
    valid = "é" * (MAX_ERROR_MESSAGE_UTF8_BYTES // 2)
    error = ApiError(
        code=QueryErrorCode.LIMIT_EXCEEDED,
        message=valid,
        retryable=False,
    )

    assert error.message == valid
    for message in (
        "é" * (MAX_ERROR_MESSAGE_UTF8_BYTES // 2 + 1),
        " leading whitespace",
        "trailing whitespace ",
        "bad\x00message",
        "\ud800",
    ):
        with pytest.raises(ValidationError):
            ApiError(
                code=QueryErrorCode.LIMIT_EXCEEDED,
                message=message,
                retryable=False,
            )
    with pytest.raises(ValidationError):
        ApiError.model_validate(
            {
                "code": "limit_exceeded",
                "message": "The query limit was exceeded.",
                "retryable": "false",
            }
        )


def test_cursor_is_bounded_but_remains_opaque() -> None:
    opaque = "payload+not-verified==:~"
    assert SymbolPage(items=(), next_cursor=opaque, generation_id=1).next_cursor == opaque

    for cursor in ("", "x" * (MAX_CURSOR_UTF8_BYTES + 1), "bad\x00cursor", "line\n"):
        with pytest.raises(ValidationError, match="cursor"):
            SymbolPage(items=(), next_cursor=cursor, generation_id=1)


def test_page_item_limit_and_generation_consistency_are_enforced() -> None:
    item = symbol()
    assert len(
        SymbolPage(
            items=(item,) * MAX_PAGE_ITEMS,
            generation_id=item.generation_id,
        ).items
    ) == MAX_PAGE_ITEMS

    with pytest.raises(ValidationError, match="at most|page items"):
        SymbolPage(
            items=(item,) * (MAX_PAGE_ITEMS + 1),
            generation_id=item.generation_id,
        )
    with pytest.raises(ValidationError, match="generation"):
        SymbolPage(items=(item,), generation_id=item.generation_id + 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"stable_symbol_id": str(uuid4())},
        {"stable_symbol_id": SYMBOL_ID.upper()},
        {"kind": "Not Canonical"},
        {"name": ""},
        {"qualified_name": "\ud800"},
        {"signature": ""},
        {"start_line": 0},
        {"end_line": 0},
        {"start_line": 3, "end_line": 2},
        {"source_sha256": "A" * 64},
        {"source_sha256": "a" * 63},
        {"generation_id": 0},
        {"generation_id": True},
    ],
)
def test_symbol_item_identity_text_span_and_hash_are_validated(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        symbol(**overrides)


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("name", MAX_NAME_UTF8_BYTES),
        ("qualified_name", MAX_QUALIFIED_NAME_UTF8_BYTES),
        ("signature", MAX_SIGNATURE_UTF8_BYTES),
    ],
)
def test_symbol_item_uses_shared_utf8_byte_caps(field: str, maximum: int) -> None:
    exact = "é" * (maximum // 2)

    assert getattr(symbol(**{field: exact}), field) == exact
    with pytest.raises(ValidationError, match="UTF-8 byte limit"):
        symbol(**{field: exact + "é"})


def test_symbol_path_uses_shared_utf8_byte_cap() -> None:
    exact = "é" * 2047 + "ab"

    assert len(exact.encode("utf-8")) == MAX_RELATIVE_PATH_UTF8_BYTES
    assert symbol(relative_path=exact).relative_path == exact
    with pytest.raises(ValidationError, match="relative POSIX path"):
        symbol(relative_path=exact + "c")


@pytest.mark.parametrize(
    "field", ["name", "qualified_name", "signature", "relative_path"]
)
@pytest.mark.parametrize("value", ["bad\x1ftext", "hidden\u202etext", "\ud800"])
def test_symbol_item_rejects_controls_and_surrogates(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        symbol(**{field: value})


def test_snippet_text_is_utf8_byte_bounded_and_span_is_actual() -> None:
    valid = "é" * (MAX_SNIPPET_UTF8_BYTES // 2)
    snippet = Snippet(
        text=valid,
        start_line=4,
        end_line=5,
        truncated=True,
        source_sha256=SOURCE_HASH,
    )

    assert snippet.text == valid
    for overrides in (
        {"text": "é" * (MAX_SNIPPET_UTF8_BYTES // 2 + 1)},
        {"text": "\ud800"},
        {"start_line": 0},
        {"start_line": 6, "end_line": 5},
        {"source_sha256": "invalid"},
        {"truncated": 1},
    ):
        values: dict[str, Any] = {
            "text": "pass\n",
            "start_line": 4,
            "end_line": 5,
            "truncated": False,
            "source_sha256": SOURCE_HASH,
        }
        values.update(overrides)
        with pytest.raises(ValidationError):
            Snippet(**values)
