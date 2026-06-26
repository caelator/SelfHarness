import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "container_preflight_report.py"


def test_container_preflight_report_offline_skips_daemon_and_image_probe(tmp_path: Path) -> None:
    docker_calls = tmp_path / "docker-calls.txt"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\necho \"$@\" >> {docker_calls}\nexit 0\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | 0o111)
    out = tmp_path / "container-preflight.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "offline",
            "--docker-executable",
            str(docker),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"},
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in report["checks"]}

    assert completed.returncode == 0
    assert report["mode"] == "offline"
    assert report["reproduction_claimed"] is False
    assert checks["docker_cli_present"]["status"] == "pass"
    assert checks["docker_daemon_reachable"]["status"] == "skipped"
    assert checks["docker_daemon_reachable"]["required_for_live"] is False
    assert checks["container_image_present"]["status"] == "skipped"
    assert not docker_calls.exists()


def test_container_preflight_report_live_fails_when_docker_missing(tmp_path: Path) -> None:
    out = tmp_path / "container-preflight.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "live",
            "--docker-executable",
            "__missing_docker_for_self_harness__",
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(out.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert report["mode"] == "live"
    assert report["ok"] is False
