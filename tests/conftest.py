"""Shared pytest fixtures and global test isolation.

These tests must be hermetic: they must never read the developer's real ``~/.config/self-harness/
config.json`` or a real ``ZAI_*`` environment variable, or behavior changes depending on whose machine
runs them (e.g. an offline deterministic run could suddenly attempt a live GLM call because a real API
key was found on disk). The autouse fixture below redirects the user-config directory to a temp path and
clears the GLM environment variables for every test. Individual tests that want a key set it explicitly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_root = tmp_path_factory.mktemp("xdg-config")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_root))
    for var in ("ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
