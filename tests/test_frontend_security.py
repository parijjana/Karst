from __future__ import annotations

import base64
import hashlib
import re
import shutil
import subprocess

import pytest

from tests.frontend_dashboard_support import DashboardHTMLParser

pytest_plugins = ("tests.frontend_dashboard_support",)


def test_dashboard_has_no_remote_or_mutable_script_dependencies(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    source, parser = dashboard

    assert parser.scripts
    assert all("src" not in attributes for attributes, _script in parser.scripts)
    assert "ForceGraph" not in source
    assert "new Chart" not in source
    assert "unpkg.com" not in source
    assert "cdn.jsdelivr.net" not in source
    assert "http://" not in source
    assert "https://" not in source


def test_dashboard_has_no_inline_event_handler_attributes(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    _source, parser = dashboard
    event_attributes = [
        (tag, name)
        for tag, name, _value in parser.attributes
        if name.lower().startswith("on")
    ]

    assert event_attributes == []


def test_dashboard_avoids_html_parsing_sinks_for_repository_data(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    source, _parser = dashboard
    forbidden_sinks = (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "DOMParser",
        "createContextualFragment",
    )

    assert all(sink not in source for sink in forbidden_sinks)
    assert "document.createElement" in source
    assert ".textContent" in source


def test_self_contained_csp_blocks_external_capabilities(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    _source, parser = dashboard
    policies = [
        attributes.get("content") or ""
        for attributes in parser.meta
        if (attributes.get("http-equiv") or "").lower()
        == "content-security-policy"
    ]

    assert len(policies) == 1
    policy = policies[0]
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'none'" in policy
    assert "form-action 'none'" in policy
    assert "unsafe-eval" not in policy
    assert "script-src 'unsafe-inline'" not in policy
    assert "http:" not in policy
    assert "https:" not in policy
    expected_hashes = []
    for _attributes, inline_script in parser.scripts:
        digest = base64.b64encode(
            hashlib.sha256(inline_script.encode("utf-8")).digest()
        ).decode("ascii")
        expected_hashes.append(f"'sha256-{digest}'")
    configured_hashes = re.findall(r"'sha256-[A-Za-z0-9+/=]+'", policy)
    assert configured_hashes == expected_hashes


def test_dashboard_does_not_embed_admin_credentials(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    source, _parser = dashboard

    assert "Authorization" not in source
    assert "X-Karst-Admin" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source


def test_dashboard_uses_ephemeral_session_csrf_and_bounded_requests(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    source, _parser = dashboard

    assert 'id="admin-capability"' in source
    assert "/api/admin/session/login" in source
    assert "/api/admin/session/csrf" in source
    assert "adminCsrfToken" in source
    assert "AbortController" in source
    assert "REQUEST_TIMEOUT_MS" in source
    assert "setInterval" not in source
    assert "statusPollPromise" in source
    assert "MAX_TABLE_ROWS" in source
    assert "MAX_TABLE_COLUMNS" in source
    assert "?limit=" in source


def test_inline_javascript_is_syntactically_valid(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    _source, parser = dashboard
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for browser-independent syntax checking")

    script = "\n".join(source for _attributes, source in parser.scripts)
    result = subprocess.run(
        [node, "--check", "-"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
