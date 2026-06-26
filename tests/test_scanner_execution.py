import json
import subprocess
import sys
from pathlib import Path

from self_harness.scanner_execution import (
    ScannerCommand,
    build_trivy_command,
    preflight_scanner,
    run_scanner,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER_RUN = REPO_ROOT / "scripts" / "scanner_run.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "vuln"
IMAGE = "registry.example/trusted/verifier:1"
VALID_DIGEST = "sha256:" + "c" * 64
OTHER_DIGEST = "sha256:" + "d" * 64


def test_trivy_command_construction_is_stable(tmp_path: Path) -> None:
    registry_config = tmp_path / "registry-config.json"
    command = ScannerCommand(
        image=IMAGE,
        digest=VALID_DIGEST,
        output_path=tmp_path / "scan.json",
        db_dir=tmp_path / "trivy-cache",
        db_registry_config_path=registry_config,
        additional_args=("--severity", "HIGH,CRITICAL"),
    )

    assert build_trivy_command(command, trivy_binary="trivy") == [
        "trivy",
        "image",
        "--format",
        "json",
        "--output",
        str(tmp_path / "scan.json"),
        "--cache-dir",
        str(tmp_path / "trivy-cache"),
        "--registry-config",
        str(registry_config.resolve()),
        "--severity",
        "HIGH,CRITICAL",
        f"{IMAGE}@{VALID_DIGEST}",
    ]


def test_scanner_dry_run_does_not_create_report(tmp_path: Path) -> None:
    output = tmp_path / "scan.json"
    result = run_scanner(
        ScannerCommand(image=IMAGE, digest=VALID_DIGEST, output_path=output),
        dry_run=True,
        trivy_binary="__missing_trivy_for_self_harness__",
    )

    assert result.ok
    assert result.mode == "dry-run"
    assert not output.exists()
    assert result.command[-1] == f"{IMAGE}@{VALID_DIGEST}"


def test_scanner_preflight_checks_optional_db_metadata(tmp_path: Path) -> None:
    db_dir = tmp_path / "trivy-cache"
    metadata = db_dir / "db" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text((FIXTURES / "trivy_db_metadata.json").read_text(encoding="utf-8"), encoding="utf-8")
    fake_trivy = _write_fake_trivy(tmp_path / "trivy")

    report = preflight_scanner(
        ScannerCommand(image=IMAGE, output_path=tmp_path / "scan.json", db_dir=db_dir),
        trivy_binary=str(fake_trivy),
    )

    checks = {check.name: check.status for check in report.checks}
    assert report.passed
    assert checks["trivy_present"] == "pass"
    assert checks["trivy_db_metadata_present"] == "pass"


def test_scanner_replay_cli_evaluates_policies(tmp_path: Path) -> None:
    image_policy = _write_image_policy(tmp_path / "image-policy.json", VALID_DIGEST)
    freshness_policy = _write_freshness_policy(tmp_path / "freshness-policy.json", {"max_age_days": 7})
    scan_report = tmp_path / "scan.json"

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(scan_report),
        "--replay",
        str(FIXTURES / "trivy_fresh_with_timestamp.json"),
        "--image-policy",
        str(image_policy),
        "--freshness-policy",
        str(freshness_policy),
        "--today",
        "2026-06-24",
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert scan_report.exists()
    assert result["ok"] is True
    assert result["scanner"]["mode"] == "replay"
    assert result["vulnerability_report"]["ok"] is True
    assert result["vulnerability_report"]["image_policy"]["allowed"] is True
    assert result["vulnerability_report"]["freshness"]["allowed"] is True


def test_scanner_replay_rejects_image_policy_mismatch(tmp_path: Path) -> None:
    image_policy = _write_image_policy(tmp_path / "image-policy.json", OTHER_DIGEST)

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(tmp_path / "scan.json"),
        "--replay",
        str(FIXTURES / "trivy_fresh_with_timestamp.json"),
        "--image-policy",
        str(image_policy),
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert result["ok"] is False
    assert result["vulnerability_report"]["image_policy"]["allowed"] is False
    assert result["vulnerability_report"]["image_policy"]["code"] == "digest-mismatch"


def test_scanner_replay_rejects_stale_report(tmp_path: Path) -> None:
    freshness_policy = _write_freshness_policy(tmp_path / "freshness-policy.json", {"max_age_days": 7})

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(tmp_path / "scan.json"),
        "--replay",
        str(FIXTURES / "trivy_stale_with_timestamp.json"),
        "--freshness-policy",
        str(freshness_policy),
        "--today",
        "2026-06-24",
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert result["ok"] is False
    assert result["vulnerability_report"]["freshness"]["allowed"] is False
    assert result["vulnerability_report"]["freshness"]["code"] == "stale-report"


def test_scanner_live_missing_trivy_fails_closed_without_report(tmp_path: Path) -> None:
    scan_report = tmp_path / "scan.json"

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(scan_report),
        "--trivy-binary",
        "__missing_trivy_for_self_harness__",
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert not scan_report.exists()
    assert result["ok"] is False
    assert result["scanner"]["reason"] == "scanner-preflight-failed"
    assert result["scanner"]["preflight"]["passed"] is False


def test_scanner_live_missing_registry_config_fails_closed(tmp_path: Path) -> None:
    fake_trivy = _write_fake_trivy(tmp_path / "trivy")

    result = run_scanner(
        ScannerCommand(
            image=IMAGE,
            digest=VALID_DIGEST,
            output_path=tmp_path / "scan.json",
            db_registry_config_path=tmp_path / "missing-registry-config.json",
        ),
        trivy_binary=str(fake_trivy),
    )

    checks = {check.name: check for check in result.preflight.checks} if result.preflight is not None else {}
    assert not result.ok
    assert result.reason == "scanner-preflight-failed"
    assert checks["trivy_registry_config_present"].status == "fail"
    assert checks["trivy_registry_config_present"].detail == "missing scanner DB registry config file"


def test_scanner_dry_run_skips_db_freshness_evaluation(tmp_path: Path) -> None:
    output = tmp_path / "scan.json"

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(output),
        "--dry-run",
        "--db-freshness-policy",
        str(FIXTURES / "scanner_db_freshness_policy.json"),
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert result["scanner"]["preflight"] is None
    assert not output.exists()


def test_scanner_cli_dry_run_accepts_registry_config_without_reading_it(tmp_path: Path) -> None:
    registry_config = tmp_path / "registry-config.json"
    registry_config.write_text('{"auths":{"registry.example":{"auth":"secret"}}}', encoding="utf-8")

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(tmp_path / "scan.json"),
        "--dry-run",
        "--db-registry-config",
        str(registry_config),
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert "--registry-config" in result["scanner"]["command"]
    assert str(registry_config.resolve()) in result["scanner"]["command"]
    assert "secret" not in completed.stdout


def _write_fake_trivy(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_image_policy(path: Path, digest: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "policy_version": "1",
                "entries": [
                    {
                        "image": "registry.example/trusted/verifier",
                        "digest": digest,
                        "status": "active",
                        "labels": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_freshness_policy(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps({"policy_version": "1", **payload}), encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER_RUN), *args],
        text=True,
        capture_output=True,
        check=False,
    )
