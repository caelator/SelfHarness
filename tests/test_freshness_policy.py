import json
from datetime import date
from pathlib import Path

import pytest

from self_harness.freshness_policy import (
    FreshnessPolicyError,
    evaluate_freshness_policy,
    load_freshness_policy,
    load_trivy_report_timestamp,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "vuln"


def test_freshness_policy_allows_recent_trivy_report(tmp_path: Path) -> None:
    policy = load_freshness_policy(_write_policy(tmp_path / "freshness.json", {"max_age_days": 7}))
    timestamp = load_trivy_report_timestamp(FIXTURES / "trivy_fresh_with_timestamp.json")

    decision = evaluate_freshness_policy(policy, timestamp, evaluated_at=date(2026, 6, 24))

    assert decision.allowed
    assert decision.code == "allowed"
    assert decision.age_days == 0


def test_freshness_policy_rejects_stale_report(tmp_path: Path) -> None:
    policy = load_freshness_policy(_write_policy(tmp_path / "freshness.json", {"max_age_days": 7}))
    timestamp = load_trivy_report_timestamp(FIXTURES / "trivy_stale_with_timestamp.json")

    decision = evaluate_freshness_policy(policy, timestamp, evaluated_at=date(2026, 6, 24))

    assert not decision.allowed
    assert decision.code == "stale-report"
    assert decision.age_days == 54


def test_freshness_policy_not_before_rejects_older_report(tmp_path: Path) -> None:
    policy = load_freshness_policy(_write_policy(tmp_path / "freshness.json", {"not_before": "2026-06-01"}))
    timestamp = load_trivy_report_timestamp(FIXTURES / "trivy_stale_with_timestamp.json")

    decision = evaluate_freshness_policy(policy, timestamp, evaluated_at=date(2026, 6, 24))

    assert not decision.allowed
    assert decision.code == "before-not-before"


def test_freshness_policy_fails_closed_for_missing_malformed_and_future_timestamps(tmp_path: Path) -> None:
    policy = load_freshness_policy(_write_policy(tmp_path / "freshness.json", {"max_age_days": 7}))

    missing = evaluate_freshness_policy(
        policy,
        load_trivy_report_timestamp(FIXTURES / "trivy_clean.json"),
        evaluated_at=date(2026, 6, 24),
    )
    malformed = evaluate_freshness_policy(
        policy,
        load_trivy_report_timestamp(FIXTURES / "trivy_malformed_timestamp.json"),
        evaluated_at=date(2026, 6, 24),
    )
    future = evaluate_freshness_policy(policy, "2026-06-25T00:00:00Z", evaluated_at=date(2026, 6, 24))

    assert missing.code == "missing-timestamp"
    assert malformed.code == "malformed-timestamp"
    assert future.code == "future-timestamp"
    assert not missing.allowed
    assert not malformed.allowed
    assert not future.allowed


def test_freshness_policy_reads_metadata_created_at() -> None:
    timestamp = load_trivy_report_timestamp(FIXTURES / "trivy_metadata_timestamp.json")

    assert timestamp == "2026-06-23"


def test_freshness_policy_rejects_bad_schema(tmp_path: Path) -> None:
    with pytest.raises(FreshnessPolicyError, match="max_age_days or not_before"):
        load_freshness_policy(_write_policy(tmp_path / "empty.json", {}))
    with pytest.raises(FreshnessPolicyError, match="non-negative integer"):
        load_freshness_policy(_write_policy(tmp_path / "bad-age.json", {"max_age_days": -1}))
    with pytest.raises(FreshnessPolicyError, match="YYYY-MM-DD"):
        load_freshness_policy(_write_policy(tmp_path / "bad-date.json", {"not_before": "last week"}))


def _write_policy(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps({"policy_version": "1", **payload}), encoding="utf-8")
    return path
