from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SCANNER_DB_UPDATE_SCHEMA_VERSION = "1.0"

ScannerDbUpdateMode = Literal["dry-run", "live"]


@dataclass(frozen=True)
class ScannerDbUpdateCommand:
    cache_dir: Path
    db_registry_config_path: Path | None = None
    additional_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScannerDbUpdateResult:
    schema_version: str
    scanner: str
    mode: ScannerDbUpdateMode
    ok: bool
    exit_code: int
    command: tuple[str, ...]
    cache_dir: str
    reason: str | None = None
    stdout: str = ""
    stderr: str = ""


class ScannerDbUpdateError(RuntimeError):
    """Raised when scanner DB update command construction cannot continue."""


def build_trivy_db_update_command(
    command: ScannerDbUpdateCommand,
    *,
    trivy_binary: str = "trivy",
) -> list[str]:
    _validate_command(command)
    argv = [
        trivy_binary,
        "image",
        "--cache-dir",
        str(command.cache_dir),
        "--download-db-only",
    ]
    if command.db_registry_config_path is not None:
        argv.extend(["--registry-config", str(command.db_registry_config_path.resolve())])
    argv.extend(command.additional_args)
    return argv


def run_scanner_db_update(
    command: ScannerDbUpdateCommand,
    *,
    dry_run: bool = False,
    trivy_binary: str = "trivy",
    timeout_seconds: int = 300,
) -> ScannerDbUpdateResult:
    if timeout_seconds <= 0:
        raise ScannerDbUpdateError("scanner DB update timeout must be positive")
    argv = tuple(build_trivy_db_update_command(command, trivy_binary=trivy_binary))
    if dry_run:
        return _result(mode="dry-run", ok=True, exit_code=0, command=argv, cache_dir=command.cache_dir)
    missing_registry_config = _missing_registry_config_reason(command.db_registry_config_path)
    if missing_registry_config is not None:
        return _result(
            mode="live",
            ok=False,
            exit_code=2,
            command=argv,
            cache_dir=command.cache_dir,
            reason=missing_registry_config,
        )
    found = shutil.which(trivy_binary)
    if found is None:
        return _result(
            mode="live",
            ok=False,
            exit_code=2,
            command=argv,
            cache_dir=command.cache_dir,
            reason=f"missing executable: {trivy_binary}",
        )
    command.cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            list(argv),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _result(
            mode="live",
            ok=False,
            exit_code=2,
            command=argv,
            cache_dir=command.cache_dir,
            reason=str(exc),
        )
    return _result(
        mode="live",
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        command=argv,
        cache_dir=command.cache_dir,
        reason=None if completed.returncode == 0 else "scanner-db-update-exit-nonzero",
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def scanner_db_update_result_to_jsonable(result: ScannerDbUpdateResult) -> dict[str, object]:
    return {
        "schema_version": result.schema_version,
        "scanner": result.scanner,
        "mode": result.mode,
        "ok": result.ok,
        "exit_code": result.exit_code,
        "command": list(result.command),
        "cache_dir": result.cache_dir,
        "reason": result.reason,
        "stdout": _trim(result.stdout),
        "stderr": _trim(result.stderr),
    }


def _validate_command(command: ScannerDbUpdateCommand) -> None:
    if not str(command.cache_dir):
        raise ScannerDbUpdateError("scanner DB update cache_dir must be non-empty")
    if command.db_registry_config_path is not None and not str(command.db_registry_config_path):
        raise ScannerDbUpdateError("scanner DB registry config path must be non-empty")
    for item in command.additional_args:
        if not item:
            raise ScannerDbUpdateError("scanner DB update additional args must be non-empty")


def _missing_registry_config_reason(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.is_file():
        return "missing scanner DB registry config file"
    return None


def _result(
    *,
    mode: ScannerDbUpdateMode,
    ok: bool,
    exit_code: int,
    command: tuple[str, ...],
    cache_dir: Path,
    reason: str | None = None,
    stdout: str = "",
    stderr: str = "",
) -> ScannerDbUpdateResult:
    return ScannerDbUpdateResult(
        schema_version=SCANNER_DB_UPDATE_SCHEMA_VERSION,
        scanner="trivy",
        mode=mode,
        ok=ok,
        exit_code=exit_code,
        command=command,
        cache_dir=str(cache_dir),
        reason=reason,
        stdout=stdout,
        stderr=stderr,
    )


def _trim(value: str, limit: int = 2000) -> str:
    text = value.replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"
