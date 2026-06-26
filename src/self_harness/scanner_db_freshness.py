from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCANNER_DB_FRESHNESS_POLICY_VERSION = "1"


@dataclass(frozen=True)
class TrivyDbMetadata:
    version: int
    next_update: date | None
    updated_at: date | None
    source_path: str


@dataclass(frozen=True)
class ScannerDbFreshnessPolicy:
    policy_version: str
    max_age_days: int | None = None
    require_next_update: bool = True


@dataclass(frozen=True)
class ScannerDbFreshnessDecision:
    allowed: bool
    code: str
    message: str
    source_path: str | None
    next_update: date | None
    updated_at: date | None
    evaluated_at: date
    age_days: int | None
    policy: ScannerDbFreshnessPolicy


class ScannerDbFreshnessError(RuntimeError):
    """Raised when scanner DB metadata or freshness policy is malformed."""


def load_scanner_db_freshness_policy(path: Path) -> ScannerDbFreshnessPolicy:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScannerDbFreshnessError(f"missing scanner DB freshness policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScannerDbFreshnessError(f"invalid scanner DB freshness policy JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ScannerDbFreshnessError("scanner DB freshness policy JSON must be an object")
    version = _required_str(data, "policy_version", "scanner DB freshness policy")
    if version != SCANNER_DB_FRESHNESS_POLICY_VERSION:
        raise ScannerDbFreshnessError(f"unsupported scanner DB freshness policy version: {version}")
    max_age_days = _optional_non_negative_int(data.get("max_age_days"), "max_age_days")
    require_next_update = _optional_bool(data.get("require_next_update"), "require_next_update", default=True)
    if max_age_days is None and not require_next_update:
        raise ScannerDbFreshnessError("scanner DB freshness policy must require next_update or max_age_days")
    return ScannerDbFreshnessPolicy(
        policy_version=version,
        max_age_days=max_age_days,
        require_next_update=require_next_update,
    )


def parse_trivy_db_metadata(path: Path) -> TrivyDbMetadata:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScannerDbFreshnessError(f"missing Trivy DB metadata: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScannerDbFreshnessError(f"invalid Trivy DB metadata JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ScannerDbFreshnessError("Trivy DB metadata must be a JSON object")
    version = data.get("Version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ScannerDbFreshnessError("Trivy DB metadata Version must be an integer")
    next_update = _optional_metadata_date(data.get("NextUpdate"), "NextUpdate")
    updated_at = _optional_metadata_date(data.get("UpdatedAt"), "UpdatedAt")
    return TrivyDbMetadata(
        version=version,
        next_update=next_update,
        updated_at=updated_at,
        source_path=str(path),
    )


def evaluate_scanner_db_freshness(
    metadata: TrivyDbMetadata,
    policy: ScannerDbFreshnessPolicy,
    *,
    evaluated_at: date | None = None,
) -> ScannerDbFreshnessDecision:
    evaluation_date = date.today() if evaluated_at is None else evaluated_at
    if policy.require_next_update and metadata.next_update is None:
        return _decision(
            policy,
            allowed=False,
            code="missing-next-update",
            message="Trivy DB metadata does not include NextUpdate",
            metadata=metadata,
            evaluated_at=evaluation_date,
            age_days=None,
        )
    if metadata.next_update is not None and metadata.next_update < evaluation_date:
        return _decision(
            policy,
            allowed=False,
            code="stale-next-update",
            message="Trivy DB NextUpdate is earlier than the evaluation date",
            metadata=metadata,
            evaluated_at=evaluation_date,
            age_days=None,
        )
    if policy.max_age_days is not None:
        if metadata.updated_at is None:
            return _decision(
                policy,
                allowed=False,
                code="missing-updated-at",
                message="Trivy DB metadata does not include UpdatedAt",
                metadata=metadata,
                evaluated_at=evaluation_date,
                age_days=None,
            )
        age_days = (evaluation_date - metadata.updated_at).days
        if age_days < 0:
            return _decision(
                policy,
                allowed=False,
                code="future-updated-at",
                message="Trivy DB UpdatedAt is after the evaluation date",
                metadata=metadata,
                evaluated_at=evaluation_date,
                age_days=age_days,
            )
        if age_days > policy.max_age_days:
            return _decision(
                policy,
                allowed=False,
                code="stale-updated-at",
                message="Trivy DB UpdatedAt is older than freshness policy max_age_days",
                metadata=metadata,
                evaluated_at=evaluation_date,
                age_days=age_days,
            )
    allowed_age_days: int | None = (
        (evaluation_date - metadata.updated_at).days if metadata.updated_at is not None else None
    )
    return _decision(
        policy,
        allowed=True,
        code="allowed",
        message="Trivy DB metadata satisfies freshness policy",
        metadata=metadata,
        evaluated_at=evaluation_date,
        age_days=allowed_age_days,
    )


def scanner_db_freshness_error_decision(
    policy: ScannerDbFreshnessPolicy,
    *,
    code: str,
    message: str,
    source_path: str | None,
    evaluated_at: date | None = None,
) -> ScannerDbFreshnessDecision:
    evaluation_date = date.today() if evaluated_at is None else evaluated_at
    return ScannerDbFreshnessDecision(
        allowed=False,
        code=code,
        message=message,
        source_path=source_path,
        next_update=None,
        updated_at=None,
        evaluated_at=evaluation_date,
        age_days=None,
        policy=policy,
    )


def scanner_db_freshness_decision_to_jsonable(decision: ScannerDbFreshnessDecision) -> dict[str, object]:
    return {
        "required": True,
        "allowed": decision.allowed,
        "code": decision.code,
        "message": decision.message,
        "source_path": decision.source_path,
        "next_update": decision.next_update.isoformat() if decision.next_update is not None else None,
        "updated_at": decision.updated_at.isoformat() if decision.updated_at is not None else None,
        "evaluated_at": decision.evaluated_at.isoformat(),
        "age_days": decision.age_days,
        "max_age_days": decision.policy.max_age_days,
        "require_next_update": decision.policy.require_next_update,
    }


def _decision(
    policy: ScannerDbFreshnessPolicy,
    *,
    allowed: bool,
    code: str,
    message: str,
    metadata: TrivyDbMetadata,
    evaluated_at: date,
    age_days: int | None,
) -> ScannerDbFreshnessDecision:
    return ScannerDbFreshnessDecision(
        allowed=allowed,
        code=code,
        message=message,
        source_path=metadata.source_path,
        next_update=metadata.next_update,
        updated_at=metadata.updated_at,
        evaluated_at=evaluated_at,
        age_days=age_days,
        policy=policy,
    )


def _optional_metadata_date(value: object, label: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ScannerDbFreshnessError(f"Trivy DB metadata {label} must be a non-empty string")
    parsed = _parse_metadata_date(value)
    if parsed is None:
        raise ScannerDbFreshnessError(f"Trivy DB metadata {label} must be ISO-8601 or YYYY-MM-DD")
    return parsed


def _parse_metadata_date(value: str) -> date | None:
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
        raise ScannerDbFreshnessError(f"{label} missing string field: {key}")
    return value


def _optional_non_negative_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ScannerDbFreshnessError(f"scanner DB freshness policy {label} must be a non-negative integer")
    return value


def _optional_bool(value: object, label: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ScannerDbFreshnessError(f"scanner DB freshness policy {label} must be a boolean")
    return value
