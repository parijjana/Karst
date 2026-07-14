from __future__ import annotations

from collections.abc import Callable

import pytest

from src.text_contracts import (
    MAX_KIND_UTF8_BYTES,
    MAX_LANGUAGE_UTF8_BYTES,
    MAX_NAME_UTF8_BYTES,
    MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES,
    MAX_QUALIFIED_NAME_UTF8_BYTES,
    MAX_RELATIVE_PATH_UTF8_BYTES,
    MAX_SIGNATURE_UTF8_BYTES,
    MAX_SNIPPET_UTF8_BYTES,
    require_kind,
    require_language,
    require_name,
    require_overload_discriminator,
    require_qualified_name,
    require_relative_posix_path,
    require_signature,
    require_snippet_text,
)


def _multibyte_boundary(maximum: int) -> tuple[str, str]:
    exact = "é" * (maximum // 2)
    return exact, exact + "é"


@pytest.mark.parametrize(
    ("validator", "maximum"),
    [
        (require_name, MAX_NAME_UTF8_BYTES),
        (require_qualified_name, MAX_QUALIFIED_NAME_UTF8_BYTES),
        (require_signature, MAX_SIGNATURE_UTF8_BYTES),
        (
            require_overload_discriminator,
            MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES,
        ),
    ],
)
def test_identity_text_caps_are_exact_utf8_bytes_without_truncation(
    validator: Callable[[object], str], maximum: int
) -> None:
    exact, oversized = _multibyte_boundary(maximum)

    assert validator(exact) == exact
    assert len(validator(exact).encode("utf-8")) == maximum
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        validator(oversized)


def test_relative_path_cap_is_exact_utf8_bytes_and_canonical() -> None:
    exact = "é" * 2047 + "ab"
    oversized = exact + "c"

    assert len(exact.encode("utf-8")) == MAX_RELATIVE_PATH_UTF8_BYTES
    assert require_relative_posix_path(exact) == exact
    with pytest.raises(ValueError, match="relative POSIX path"):
        require_relative_posix_path(oversized)


@pytest.mark.parametrize(
    ("validator", "maximum"),
    [
        (require_kind, MAX_KIND_UTF8_BYTES),
        (require_language, MAX_LANGUAGE_UTF8_BYTES),
    ],
)
def test_identity_tokens_have_explicit_ascii_byte_caps(
    validator: Callable[[object], str], maximum: int
) -> None:
    exact = "a" + "x" * (maximum - 1)

    assert validator(exact) == exact
    with pytest.raises(ValueError, match="canonical identity token"):
        validator(exact + "x")


@pytest.mark.parametrize(
    "value",
    [
        "line\nbreak",
        "tab\tvalue",
        "hidden\u202evalue",
        "line\u2028separator",
        "\ud800",
    ],
)
@pytest.mark.parametrize(
    "validator",
    [
        require_name,
        require_qualified_name,
        require_signature,
        require_overload_discriminator,
    ],
)
def test_query_ready_identity_text_rejects_controls_and_surrogates(
    validator: Callable[[object], str], value: str
) -> None:
    with pytest.raises(ValueError):
        validator(value)


@pytest.mark.parametrize(
    "value", ["src/line\nbreak.py", "src/hidden\u202efile.py", "src/\ud800.py"]
)
def test_relative_path_rejects_controls_and_surrogates(value: str) -> None:
    with pytest.raises(ValueError, match="relative POSIX path"):
        require_relative_posix_path(value)


def test_snippet_is_utf8_bounded_and_allows_only_source_whitespace_controls() -> None:
    exact, oversized = _multibyte_boundary(MAX_SNIPPET_UTF8_BYTES)

    assert require_snippet_text(exact) == exact
    assert require_snippet_text("line one\n\tline two\r\n") == (
        "line one\n\tline two\r\n"
    )
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        require_snippet_text(oversized)
    for value in (
        "bad\x00source",
        "bad\x1fsource",
        "hidden\u202esource",
        "line\u2028separator",
        "\ud800",
    ):
        with pytest.raises(ValueError):
            require_snippet_text(value)


def test_caps_are_frozen_to_the_query_ready_contract() -> None:
    assert (
        MAX_RELATIVE_PATH_UTF8_BYTES,
        MAX_NAME_UTF8_BYTES,
        MAX_QUALIFIED_NAME_UTF8_BYTES,
        MAX_SIGNATURE_UTF8_BYTES,
        MAX_KIND_UTF8_BYTES,
        MAX_LANGUAGE_UTF8_BYTES,
        MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES,
    ) == (4096, 512, 1024, 2048, 64, 64, 256)
