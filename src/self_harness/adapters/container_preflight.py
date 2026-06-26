from __future__ import annotations

import shutil
import subprocess

from self_harness.adapters.terminal_bench.preflight import PreflightCheck, PreflightReport


def run_container_preflight(
    image: str,
    *,
    docker_executable: str = "docker",
    require_daemon: bool = True,
    require_image_present: bool = False,
) -> PreflightReport:
    checks = [
        _docker_cli_check(docker_executable),
        _docker_daemon_check(docker_executable, required=require_daemon),
        _image_present_check(docker_executable, image, required=require_image_present),
    ]
    passed = all(check.status == "pass" for check in checks if check.required_for_live)
    return PreflightReport(schema_version="1.0", dataset="container-verifier", passed=passed, checks=checks)


def _docker_cli_check(executable: str) -> PreflightCheck:
    found = shutil.which(executable)
    if found is None:
        return PreflightCheck(name="docker_cli_present", status="fail", detail=f"missing executable: {executable}")
    return PreflightCheck(name="docker_cli_present", status="pass", detail=found)


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


def _image_present_check(executable: str, image: str, *, required: bool) -> PreflightCheck:
    if not required:
        return PreflightCheck(
            name="container_image_present",
            status="skipped",
            detail="not required",
            required_for_live=False,
        )
    if shutil.which(executable) is None:
        return PreflightCheck(name="container_image_present", status="fail", detail=f"missing executable: {executable}")
    try:
        completed = subprocess.run(
            [executable, "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PreflightCheck(name="container_image_present", status="fail", detail=str(exc))
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return PreflightCheck(name="container_image_present", status="fail", detail=detail)
    return PreflightCheck(name="container_image_present", status="pass", detail=image)
