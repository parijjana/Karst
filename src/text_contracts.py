from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath


__all__ = [
    "MAX_KIND_UTF8_BYTES",
    "MAX_LANGUAGE_UTF8_BYTES",
    "MAX_NAME_UTF8_BYTES",
    "MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES",
    "MAX_QUALIFIED_NAME_UTF8_BYTES",
    "MAX_RELATIVE_PATH_UTF8_BYTES",
    "MAX_SIGNATURE_UTF8_BYTES",
    "MAX_SNIPPET_UTF8_BYTES",
    "require_kind",
    "require_language",
    "require_name",
    "require_overload_discriminator",
    "require_qualified_name",
    "require_relative_posix_path",
    "require_signature",
    "require_snippet_text",
    "require_utf8_text",
]


MAX_KIND_UTF8_BYTES = 64
MAX_LANGUAGE_UTF8_BYTES = 64
MAX_NAME_UTF8_BYTES = 512
MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES = 256
MAX_QUALIFIED_NAME_UTF8_BYTES = 1024
MAX_RELATIVE_PATH_UTF8_BYTES = 4096
MAX_SIGNATURE_UTF8_BYTES = 2048
MAX_SNIPPET_UTF8_BYTES = 65_536
_IDENTITY_TOKEN = re.compile(r"[a-z][a-z0-9_+-]*\Z")
_SOURCE_WHITESPACE_CONTROLS = frozenset({"\t", "\n", "\r"})
_UNSAFE_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def require_utf8_text(
    value: object,
    field: str,
    maximum: int,
    *,
    allow_empty: bool = False,
    allow_outer_whitespace: bool = False,
    allowed_controls: frozenset[str] = frozenset(),
) -> str:
    """Validate a string exactly; never normalize, replace, or truncate it."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must be valid UTF-8 text.") from error
    if len(encoded) > maximum:
        raise ValueError(f"{field} exceeds its UTF-8 byte limit.")
    if not allow_empty and not value:
        raise ValueError(f"{field} must be nonempty.")
    if not allow_outer_whitespace and value != value.strip():
        raise ValueError(f"{field} must not contain outer whitespace.")
    if any(
        unicodedata.category(char) in _UNSAFE_UNICODE_CATEGORIES
        and char not in allowed_controls
        for char in value
    ):
        raise ValueError(f"{field} must not contain control characters.")
    return value


def _require_identity_token(value: object, field: str, maximum: int) -> str:
    try:
        token = require_utf8_text(value, field, maximum)
    except ValueError as error:
        raise ValueError(f"{field} must be a canonical identity token.") from error
    if _IDENTITY_TOKEN.fullmatch(token) is None:
        raise ValueError(f"{field} must be a canonical identity token.")
    return token


def require_kind(value: object) -> str:
    return _require_identity_token(value, "kind", MAX_KIND_UTF8_BYTES)


def require_language(value: object) -> str:
    return _require_identity_token(value, "language", MAX_LANGUAGE_UTF8_BYTES)


def require_name(value: object) -> str:
    return require_utf8_text(value, "name", MAX_NAME_UTF8_BYTES)


def require_qualified_name(value: object) -> str:
    return require_utf8_text(
        value, "qualified_name", MAX_QUALIFIED_NAME_UTF8_BYTES
    )


def require_signature(value: object) -> str:
    return require_utf8_text(value, "signature", MAX_SIGNATURE_UTF8_BYTES)


def require_overload_discriminator(value: object) -> str:
    return require_utf8_text(
        value,
        "overload_discriminator",
        MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES,
    )


def require_relative_posix_path(value: object) -> str:
    error_message = "Path must be a canonical relative POSIX path."
    try:
        path = require_utf8_text(
            value, "relative_path", MAX_RELATIVE_PATH_UTF8_BYTES
        )
    except ValueError as error:
        raise ValueError(error_message) from error
    parsed = PurePosixPath(path)
    if (
        path.casefold().startswith("file:")
        or "\\" in path
        or parsed.is_absolute()
        or bool(PureWindowsPath(path).drive)
        or str(parsed) != path
        or any(part in {".", ".."} for part in parsed.parts)
    ):
        raise ValueError(error_message)
    return path


def require_snippet_text(value: object) -> str:
    return require_utf8_text(
        value,
        "snippet text",
        MAX_SNIPPET_UTF8_BYTES,
        allow_empty=True,
        allow_outer_whitespace=True,
        allowed_controls=_SOURCE_WHITESPACE_CONTROLS,
    )
