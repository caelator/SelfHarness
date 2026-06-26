from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from self_harness.types import stable_json_dumps

CheckStatus = Literal["pass", "fail", "skipped"]


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: CheckStatus
    detail: str
    required_for_live: bool = True


@dataclass(frozen=True)
class PreflightReport:
    schema_version: str
    dataset: str
    passed: bool
    checks: list[PreflightCheck]


def run_preflight(
    dataset: str,
    harbor_executable: str = "harbor",
    docker_executable: str = "docker",
    corpus_cache: Path | None = None,
    *,
    require_docker: bool = True,
    require_uv: bool = False,
) -> PreflightReport:
    checks = [
        _executable_check("harbor_present", harbor_executable, required=True),
        _version_check("harbor_version", harbor_executable, required=True),
        _executable_check("docker_cli_present", docker_executable, required=require_docker),
        _docker_daemon_check(docker_executable, required=require_docker),
        _executable_check("uv_present", "uv", required=require_uv),
        _cache_check(corpus_cache),
    ]
    passed = all(check.status == "pass" for check in checks if check.required_for_live)
    return PreflightReport(schema_version="1.0", dataset=dataset, passed=passed, checks=checks)


def write_preflight_report(path: Path, report: PreflightReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(report) + "\n", encoding="utf-8")


def load_preflight_report(path: Path) -> PreflightReport:
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("preflight report must be an object")
    checks_raw = data.get("checks")
    if not isinstance(checks_raw, list):
        raise ValueError("preflight report must include checks")
    checks = []
    for row in checks_raw:
        if not isinstance(row, dict):
            raise ValueError("preflight check must be an object")
        checks.append(
            PreflightCheck(
                name=str(row.get("name", "")),
                status=_status(str(row.get("status", ""))),
                detail=str(row.get("detail", "")),
                required_for_live=row.get("required_for_live") is True,
            )
        )
    return PreflightReport(
        schema_version=str(data.get("schema_version", "")),
        dataset=str(data.get("dataset", "")),
        passed=data.get("passed") is True,
        checks=checks,
    )


def _executable_check(name: str, executable: str, *, required: bool) -> PreflightCheck:
    if not required:
        return PreflightCheck(name=name, status="skipped", detail="not required", required_for_live=False)
    found = shutil.which(executable)
    if found is None:
        return PreflightCheck(name=name, status="fail", detail=f"missing executable: {executable}")
    return PreflightCheck(name=name, status="pass", detail=found)


def _version_check(name: str, executable: str, *, required: bool) -> PreflightCheck:
    if not required:
        return PreflightCheck(name=name, status="skipped", detail="not required", required_for_live=False)
    if shutil.which(executable) is None:
        return PreflightCheck(name=name, status="fail", detail=f"missing executable: {executable}")
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PreflightCheck(name=name, status="fail", detail=str(exc))
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return PreflightCheck(name=name, status="fail", detail=detail)
    return PreflightCheck(name=name, status="pass", detail=(completed.stdout.strip() or "version detected"))


def _docker_daemon_check(executable: str, *, required: bool) -> PreflightCheck:
    if not required:
        return PreflightCheck(
            name="docker_daemon_reachable",
            status="skipped",
            detail="not required",
            required_for_live=False,
        )
    if shutil.which(executable) is None:
        return PreflightCheck(name="docker_daemon_reachable", status="fail", detail=f"missing executable: {executable}")
    try:
        completed = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PreflightCheck(name="docker_daemon_reachable", status="fail", detail=str(exc))
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return PreflightCheck(name="docker_daemon_reachable", status="fail", detail=detail)
    return PreflightCheck(name="docker_daemon_reachable", status="pass", detail=completed.stdout.strip())


def _cache_check(corpus_cache: Path | None) -> PreflightCheck:
    if corpus_cache is None:
        return PreflightCheck(
            name="dataset_cache_writable",
            status="skipped",
            detail="no corpus cache requested",
            required_for_live=False,
        )
    try:
        corpus_cache.mkdir(parents=True, exist_ok=True)
        probe = corpus_cache / ".self-harness-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return PreflightCheck(name="dataset_cache_writable", status="fail", detail=str(exc))
    return PreflightCheck(name="dataset_cache_writable", status="pass", detail=str(corpus_cache))


def _status(value: str) -> CheckStatus:
    if value in {"pass", "fail", "skipped"}:
        return cast(CheckStatus, value)
    raise ValueError(f"invalid preflight status: {value}")
