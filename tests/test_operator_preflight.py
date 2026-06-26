import json
import subprocess
import sys
from pathlib import Path

OPERATOR_PREFLIGHT = Path("scripts/operator_preflight.py")
FIXTURE_BUNDLE = Path("tests/fixtures/operator_bundle/valid.json")
REGISTRY_CONFIG = Path("tests/fixtures/vuln/trivy_registry_config.json")
HARBOR_REPLAY = Path("tests/fixtures/harbor/harbor_artifact_valid.json")


def test_operator_preflight_fixture_bundle_passes_offline() -> None:
    completed = _run_preflight(
        "--bundle",
        str(FIXTURE_BUNDLE),
        "--today",
        "2026-06-24",
        "--db-registry-config",
        str(REGISTRY_CONFIG),
        "--harbor-url",
        "https://harbor.example",
        "--harbor-project",
        "terminal-bench",
        "--harbor-repository",
        "agents/verifier",
        "--harbor-reference",
        "stable",
        "--harbor-replay",
        str(HARBOR_REPLAY),
    )
    report = json.loads(completed.stdout)
    checks = {check["name"]: check for check in report["checks"]}

    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["bundle"]["owner"] == "self-harness-tests"
    assert checks["image_policy"]["status"] == "pass"
    assert checks["freshness_policy"]["status"] == "pass"
    assert checks["vulnerability_policy"]["status"] == "pass"
    assert checks["scanner_db_freshness_policy"]["status"] == "pass"
    assert checks["trusted_public_key_0"]["status"] == "pass"
    assert checks["scanner_dry_run_command"]["status"] == "pass"
    assert checks["scanner_db_update_dry_run_command"]["status"] == "pass"
    assert checks["scanner_db_registry_config_path"]["status"] == "pass"
    assert checks["harbor_discovery_offline"]["status"] == "pass"
    assert "auths" not in completed.stdout
    assert "benchmark reproduction" in report["boundary"]


def test_operator_preflight_missing_registry_config_fails() -> None:
    completed = _run_preflight(
        "--bundle",
        str(FIXTURE_BUNDLE),
        "--today",
        "2026-06-24",
        "--db-registry-config",
        "tests/fixtures/vuln/missing-registry-config.json",
    )
    report = json.loads(completed.stdout)
    checks = {check["name"]: check for check in report["checks"]}

    assert completed.returncode == 2
    assert report["ok"] is False
    assert checks["scanner_db_registry_config_path"]["status"] == "fail"


def test_operator_preflight_reports_policy_parse_failure(tmp_path: Path) -> None:
    bad_policy = tmp_path / "bad-image-policy.json"
    bundle = tmp_path / "bundle.json"
    bad_policy.write_text('{"policy_version":"9","entries":[]}', encoding="utf-8")
    bundle.write_text(
        json.dumps(
            {
                "bundle_version": "1",
                "owner": "tests",
                "expires_on": "2026-12-31",
                "image_policy": "bad-image-policy.json",
            }
        ),
        encoding="utf-8",
    )

    completed = _run_preflight("--bundle", str(bundle), "--today", "2026-06-24")
    report = json.loads(completed.stdout)
    checks = {check["name"]: check for check in report["checks"]}

    assert completed.returncode == 2
    assert report["ok"] is False
    assert checks["image_policy"]["status"] == "fail"
    assert "unsupported image policy version" in checks["image_policy"]["detail"]


def _run_preflight(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(OPERATOR_PREFLIGHT), *args],
        text=True,
        capture_output=True,
        check=False,
    )
