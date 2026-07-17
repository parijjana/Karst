from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.karst_core.indexing.identity import _require_uuid5
from src.text_contracts import (
    MAX_KIND_UTF8_BYTES,
    MAX_NAME_UTF8_BYTES,
    MAX_QUALIFIED_NAME_UTF8_BYTES,
    MAX_RELATIVE_PATH_UTF8_BYTES,
    MAX_SIGNATURE_UTF8_BYTES,
    MAX_SNIPPET_UTF8_BYTES,
    require_kind,
    require_name,
    require_qualified_name,
    require_relative_posix_path,
    require_signature,
    require_snippet_text,
    require_utf8_text,
)


__all__ = [
    "MAX_CURSOR_UTF8_BYTES", "MAX_ERROR_MESSAGE_UTF8_BYTES", "MAX_PAGE_ITEMS",
    "MAX_SNIPPET_UTF8_BYTES", "ApiError", "QueryErrorCode", "Snippet",
    "SnippetEnvelope", "SnippetError", "SnippetSuccess", "SymbolPage",
    "SymbolPageEnvelope", "SymbolPageError", "SymbolPageSuccess", "SymbolRef",
]


MAX_CURSOR_UTF8_BYTES = 4096
MAX_ERROR_MESSAGE_UTF8_BYTES = 1024
MAX_PAGE_ITEMS = 200
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_UUID5_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DRIVE_PREFIX_PATTERN = re.compile(r"(?i)(?:^|[^a-z0-9_])[a-z]:")


def _safe_message(value: str) -> str:
    message = require_utf8_text(
        value, "message", MAX_ERROR_MESSAGE_UTF8_BYTES
    )
    if (
        "/" in message
        or "\\" in message
        or "file:" in message.casefold()
        or _DRIVE_PREFIX_PATTERN.search(message) is not None
    ):
        raise ValueError("message must not contain path data.")
    return message


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QueryErrorCode(str, Enum):
    PROJECT_NOT_FOUND = "project_not_found"
    INDEX_NOT_READY = "index_not_ready"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    AMBIGUOUS_SYMBOL = "ambiguous_symbol"
    INVALID_CURSOR = "invalid_cursor"
    STALE_CURSOR = "stale_cursor"
    SOURCE_STALE = "source_stale"
    LIMIT_EXCEEDED = "limit_exceeded"


class ApiError(_ContractModel):
    code: QueryErrorCode
    message: str = Field(
        min_length=1,
        max_length=MAX_ERROR_MESSAGE_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_ERROR_MESSAGE_UTF8_BYTES},
    )
    retryable: bool = Field(strict=True)

    @field_validator("message")
    @classmethod
    def _message_is_safe(cls, value: str) -> str:
        return _safe_message(value)


class SymbolRef(_ContractModel):
    stable_symbol_id: str = Field(
        min_length=36, max_length=36, pattern=_UUID5_PATTERN
    )
    kind: str = Field(
        min_length=1,
        max_length=MAX_KIND_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_KIND_UTF8_BYTES},
    )
    name: str = Field(
        min_length=1,
        max_length=MAX_NAME_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_NAME_UTF8_BYTES},
    )
    qualified_name: str = Field(
        min_length=1,
        max_length=MAX_QUALIFIED_NAME_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_QUALIFIED_NAME_UTF8_BYTES},
    )
    signature: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SIGNATURE_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_SIGNATURE_UTF8_BYTES},
    )
    relative_path: str = Field(
        min_length=1,
        max_length=MAX_RELATIVE_PATH_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_RELATIVE_PATH_UTF8_BYTES},
    )
    start_line: int = Field(gt=0, strict=True)
    end_line: int = Field(gt=0, strict=True)
    source_sha256: str = Field(
        min_length=64, max_length=64, pattern=_SHA256_PATTERN
    )
    generation_id: int = Field(gt=0, strict=True)

    @field_validator("stable_symbol_id")
    @classmethod
    def _stable_id_is_uuid5(cls, value: str) -> str:
        return _require_uuid5(value, "stable_symbol_id")

    @field_validator("kind")
    @classmethod
    def _kind_is_canonical(cls, value: str) -> str:
        return require_kind(value)

    @field_validator("name")
    @classmethod
    def _name_is_bounded(cls, value: str) -> str:
        return require_name(value)

    @field_validator("qualified_name")
    @classmethod
    def _qualified_name_is_bounded(cls, value: str) -> str:
        return require_qualified_name(value)

    @field_validator("signature")
    @classmethod
    def _signature_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_signature(value)

    @field_validator("relative_path")
    @classmethod
    def _path_is_project_relative(cls, value: str) -> str:
        return require_relative_posix_path(value)

    @model_validator(mode="after")
    def _span_is_ordered(self) -> SymbolRef:
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line.")
        return self


class SymbolPage(_ContractModel):
    items: tuple[SymbolRef, ...] = Field(max_length=MAX_PAGE_ITEMS)
    next_cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CURSOR_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_CURSOR_UTF8_BYTES},
    )
    generation_id: int = Field(gt=0, strict=True)

    @field_validator("items", mode="before")
    @classmethod
    def _own_items(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("page items must be a list or tuple.")
        return tuple(item for item in value)

    @field_validator("items")
    @classmethod
    def _items_are_bounded(
        cls, value: tuple[SymbolRef, ...]
    ) -> tuple[SymbolRef, ...]:
        if len(value) > MAX_PAGE_ITEMS:
            raise ValueError("page items exceed the item limit.")
        return value

    @field_validator("next_cursor")
    @classmethod
    def _cursor_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_utf8_text(value, "cursor", MAX_CURSOR_UTF8_BYTES)

    @model_validator(mode="after")
    def _generation_is_consistent(self) -> SymbolPage:
        if any(item.generation_id != self.generation_id for item in self.items):
            raise ValueError("page items must belong to the page generation.")
        return self


class Snippet(_ContractModel):
    text: str = Field(
        max_length=MAX_SNIPPET_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_SNIPPET_UTF8_BYTES},
    )
    start_line: int = Field(gt=0, strict=True)
    end_line: int = Field(gt=0, strict=True)
    truncated: bool = Field(strict=True)
    source_sha256: str = Field(
        min_length=64, max_length=64, pattern=_SHA256_PATTERN
    )

    @field_validator("text")
    @classmethod
    def _text_is_bounded_utf8(cls, value: str) -> str:
        return require_snippet_text(value)

    @model_validator(mode="after")
    def _span_is_ordered(self) -> Snippet:
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line.")
        return self


class _V1Envelope(_ContractModel):
    schema_version: Literal["v1"] = "v1"


class SymbolPageSuccess(_V1Envelope):
    status: Literal["success"] = "success"
    data: SymbolPage


class SymbolPageError(_V1Envelope):
    status: Literal["error"] = "error"
    error: ApiError


class SnippetSuccess(_V1Envelope):
    status: Literal["success"] = "success"
    data: Snippet


class SnippetError(_V1Envelope):
    status: Literal["error"] = "error"
    error: ApiError


SymbolPageEnvelope = Annotated[
    Union[SymbolPageSuccess, SymbolPageError], Field(discriminator="status")
]
SnippetEnvelope = Annotated[
    Union[SnippetSuccess, SnippetError], Field(discriminator="status")
]
