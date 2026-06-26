import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from self_harness.scanner_db_freshness import (
    ScannerDbFreshnessError,
    evaluate_scanner_db_freshness,
    load_scanner_db_freshness_policy,
    parse_trivy_db_metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER_RUN = REPO_ROOT / "scripts" / "scanner_run.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "vuln"
IMAGE = "registry.example/trusted/verifier:1"
VALID_DIGEST = "sha256:" + "c" * 64


def test_parse_trivy_db_metadata_reads_standard_dates() -> None:
    metadata = parse_trivy_db_metadata(FIXTURES / "trivy_db_metadata.json")

    assert metadata.version == 2
    assert metadata.next_update == date(2026, 6, 25)
    assert metadata.updated_at == date(2026, 6, 24)


def test_scanner_db_freshness_allows_current_metadata(tmp_path: Path) -> None:
    policy = load_scanner_db_freshness_policy(_write_policy(tmp_path / "policy.json", {"max_age_days": 7}))
    metadata = parse_trivy_db_metadata(FIXTURES / "trivy_db_metadata.json")

    decision = evaluate_scanner_db_freshness(policy=policy, metadata=metadata, evaluated_at=date(2026, 6, 24))

    assert decision.allowed
    assert decision.code == "allowed"
    assert decision.age_days == 0


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {"Version": 2, "NextUpdate": "2026-06-23T00:00:00Z", "UpdatedAt": "2026-06-20T00:00:00Z"},
            "stale-next-update",
        ),
        ({"Version": 2, "NextUpdate": "2026-06-25T00:00:00Z", "UpdatedAt": "2026-06-01T00:00:00Z"}, "stale-updated-at"),
        ({"Version": 2, "UpdatedAt": "2026-06-24T00:00:00Z"}, "missing-next-update"),
        ({"Version": 2, "NextUpdate": "2026-06-25T00:00:00Z"}, "missing-updated-at"),
    ],
)
def test_scanner_db_freshness_rejects_stale_or_missing_fields(
    tmp_path: Path,
    payload: dict[str, object],
    code: str,
) -> None:
    policy = load_scanner_db_freshness_policy(_write_policy(tmp_path / "policy.json", {"max_age_days": 7}))
    metadata = parse_trivy_db_metadata(_write_metadata(tmp_path / "metadata.json", payload))

    decision = evaluate_scanner_db_freshness(policy=policy, metadata=metadata, evaluated_at=date(2026, 6, 24))

    assert not decision.allowed
    assert decision.code == code


def test_scanner_db_freshness_rejects_bad_policy_and_metadata(tmp_path: Path) -> None:
    with pytest.raises(ScannerDbFreshnessError, match="require next_update or max_age_days"):
        load_scanner_db_freshness_policy(
            _write_policy(tmp_path / "vacuous.json", {"require_next_update": False})
        )
    with pytest.raises(ScannerDbFreshnessError, match="Version"):
        parse_trivy_db_metadata(_write_metadata(tmp_path / "bad-version.json", {"Version": "2"}))
    with pytest.raises(ScannerDbFreshnessError, match="NextUpdate"):
        parse_trivy_db_metadata(_write_metadata(tmp_path / "bad-date.json", {"Version": 2, "NextUpdate": "soon"}))


def test_scanner_run_replay_accepts_fresh_db_metadata_without_trivy(tmp_path: Path) -> None:
    db_dir = _write_db_dir(
        tmp_path / "trivy-cache",
        next_update="2026-06-25T00:00:00Z",
        updated_at="2026-06-24T00:00:00Z",
    )
    policy = _write_policy(tmp_path / "policy.json", {"max_age_days": 7})
    scan_out = tmp_path / "scan.json"

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(scan_out),
        "--replay",
        str(FIXTURES / "trivy_fresh_with_timestamp.json"),
        "--trivy-binary",
        "__missing_trivy_for_self_harness__",
        "--db-dir",
        str(db_dir),
        "--db-freshness-policy",
        str(policy),
        "--today",
        "2026-06-24",
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert scan_out.exists()
    assert result["scanner"]["preflight"]["passed"] is True
    assert _db_check(result)["freshness"]["code"] == "allowed"
    assert _trivy_check(result)["status"] == "skipped"


def test_scanner_run_replay_rejects_stale_db_metadata_before_copy(tmp_path: Path) -> None:
    db_dir = _write_db_dir(
        tmp_path / "trivy-cache",
        next_update="2026-06-23T00:00:00Z",
        updated_at="2026-06-20T00:00:00Z",
    )
    policy = _write_policy(tmp_path / "policy.json", {"max_age_days": 7})
    scan_out = tmp_path / "scan.json"

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(scan_out),
        "--replay",
        str(FIXTURES / "trivy_fresh_with_timestamp.json"),
        "--db-dir",
        str(db_dir),
        "--db-freshness-policy",
        str(policy),
        "--today",
        "2026-06-24",
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert not scan_out.exists()
    assert result["scanner"]["reason"] == "scanner-preflight-failed"
    assert result["scanner"]["preflight"]["passed"] is False
    assert _db_check(result)["freshness"]["code"] == "stale-next-update"


def test_scanner_run_replay_rejects_malformed_db_metadata(tmp_path: Path) -> None:
    db_dir = tmp_path / "trivy-cache"
    db_dir.mkdir()
    (db_dir / "metadata.json").write_text("{not-json", encoding="utf-8")
    policy = _write_policy(tmp_path / "policy.json", {"max_age_days": 7})

    completed = _run_cli(
        "--image",
        IMAGE,
        "--digest",
        VALID_DIGEST,
        "--out",
        str(tmp_path / "scan.json"),
        "--replay",
        str(FIXTURES / "trivy_fresh_with_timestamp.json"),
        "--db-dir",
        str(db_dir),
        "--db-freshness-policy",
        str(policy),
        "--today",
        "2026-06-24",
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert _db_check(result)["freshness"]["code"] == "malformed-metadata"


def _write_policy(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps({"policy_version": "1", **payload}), encoding="utf-8")
    return path


def _write_metadata(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_db_dir(path: Path, *, next_update: str, updated_at: str) -> Path:
    path.mkdir(parents=True)
    _write_metadata(path / "metadata.json", {"Version": 2, "NextUpdate": next_update, "UpdatedAt": updated_at})
    return path


def _db_check(result: dict[str, object]) -> dict[str, object]:
    checks = result["scanner"]["preflight"]["checks"]
    return next(check for check in checks if check["name"] == "trivy_db_metadata_present")


def _trivy_check(result: dict[str, object]) -> dict[str, object]:
    checks = result["scanner"]["preflight"]["checks"]
    return next(check for check in checks if check["name"] == "trivy_present")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER_RUN), *args],
        text=True,
        capture_output=True,
        check=False,
    )
