import json
import subprocess
import sys
from pathlib import Path

import pytest

from self_harness.scanner_db_update import (
    ScannerDbUpdateCommand,
    ScannerDbUpdateError,
    build_trivy_db_update_command,
    run_scanner_db_update,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER_DB_UPDATE = REPO_ROOT / "scripts" / "scanner_db_update.py"


def test_trivy_db_update_command_construction_is_stable(tmp_path: Path) -> None:
    registry_config = tmp_path / "registry-config.json"
    command = ScannerDbUpdateCommand(
        cache_dir=tmp_path / "trivy-cache",
        db_registry_config_path=registry_config,
        additional_args=("--db-repository", "registry.example/trivy-db:2"),
    )

    assert build_trivy_db_update_command(command, trivy_binary="trivy") == [
        "trivy",
        "image",
        "--cache-dir",
        str(tmp_path / "trivy-cache"),
        "--download-db-only",
        "--registry-config",
        str(registry_config.resolve()),
        "--db-repository",
        "registry.example/trivy-db:2",
    ]


def test_trivy_db_update_dry_run_does_not_create_cache_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "trivy-cache"
    result = run_scanner_db_update(
        ScannerDbUpdateCommand(cache_dir=cache_dir),
        dry_run=True,
        trivy_binary="__missing_trivy_for_self_harness__",
    )

    assert result.ok
    assert result.mode == "dry-run"
    assert result.command == (
        "__missing_trivy_for_self_harness__",
        "image",
        "--cache-dir",
        str(cache_dir),
        "--download-db-only",
    )
    assert not cache_dir.exists()


def test_trivy_db_update_live_missing_trivy_fails_closed(tmp_path: Path) -> None:
    cache_dir = tmp_path / "trivy-cache"
    result = run_scanner_db_update(
        ScannerDbUpdateCommand(cache_dir=cache_dir),
        trivy_binary="__missing_trivy_for_self_harness__",
    )

    assert not result.ok
    assert result.exit_code == 2
    assert "missing executable" in (result.reason or "")
    assert not cache_dir.exists()


def test_trivy_db_update_live_missing_registry_config_fails_closed(tmp_path: Path) -> None:
    cache_dir = tmp_path / "trivy-cache"
    result = run_scanner_db_update(
        ScannerDbUpdateCommand(
            cache_dir=cache_dir,
            db_registry_config_path=tmp_path / "missing-registry-config.json",
        ),
        trivy_binary="__missing_trivy_for_self_harness__",
    )

    assert not result.ok
    assert result.exit_code == 2
    assert result.reason == "missing scanner DB registry config file"
    assert not cache_dir.exists()


def test_trivy_db_update_rejects_empty_additional_args(tmp_path: Path) -> None:
    with pytest.raises(ScannerDbUpdateError, match="additional args"):
        build_trivy_db_update_command(ScannerDbUpdateCommand(cache_dir=tmp_path, additional_args=("",)))


def test_trivy_db_update_cli_dry_run(tmp_path: Path) -> None:
    registry_config = tmp_path / "registry-config.json"
    completed = _run_cli(
        "--cache-dir",
        str(tmp_path / "trivy-cache"),
        "--db-registry-config",
        str(registry_config),
        "--dry-run",
        "--trivy-binary",
        "__missing_trivy_for_self_harness__",
        "--trivy-arg=--db-repository",
        "--trivy-arg",
        "registry.example/trivy-db:2",
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["mode"] == "dry-run"
    assert report["command"] == [
        "__missing_trivy_for_self_harness__",
        "image",
        "--cache-dir",
        str(tmp_path / "trivy-cache"),
        "--download-db-only",
        "--registry-config",
        str(registry_config.resolve()),
        "--db-repository",
        "registry.example/trivy-db:2",
    ]
    assert "auths" not in completed.stdout


def test_trivy_db_update_cli_live_missing_trivy(tmp_path: Path) -> None:
    completed = _run_cli(
        "--cache-dir",
        str(tmp_path / "trivy-cache"),
        "--trivy-binary",
        "__missing_trivy_for_self_harness__",
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert report["ok"] is False
    assert "missing executable" in report["reason"]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER_DB_UPDATE), *args],
        text=True,
        capture_output=True,
        check=False,
    )
