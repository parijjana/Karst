from __future__ import annotations

from pathlib import Path

import pytest

from src.core_settings import CoreSettings
from src.settings import Settings, SettingsError


def test_core_settings_ignore_dashboard_only_environment_values(tmp_path: Path) -> None:
    environment = {
        "KARST_ALLOWED_ROOTS": str(tmp_path),
        "KARST_DASHBOARD_PORT": "not-a-port",
        "KARST_ALLOW_REMOTE_DASHBOARD": "not-a-boolean",
    }

    core = CoreSettings.from_env(environment)

    assert core.allowed_roots == (tmp_path.resolve(),)
    with pytest.raises(SettingsError, match="Dashboard port is invalid"):
        Settings.from_env(environment)
