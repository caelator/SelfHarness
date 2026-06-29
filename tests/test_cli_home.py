from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from self_harness import cli, cli_home, user_config


@pytest.fixture
def cfg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the config dir at a temp location and clear env so resolution is deterministic."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_BASE_URL", raising=False)
    monkeypatch.delenv("ZAI_MODEL", raising=False)
    return tmp_path


# ---- user_config ------------------------------------------------------------------------------------


def test_config_round_trip_and_permissions(cfg_home: Path) -> None:
    c = user_config.load_config()
    c.set("api_key", "sk-secret-value-123")
    c.set("max_steps", "30")  # coerced to int
    c.set("harvest", "false")  # coerced to bool
    path = c.save()
    assert path.is_file()
    # Owner-only permissions on the secret-bearing file.
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    reloaded = user_config.load_config()
    assert reloaded.get("api_key") == "sk-secret-value-123"
    assert reloaded.get("max_steps") == 30
    assert reloaded.get("harvest") is False


def test_config_rejects_unknown_key(cfg_home: Path) -> None:
    c = user_config.load_config()
    with pytest.raises(KeyError):
        c.set("not_a_key", "x")


def test_config_redacts_secret(cfg_home: Path) -> None:
    c = user_config.load_config()
    c.set("api_key", "abcdef0123456789")
    red = c.redacted()
    assert red["api_key"].startswith("abcdef")
    assert "0123456789" not in red["api_key"]  # tail hidden
    assert "chars" in red["api_key"]


def test_resolution_order_env_beats_config(cfg_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = user_config.load_config()
    c.set("api_key", "from-config")
    c.save()
    # With no env var, config is used.
    assert user_config.resolve_api_key() == "from-config"
    # Env var wins.
    monkeypatch.setenv("ZAI_API_KEY", "from-env")
    assert user_config.resolve_api_key() == "from-env"
    # Explicit beats both.
    assert user_config.resolve_api_key("explicit") == "explicit"


def test_resolve_defaults(cfg_home: Path) -> None:
    assert user_config.resolve_base_url() == user_config.DEFAULT_BASE_URL
    assert user_config.resolve_model() == user_config.DEFAULT_MODEL
    assert user_config.resolve_api_key() is None


def test_code_provider_model_effort_resolution(cfg_home: Path) -> None:
    c = user_config.load_config()
    c.set("code_provider", "codex")
    c.set("code_model", "gpt-5.6")
    c.set("code_effort", "xhigh")
    c.save()

    loaded = user_config.load_config()
    assert user_config.resolve_code_provider(config=loaded) == "codex"
    assert user_config.resolve_code_model(provider="codex", config=loaded) == "gpt-5.6"
    assert user_config.resolve_code_effort(provider="codex", config=loaded) == "xhigh"


def test_legacy_model_codex_selects_headless_provider(cfg_home: Path) -> None:
    c = user_config.load_config()
    c.set("model", "codex")
    c.save()

    assert user_config.resolve_code_provider(config=user_config.load_config()) == "codex"


def test_loop_eval_repeats_resolution(cfg_home: Path) -> None:
    from self_harness import cli

    c = user_config.load_config()
    # Default when nothing set.
    assert cli._resolve_eval_repeats(None, c) == cli.DEFAULT_LOOP_EVAL_REPEATS
    # Explicit flag wins and is clamped to >= 1.
    assert cli._resolve_eval_repeats(5, c) == 5
    assert cli._resolve_eval_repeats(0, c) == 1
    # Saved setting is honored when no explicit flag.
    c.set("loop_eval_repeats", "4")
    assert c.get("loop_eval_repeats") == 4  # coerced to int
    assert cli._resolve_eval_repeats(None, c) == 4
    # Explicit still beats saved.
    assert cli._resolve_eval_repeats(2, c) == 2


def test_agentic_session_falls_back_to_config(cfg_home: Path) -> None:
    from self_harness.agentic_session import resolve_zai_api_key
    from self_harness.exceptions import AgenticRunnerError

    # No env, no config -> raises with a helpful message pointing at settings.
    with pytest.raises(AgenticRunnerError, match="settings"):
        resolve_zai_api_key()
    # Saved config is honored.
    c = user_config.load_config()
    c.set("api_key", "cfg-key")
    c.save()
    assert resolve_zai_api_key() == "cfg-key"


def test_agentic_session_env_mapping_is_env_only(cfg_home: Path) -> None:
    # When a caller passes an explicit env mapping, the config file must NOT be consulted
    # (preserves the env-only contract the proposer/UI rely on).
    from self_harness.agentic_session import resolve_zai_api_key
    from self_harness.exceptions import AgenticRunnerError

    c = user_config.load_config()
    c.set("api_key", "cfg-key")
    c.save()
    with pytest.raises(AgenticRunnerError):
        resolve_zai_api_key(env={})  # explicit empty mapping -> no config fallback


# ---- help system ------------------------------------------------------------------------------------


def test_help_overview_default(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_home.print_help(None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "What it is" in out
    assert "Code" in out and "Loop" in out


def test_help_all_topics_render(capsys: pytest.CaptureFixture[str]) -> None:
    for topic in cli_home.HELP_TOPICS:
        assert cli_home.print_help(topic) == 0
        assert capsys.readouterr().out.strip()


def test_help_aliases(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_home.print_help("api-key") == 0
    assert "API key" in capsys.readouterr().out


def test_help_unknown_topic_lists_topics(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_home.print_help("nonsense")
    assert rc == 1
    assert "Available topics" in capsys.readouterr().out


# ---- settings subcommand --------------------------------------------------------------------------


def test_settings_set_get_show_path(cfg_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_home.run_settings(["set", "model", "glm-5.2"]) == 0
    capsys.readouterr()
    assert cli_home.run_settings(["get", "model"]) == 0
    assert capsys.readouterr().out.strip() == "glm-5.2"
    assert cli_home.run_settings(["show"]) == 0
    assert "model" in capsys.readouterr().out
    assert cli_home.run_settings(["path"]) == 0
    assert "config.json" in capsys.readouterr().out


def test_settings_get_api_key_is_masked(cfg_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli_home.run_settings(["set", "api_key", "supersecretvalue999"])
    capsys.readouterr()
    cli_home.run_settings(["get", "api_key"])
    out = capsys.readouterr().out
    assert "supersecretvalue999" not in out
    assert "chars" in out


def test_settings_set_bad_key_errors(cfg_home: Path) -> None:
    assert cli_home.run_settings(["set", "bogus", "1"]) == 2


# ---- top-level dispatch ----------------------------------------------------------------------------


def test_bare_invocation_non_tty_prints_overview(
    cfg_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the non-interactive path so it can't hang on input().
    monkeypatch.setattr(cli_home, "_interactive", lambda: False)
    rc = cli.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "What it is" in out


def test_help_subcommand_dispatch(cfg_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["help", "flywheel"]) == 0
    assert "flywheel" in capsys.readouterr().out.lower()


def test_settings_subcommand_dispatch(cfg_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["settings", "set", "max_steps", "42"]) == 0
    capsys.readouterr()
    assert cli.main(["settings", "get", "max_steps"]) == 0
    assert capsys.readouterr().out.strip() == "42"
    # Persisted to disk where load_config can see it.
    saved = json.loads(user_config.config_path().read_text())
    assert saved["max_steps"] == 42
