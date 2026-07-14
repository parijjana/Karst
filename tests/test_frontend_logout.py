from __future__ import annotations

import pytest

from tests.frontend_dashboard_support import (
    DashboardHTMLParser,
    run_logout_harness,
)

pytest_plugins = ("tests.frontend_dashboard_support",)


def test_logout_waits_for_mutation_and_uses_rotated_csrf(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    result = run_logout_harness(
        dashboard,
        """
        (async () => {
            const states = [];
            const requests = [];
            setAdminSessionState = (authenticated, message) => {
                states.push({ authenticated, message });
            };
            adminCsrfToken = 'before-mutation';
            let releaseMutation;
            adminMutationPromise = new Promise((resolve) => {
                releaseMutation = resolve;
            });
            fetchWithTimeout = async (_url, options) => {
                requests.push(options.headers['X-CSRF-Token']);
                return { status: 204, ok: true };
            };
            const logout = logoutAdminSession();
            await Promise.resolve();
            const requestsBeforeMutation = requests.length;
            adminCsrfToken = 'rotated-by-mutation';
            releaseMutation();
            await logout;
            console.log(JSON.stringify({
                requestsBeforeMutation,
                requests,
                token: adminCsrfToken,
                states
            }));
        })();
        """,
    )

    assert result["requestsBeforeMutation"] == 0
    assert result["requests"] == ["rotated-by-mutation"]
    assert result["token"] is None
    assert result["states"][-1]["authenticated"] is False  # type: ignore[index]


@pytest.mark.parametrize("failure_kind", ["non_204", "network"])
def test_unconfirmed_logout_preserves_session_state(
    dashboard: tuple[str, DashboardHTMLParser], failure_kind: str
) -> None:
    network_behavior = (
        "throw new TypeError('network unavailable');"
        if failure_kind == "network"
        else "return { status: 200, ok: true };"
    )
    result = run_logout_harness(
        dashboard,
        f"""
        (async () => {{
            const states = [];
            setAdminSessionState = (authenticated, message) => {{
                states.push({{ authenticated, message }});
            }};
            adminCsrfToken = 'still-current';
            adminMutationPromise = null;
            fetchWithTimeout = async () => {{ {network_behavior} }};
            if (typeof bootstrapAdminCsrf === 'function') {{
                bootstrapAdminCsrf = async () => false;
            }}
            await logoutAdminSession();
            console.log(JSON.stringify({{
                token: adminCsrfToken,
                states
            }}));
        }})();
        """,
    )

    assert result["token"] == "still-current"
    final_state = result["states"][-1]  # type: ignore[index]
    assert final_state["authenticated"] is True
    assert "unconfirmed" in final_state["message"].lower()


def test_forbidden_logout_rebootstraps_csrf_for_safe_retry(
    dashboard: tuple[str, DashboardHTMLParser],
) -> None:
    result = run_logout_harness(
        dashboard,
        """
        (async () => {
            const states = [];
            setAdminSessionState = (authenticated, message) => {
                states.push({ authenticated, message });
            };
            adminCsrfToken = 'stale';
            adminMutationPromise = null;
            fetchWithTimeout = async () => ({ status: 403, ok: false });
            if (typeof bootstrapAdminCsrf === 'function') {
                bootstrapAdminCsrf = async () => {
                    adminCsrfToken = 'refreshed';
                    return true;
                };
            }
            await logoutAdminSession();
            console.log(JSON.stringify({ token: adminCsrfToken, states }));
        })();
        """,
    )

    assert result["token"] == "refreshed"
    final_state = result["states"][-1]  # type: ignore[index]
    assert final_state["authenticated"] is True
    assert "retry" in final_state["message"].lower()
