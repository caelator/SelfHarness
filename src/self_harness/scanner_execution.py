from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from self_harness.image_policy import ImagePolicyError, validate_image_digest
from self_harness.scanner_db_freshness import (
    ScannerDbFreshnessDecision,
    ScannerDbFreshnessError,
    ScannerDbFreshnessPolicy,
    evaluate_scanner_db_freshness,
    parse_trivy_db_metadata,
    scanner_db_freshness_decision_to_jsonable,
    scanner_db_freshness_error_decision,
)

SCANNER_RUN_SCHEMA_VERSION = "1.0"

ScannerMode = Literal["dry-run", "replay", "live"]
CheckStatus = Literal["pass", "fail", "skipped"]


@dataclass(frozen=True)
class ScannerCommand:
    image: str
    output_path: Path
    digest: str | None = None
    report_format: Literal["json"] = "json"
    db_dir: Path | None = None
    db_registry_config_path: Path | None = None
    db_freshness_policy: ScannerDbFreshnessPolicy | None = None
    db_freshness_evaluated_at: date | None = None
    additional_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScannerPreflightCheck:
    name: str
    status: CheckStatus
    detail: str
    required_for_live: bool = True
    freshness: ScannerDbFreshnessDecision | None = None


@dataclass(frozen=True)
class ScannerPreflightReport:
    schema_version: str
    scanner: str
    passed: bool
    checks: tuple[ScannerPreflightCheck, ...]


@dataclass(frozen=True)
class ScannerRunResult:
    schema_version: str
    scanner: str
    mode: ScannerMode
    ok: bool
    exit_code: int
    report_path: str | None
    command: tuple[str, ...]
    preflight: ScannerPreflightReport | None = None
    reason: str | None = None
    stdout: str = ""
    stderr: str = ""


class ScannerExecutionError(RuntimeError):
    """Raised when scanner command construction or execution cannot continue."""


def build_trivy_command(command: ScannerCommand, *, trivy_binary: str = "trivy") -> list[str]:
    _validate_scanner_command(command)
    argv = [
        trivy_binary,
        "image",
        "--format",
        command.report_format,
        "--output",
        str(command.output_path),
    ]
    if command.db_dir is not None:
        argv.extend(["--cache-dir", str(command.db_dir)])
    if command.db_registry_config_path is not None:
        argv.extend(["--registry-config", str(command.db_registry_config_path.resolve())])
    argv.extend(command.additional_args)
    argv.append(_image_reference(command.image, command.digest))
    return argv


def preflight_scanner(
    command: ScannerCommand,
    *,
    trivy_binary: str = "trivy",
    require_trivy: bool = True,
) -> ScannerPreflightReport:
    checks = (
        _executable_check(trivy_binary, required=require_trivy),
        _db_metadata_check(
            command.db_dir,
            policy=command.db_freshness_policy,
            evaluated_at=command.db_freshness_evaluated_at,
        ),
        _registry_config_check(command.db_registry_config_path),
    )
    passed = all(check.status == "pass" for check in checks if check.required_for_live)
    return ScannerPreflightReport(
        schema_version=SCANNER_RUN_SCHEMA_VERSION,
        scanner="trivy",
        passed=passed,
        checks=checks,
    )


