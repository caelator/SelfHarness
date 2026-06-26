from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from self_harness.types import FailureCategory


class HarborOutputFormat(StrEnum):
    SELF_HARNESS_V1 = "self-harness-harbor-v1"


@dataclass(frozen=True)
class HarborRunResult:
    task_id: str
    passed: bool
    verifier_output: str
    terminal_cause: str
    mechanism: str
    causal_status: str = "confirmed"
    trace_path: str | None = None
    container_digest: str | None = None


def parse_harbor_output(
    stdout: str,
    stderr: str,
    *,
    returncode: int,
    task_id: str,
    output_format: HarborOutputFormat = HarborOutputFormat.SELF_HARNESS_V1,
) -> HarborRunResult:
    if output_format != HarborOutputFormat.SELF_HARNESS_V1:
        raise ValueError(f"unsupported Harbor output format: {output_format}")
    payload = _extract_json_payload(stdout)
    if payload is None:
        passed = returncode == 0
        return HarborRunResult(
            task_id=task_id,
            passed=passed,
            verifier_output=(stdout.strip() or stderr.strip()),
            terminal_cause=FailureCategory.VERIFIER_PASS.value if passed else FailureCategory.VERIFIER_FAIL.value,
            mechanism="harbor-exit-code",
            causal_status="confirmed" if passed else "rejected",
        )
    row = _task_row(payload, task_id)
    passed = _bool_field(row, "passed", default=returncode == 0)
    terminal_cause = map_harbor_failure_category(_str_field(row, "terminal_cause", ""))
    if not terminal_cause:
        terminal_cause = FailureCategory.VERIFIER_PASS.value if passed else FailureCategory.VERIFIER_FAIL.value
    return HarborRunResult(
        task_id=_str_field(row, "task_id", task_id),
        passed=passed,
        verifier_output=_str_field(row, "verifier_output", _str_field(row, "message", "")),
        terminal_cause=terminal_cause,
        mechanism=_str_field(row, "mechanism", terminal_cause),
        causal_status=_str_field(row, "causal_status", "confirmed" if passed else "rejected"),
        trace_path=_optional_str(row.get("trace_path")),
        container_digest=_optional_str(
            row.get("container_digest")
            or row.get("container_image_digest")
            or row.get("container_image_digest_or_unknown")
        ),
    )


def map_harbor_failure_category(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"", "passed", "pass", "success", "verifier-pass"}:
        return FailureCategory.VERIFIER_PASS.value if normalized else ""
    if "timeout" in normalized:
        return FailureCategory.TIMEOUT.value
    if "missing" in normalized and "artifact" in normalized:
        return FailureCategory.MISSING_ARTIFACT.value
    if "assert" in normalized:
        return FailureCategory.ASSERTION_FAIL.value
    if "environment" in normalized or "infra" in normalized:
        return FailureCategory.ENVIRONMENT_ERROR.value
    if normalized in {item.value for item in FailureCategory}:
        return normalized
    return FailureCategory.VERIFIER_FAIL.value


def _extract_json_payload(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    candidates = [text, *reversed([line.strip() for line in text.splitlines() if line.strip()])]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _task_row(payload: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = payload.get("tasks") or payload.get("results")
    if isinstance(tasks, list):
        for item in tasks:
            if isinstance(item, dict) and item.get("task_id") == task_id:
                return item
        for item in tasks:
            if isinstance(item, dict):
                return item
    return payload


def _bool_field(row: dict[str, Any], key: str, *, default: bool) -> bool:
    value = row.get(key)
    return value if isinstance(value, bool) else default


def _str_field(row: dict[str, Any], key: str, default: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) and value else default


def _optional_str(value: object) -> str | None:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None
