from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import TypeAdapter, ValidationError

from src import query_models
from src.query_models import (
    ApiError,
    QueryErrorCode,
    Snippet,
    SnippetEnvelope,
    SnippetError,
    SnippetSuccess,
    SymbolPage,
    SymbolPageEnvelope,
    SymbolPageError,
    SymbolPageSuccess,
    SymbolRef,
)
from src.text_contracts import (
    MAX_KIND_UTF8_BYTES,
    MAX_NAME_UTF8_BYTES,
    MAX_QUALIFIED_NAME_UTF8_BYTES,
    MAX_RELATIVE_PATH_UTF8_BYTES,
    MAX_SIGNATURE_UTF8_BYTES,
    MAX_SNIPPET_UTF8_BYTES,
)


SYMBOL_ID = str(uuid5(NAMESPACE_URL, "symbol:example.Example"))
SOURCE_HASH = "a" * 64


def symbol(**overrides: Any) -> SymbolRef:
    values: dict[str, Any] = {
        "stable_symbol_id": SYMBOL_ID,
        "kind": "class",
        "name": "Example",
        "qualified_name": "example.Example",
        "signature": "class Example",
        "relative_path": "src/example.py",
        "start_line": 2,
        "end_line": 8,
        "source_sha256": SOURCE_HASH,
        "generation_id": 7,
    }
    values.update(overrides)
    return SymbolRef(**values)


def test_public_v1_contract_and_error_codes_are_explicit() -> None:
    assert set(query_models.__all__) >= {
        "ApiError",
        "QueryErrorCode",
        "Snippet",
        "SnippetEnvelope",
        "SnippetError",
        "SnippetSuccess",
        "SymbolPage",
        "SymbolPageEnvelope",
        "SymbolPageError",
        "SymbolPageSuccess",
        "SymbolRef",
    }
    assert {code.value for code in QueryErrorCode} == {
        "project_not_found",
        "index_not_ready",
        "symbol_not_found",
        "ambiguous_symbol",
        "invalid_cursor",
        "stale_cursor",
        "source_stale",
        "limit_exceeded",
    }


def test_symbol_page_owns_an_immutable_tuple_and_generation() -> None:
    original = [symbol()]
    page = SymbolPage(
        items=original,  # type: ignore[arg-type]
        next_cursor="opaque+cursor==",
        generation_id=7,
    )
    original.clear()

    assert page.items == (symbol(),)
    assert isinstance(page.items, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        page.generation_id = 8  # type: ignore[misc]
    with pytest.raises(ValidationError, match="generation"):
        SymbolPage(items=(symbol(generation_id=8),), generation_id=7)


def test_models_serialize_and_round_trip_as_v1_json() -> None:
    page = SymbolPage(items=(symbol(),), next_cursor=None, generation_id=7)
    snippet = Snippet(
        text="class Example:\n    pass\n",
        start_line=2,
        end_line=3,
        truncated=False,
        source_sha256=SOURCE_HASH,
    )
    error = ApiError(
        code=QueryErrorCode.INDEX_NOT_READY,
        message="The project index is not ready.",
        retryable=True,
    )

    assert SymbolPage.model_validate_json(page.model_dump_json()) == page
    assert Snippet.model_validate_json(snippet.model_dump_json()) == snippet
    assert ApiError.model_validate_json(error.model_dump_json()) == error
    assert ApiError.model_validate(
        {
            "code": "index_not_ready",
            "message": "The project index is not ready.",
            "retryable": True,
        }
    ) == error
    assert json.loads(page.model_dump_json())["items"][0]["stable_symbol_id"] == (
        SYMBOL_ID
    )


def test_multibyte_boundary_symbol_round_trips_without_normalization() -> None:
    boundary = symbol(
        name="é" * (MAX_NAME_UTF8_BYTES // 2),
        qualified_name="é" * (MAX_QUALIFIED_NAME_UTF8_BYTES // 2),
        signature="é" * (MAX_SIGNATURE_UTF8_BYTES // 2),
        relative_path="é" * 2047 + "ab",
    )

    assert SymbolRef.model_validate_json(boundary.model_dump_json()) == boundary
    assert len(boundary.relative_path.encode("utf-8")) == (
        MAX_RELATIVE_PATH_UTF8_BYTES
    )


@pytest.mark.parametrize(
    ("adapter", "envelope", "expected_type"),
    [
        (
            TypeAdapter(SymbolPageEnvelope),
            SymbolPageSuccess(
                data=SymbolPage(items=(symbol(),), generation_id=7)
            ),
            SymbolPageSuccess,
        ),
        (
            TypeAdapter(SymbolPageEnvelope),
            SymbolPageError(
                error=ApiError(
                    code=QueryErrorCode.INVALID_CURSOR,
                    message="The cursor is invalid.",
                    retryable=False,
                )
            ),
            SymbolPageError,
        ),
        (
            TypeAdapter(SnippetEnvelope),
            SnippetSuccess(
                data=Snippet(
                    text="pass\n",
                    start_line=3,
                    end_line=3,
                    truncated=True,
                    source_sha256=SOURCE_HASH,
                )
            ),
            SnippetSuccess,
        ),
        (
            TypeAdapter(SnippetEnvelope),
            SnippetError(
                error=ApiError(
                    code=QueryErrorCode.SOURCE_STALE,
                    message="The indexed source is stale.",
                    retryable=True,
                )
            ),
            SnippetError,
        ),
    ],
)
def test_envelopes_are_discriminated_and_round_trip(
    adapter: TypeAdapter[Any], envelope: Any, expected_type: type[Any]
) -> None:
    raw = adapter.dump_json(envelope)
    decoded = adapter.validate_json(raw)

    assert isinstance(decoded, expected_type)
    assert json.loads(raw)["schema_version"] == "v1"


def test_envelope_json_schema_is_machine_discriminated() -> None:
    page_schema = TypeAdapter(SymbolPageEnvelope).json_schema()
    snippet_schema = TypeAdapter(SnippetEnvelope).json_schema()

    assert page_schema["discriminator"]["propertyName"] == "status"
    assert snippet_schema["discriminator"]["propertyName"] == "status"
    assert set(page_schema["discriminator"]["mapping"]) == {
        "success",
        "error",
    }


def test_json_schema_publishes_utf8_byte_caps() -> None:
    properties = SymbolRef.model_json_schema()["properties"]
    snippet = Snippet.model_json_schema()["properties"]["text"]

    assert {
        field: properties[field]["x-max-utf8-bytes"]
        for field in ("kind", "name", "qualified_name", "signature", "relative_path")
    } == {
        "kind": MAX_KIND_UTF8_BYTES,
        "name": MAX_NAME_UTF8_BYTES,
        "qualified_name": MAX_QUALIFIED_NAME_UTF8_BYTES,
        "signature": MAX_SIGNATURE_UTF8_BYTES,
        "relative_path": MAX_RELATIVE_PATH_UTF8_BYTES,
    }
    assert snippet["x-max-utf8-bytes"] == MAX_SNIPPET_UTF8_BYTES


def test_envelope_shape_and_schema_version_cannot_be_ambiguous() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(SymbolPageEnvelope)
    error = {
        "code": "project_not_found",
        "message": "The project was not found.",
        "retryable": False,
    }

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"schema_version": "v1", "status": "success", "error": error}
        )
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"schema_version": "v2", "status": "error", "error": error}
        )