def run_scanner(
    command: ScannerCommand,
    *,
    dry_run: bool = False,
    replay_report: Path | None = None,
    trivy_binary: str = "trivy",
    timeout_seconds: int = 300,
) -> ScannerRunResult:
    if dry_run and replay_report is not None:
        raise ScannerExecutionError("--dry-run and --replay are mutually exclusive")
    if timeout_seconds <= 0:
        raise ScannerExecutionError("scanner timeout must be positive")
    argv = tuple(build_trivy_command(command, trivy_binary=trivy_binary))
    if dry_run:
        return _result(mode="dry-run", ok=True, exit_code=0, command=argv, report_path=None)
    if replay_report is not None:
        preflight = None
        if command.db_dir is not None or command.db_freshness_policy is not None:
            preflight = preflight_scanner(command, trivy_binary=trivy_binary, require_trivy=False)
            if not preflight.passed:
                return _result(
                    mode="replay",
                    ok=False,
                    exit_code=2,
                    command=argv,
                    report_path=None,
                    preflight=preflight,
                    reason="scanner-preflight-failed",
                )
        return _replay_scanner_report(command, replay_report, argv, preflight=preflight)

    preflight = preflight_scanner(command, trivy_binary=trivy_binary)
    if not preflight.passed:
        return _result(
            mode="live",
            ok=False,
            exit_code=2,
            command=argv,
            report_path=None,
            preflight=preflight,
            reason="scanner-preflight-failed",
        )
    command.output_path.parent.mkdir(parents=True, exist_ok=True)
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
            report_path=None,
            preflight=preflight,
            reason=str(exc),
        )
    return _result(
        mode="live",
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        command=argv,
        report_path=str(command.output_path.resolve()) if completed.returncode == 0 else None,
        preflight=preflight,
        reason=None if completed.returncode == 0 else "scanner-exit-nonzero",
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def scanner_run_result_to_jsonable(result: ScannerRunResult) -> dict[str, object]:
    return {
        "schema_version": result.schema_version,
        "scanner": result.scanner,
        "mode": result.mode,
        "ok": result.ok,
        "exit_code": result.exit_code,
        "report_path": result.report_path,
        "command": list(result.command),
        "preflight": _preflight_to_jsonable(result.preflight),
        "reason": result.reason,
        "stdout": _trim(result.stdout),
        "stderr": _trim(result.stderr),
    }


def _replay_scanner_report(
    command: ScannerCommand,
    replay_report: Path,
    argv: tuple[str, ...],
    *,
    preflight: ScannerPreflightReport | None = None,
) -> ScannerRunResult:
    if not replay_report.is_file():
        raise ScannerExecutionError(f"replay report does not exist: {replay_report}")
    command.output_path.parent.mkdir(parents=True, exist_ok=True)
    if replay_report.resolve() != command.output_path.resolve():
        shutil.copyfile(replay_report, command.output_path)
    return _result(
        mode="replay",
        ok=True,
        exit_code=0,
        command=argv,
        report_path=str(command.output_path.resolve()),
        preflight=preflight,
    )


def _validate_scanner_command(command: ScannerCommand) -> None:
    if not command.image:
        raise ScannerExecutionError("scanner image must be non-empty")
    if command.report_format != "json":
        raise ScannerExecutionError("only Trivy JSON reports are supported")
    if command.digest is not None:
        try:
            validate_image_digest(command.digest)
        except ImagePolicyError as exc:
            raise ScannerExecutionError(exc.decision.message) from exc
        if "@" in command.image:
            raise ScannerExecutionError("scanner image must not include a digest when --digest is supplied")
    if command.db_registry_config_path is not None and not str(command.db_registry_config_path):
        raise ScannerExecutionError("scanner DB registry config path must be non-empty")
    for item in command.additional_args:
        if not item:
            raise ScannerExecutionError("scanner additional args must be non-empty")


def _image_reference(image: str, digest: str | None) -> str:
    if digest is None:
        return image
    return f"{image}@{digest}"


def _executable_check(executable: str, *, required: bool) -> ScannerPreflightCheck:
    if not required:
        return ScannerPreflightCheck(
            name="trivy_present",
            status="skipped",
            detail="not required",
            required_for_live=False,
        )
    found = shutil.which(executable)
    if found is None:
        return ScannerPreflightCheck(
            name="trivy_present",
            status="fail",
            detail=f"missing executable: {executable}",
        )
    return ScannerPreflightCheck(name="trivy_present", status="pass", detail=found)


def _db_metadata_check(
    db_dir: Path | None,
    *,
    policy: ScannerDbFreshnessPolicy | None,
    evaluated_at: date | None,
) -> ScannerPreflightCheck:
    if db_dir is None:
        if policy is not None:
            freshness = scanner_db_freshness_error_decision(
                policy,
                code="missing-metadata",
                message="scanner DB freshness policy requires --db-dir",
                source_path=None,
                evaluated_at=evaluated_at,
            )
            return ScannerPreflightCheck(
                name="trivy_db_metadata_present",
                status="fail",
                detail=f"{freshness.code}: {freshness.message}",
                freshness=freshness,
            )
        return ScannerPreflightCheck(
            name="trivy_db_metadata_present",
            status="skipped",
            detail="no scanner DB cache requested",
            required_for_live=False,
        )
    candidates = (db_dir / "metadata.json", db_dir / "db" / "metadata.json")
    for candidate in candidates:
        if candidate.is_file():
            if policy is not None:
                freshness = _evaluate_db_freshness(candidate, policy=policy, evaluated_at=evaluated_at)
                return ScannerPreflightCheck(
                    name="trivy_db_metadata_present",
                    status="pass" if freshness.allowed else "fail",
                    detail=f"{freshness.code}: {freshness.message}",
                    freshness=freshness,
                )
            return ScannerPreflightCheck(
                name="trivy_db_metadata_present",
                status="pass",
                detail=str(candidate),
            )
    missing_freshness: ScannerDbFreshnessDecision | None = None
    if policy is not None:
        missing_freshness = scanner_db_freshness_error_decision(
            policy,
            code="missing-metadata",
            message=f"missing Trivy DB metadata under {db_dir}",
            source_path=str(db_dir),
            evaluated_at=evaluated_at,
        )
    detail = (
        f"{missing_freshness.code}: {missing_freshness.message}"
        if missing_freshness is not None
        else f"missing Trivy DB metadata under {db_dir}"
    )
    return ScannerPreflightCheck(
        name="trivy_db_metadata_present",
        status="fail",
        detail=detail,
        freshness=missing_freshness,
    )


def _registry_config_check(path: Path | None) -> ScannerPreflightCheck:
    if path is None:
        return ScannerPreflightCheck(
            name="trivy_registry_config_present",
            status="skipped",
            detail="no scanner DB registry config requested",
            required_for_live=False,
        )
    if path.is_file():
        return ScannerPreflightCheck(
            name="trivy_registry_config_present",
            status="pass",
            detail=str(path.resolve()),
        )
    return ScannerPreflightCheck(
        name="trivy_registry_config_present",
        status="fail",
        detail="missing scanner DB registry config file",
    )


def _evaluate_db_freshness(
    metadata_path: Path,
    *,
    policy: ScannerDbFreshnessPolicy,
    evaluated_at: date | None,
) -> ScannerDbFreshnessDecision:
    try:
        metadata = parse_trivy_db_metadata(metadata_path)
    except ScannerDbFreshnessError as exc:
        return scanner_db_freshness_error_decision(
            policy,
            code="malformed-metadata",
            message=str(exc),
            source_path=str(metadata_path),
            evaluated_at=evaluated_at,
        )
    return evaluate_scanner_db_freshness(metadata, policy, evaluated_at=evaluated_at)


def _preflight_to_jsonable(report: ScannerPreflightReport | None) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "schema_version": report.schema_version,
        "scanner": report.scanner,
        "passed": report.passed,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "required_for_live": check.required_for_live,
                "freshness": (
                    scanner_db_freshness_decision_to_jsonable(check.freshness)
                    if check.freshness is not None
                    else None
                ),
            }
            for check in report.checks
        ],
    }


def _result(
    *,
    mode: ScannerMode,
    ok: bool,
    exit_code: int,
    command: tuple[str, ...],
    report_path: str | None,
    preflight: ScannerPreflightReport | None = None,
    reason: str | None = None,
    stdout: str = "",
    stderr: str = "",
) -> ScannerRunResult:
    return ScannerRunResult(
        schema_version=SCANNER_RUN_SCHEMA_VERSION,
        scanner="trivy",
        mode=mode,
        ok=ok,
        exit_code=exit_code,
        report_path=report_path,
        command=command,
        preflight=preflight,
        reason=reason,
        stdout=stdout,
        stderr=stderr,
    )


def _trim(value: str, limit: int = 2000) -> str:
    text = value.replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"
