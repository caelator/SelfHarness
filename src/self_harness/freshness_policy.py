from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

FRESHNESS_POLICY_VERSION = "1"


@dataclass(frozen=True)
class FreshnessPolicy:
    policy_version: str
    max_age_days: int | None = None
    not_before: date | None = None


@dataclass(frozen=True)
class FreshnessDecision:
    allowed: bool
    code: str
    message: str
    report_timestamp: str | None
    report_date: date | None
    evaluated_at: date
    age_days: int | None
    policy: FreshnessPolicy


class FreshnessPolicyError(RuntimeError):
    """Raised when scanner freshness policy or report metadata is malformed."""


def load_freshness_policy(path: Path) -> FreshnessPolicy:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FreshnessPolicyError(f"missing freshness policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FreshnessPolicyError(f"invalid freshness policy JSON: {path}") from exc
    if not isinstance(data, dict):
        raise FreshnessPolicyError("freshness policy JSON must be an object")
    version = _required_str(data, "policy_version", "freshness policy")
    if version != FRESHNESS_POLICY_VERSION:
        raise FreshnessPolicyError(f"unsupported freshness policy version: {version}")
    max_age_days = _optional_non_negative_int(data.get("max_age_days"), "max_age_days")
    not_before = _optional_date(data.get("not_before"), "not_before")
    if max_age_days is None and not_before is None:
        raise FreshnessPolicyError("freshness policy must include max_age_days or not_before")
    return FreshnessPolicy(policy_version=version, max_age_days=max_age_days, not_before=not_before)


def evaluate_freshness_policy(
    policy: FreshnessPolicy,
    report_timestamp: object,
    *,
    evaluated_at: date | None = None,
) -> FreshnessDecision:
    evaluation_date = date.today() if evaluated_at is None else evaluated_at
    if report_timestamp is None:
        return _decision(
            policy,
            allowed=False,
            code="missing-timestamp",
            message="scanner report does not include a creation timestamp",
            report_timestamp=None,
            report_date=None,
            evaluated_at=evaluation_date,
            age_days=None,
        )
    if not isinstance(report_timestamp, str) or not report_timestamp:
        return _decision(
            policy,
            allowed=False,
            code="malformed-timestamp",
            message="scanner report timestamp must be a non-empty string",
            report_timestamp=str(report_timestamp),
            report_date=None,
            evaluated_at=evaluation_date,
            age_days=None,
        )
    report_date = _parse_report_date(report_timestamp)
    if report_date is None:
        return _decision(
            policy,
            allowed=False,
            code="malformed-timestamp",
            message="scanner report timestamp must be ISO-8601 or YYYY-MM-DD",
            report_timestamp=report_timestamp,
            report_date=None,
            evaluated_at=evaluation_date,
            age_days=None,
        )
    age_days = (evaluation_date - report_date).days
    if age_days < 0:
        return _decision(
            policy,
            allowed=False,
            code="future-timestamp",
            message="scanner report timestamp is after the evaluation date",
            report_timestamp=report_timestamp,
            report_date=report_date,
            evaluated_at=evaluation_date,
            age_days=age_days,
        )
    if policy.not_before is not None and report_date < policy.not_before:
        return _decision(
            policy,
            allowed=False,
            code="before-not-before",
            message="scanner report timestamp is earlier than freshness policy not_before",
            report_timestamp=report_timestamp,
            report_date=report_date,
            evaluated_at=evaluation_date,
            age_days=age_days,
        )
    if policy.max_age_days is not None and age_days > policy.max_age_days:
        return _decision(
            policy,
            allowed=False,
            code="stale-report",
            message="scanner report is older than freshness policy max_age_days",
            report_timestamp=report_timestamp,
            report_date=report_date,
            evaluated_at=evaluation_date,
            age_days=age_days,
        )
    return _decision(
        policy,
        allowed=True,
        code="allowed",
        message="scanner report satisfies freshness policy",
        report_timestamp=report_timestamp,
        report_date=report_date,
        evaluated_at=evaluation_date,
        age_days=age_days,
    )


def freshness_decision_to_jsonable(decision: FreshnessDecision) -> dict[str, object]:
    return {
        "required": True,
        "allowed": decision.allowed,
        "code": decision.code,
        "message": decision.message,
        "report_timestamp": decision.report_timestamp,
        "report_date": decision.report_date.isoformat() if decision.report_date is not None else None,
        "evaluated_at": decision.evaluated_at.isoformat(),
        "age_days": decision.age_days,
        "max_age_days": decision.policy.max_age_days,
        "not_before": decision.policy.not_before.isoformat() if decision.policy.not_before is not None else None,
    }


def trivy_report_timestamp(report: dict[str, Any]) -> object:
    if "CreatedAt" in report:
        return report["CreatedAt"]
    metadata = report.get("Metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise FreshnessPolicyError("Trivy report Metadata must be an object")
    return metadata.get("CreatedAt")


def load_trivy_report_timestamp(path: Path) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FreshnessPolicyError(f"missing Trivy report: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FreshnessPolicyError(f"invalid Trivy report JSON: {path}") from exc
    if not isinstance(value, dict):
        raise FreshnessPolicyError("Trivy report must be a JSON object")
    return trivy_report_timestamp(value)


def _parse_report_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _required_str(row: dict[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise FreshnessPolicyError(f"{label} missing string field: {key}")
    return value


def _optional_non_negative_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FreshnessPolicyError(f"freshness policy {label} must be a non-negative integer")
    return value


def _optional_date(value: object, label: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise FreshnessPolicyError(f"freshness policy {label} must be a non-empty YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FreshnessPolicyError(f"freshness policy {label} must use YYYY-MM-DD") from exc


def _decision(
    policy: FreshnessPolicy,
    *,
    allowed: bool,
    code: str,
    message: str,
    report_timestamp: str | None,
    report_date: date | None,
    evaluated_at: date,
    age_days: int | None,
) -> FreshnessDecision:
    return FreshnessDecision(
        allowed=allowed,
        code=code,
        message=message,
        report_timestamp=report_timestamp,
        report_date=report_date,
        evaluated_at=evaluated_at,
        age_days=age_days,
        policy=policy,
    )
