from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from self_harness.exceptions import AuditCorruptError
from self_harness.types import write_jsonl, write_stable_json

SCHEMA_CHANGELOG_DOC = "docs/architecture/schema_changelog.md"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0", "1.1", "1.2", "1.3", "1.4"})
"""Supported audit schema versions; keep in sync with docs/architecture/schema_changelog.md."""
TRAJECTORY_SCHEMA_VERSION = "1.0"
"""Schema version for derived paper-style trajectory rows."""
HARNESS_INSPECTION_SCHEMA_VERSION = "1.0"
"""Schema version for derived harness-inspection reports."""


@dataclass(frozen=True)
class AuditRound:
    index: int
    proposals: list[dict[str, Any]]
    evaluations: list[dict[str, Any]]
    harness_before: dict[str, Any]
    harness_after: dict[str, Any]


@dataclass(frozen=True)
class AuditRun:
    path: Path
    manifest: dict[str, Any]
    lineage: list[dict[str, Any]]
    rounds: list[AuditRound]


@dataclass(frozen=True)
class AuditSummary:
    schema_version: str
    protocol_version: str
    rounds: int
    final_held_in_score: float | None
    final_held_out_score: float | None
    accepted_count: int
    rejected_count: int
    invalid_count: int
    benchmark_protocol: str | None = None
    reproduction_claimed: bool = False


@dataclass(frozen=True)
class AuditDiff:
    equal: bool
    changed_files: list[str]
    missing_from_left: list[str]
    missing_from_right: list[str]


@dataclass(frozen=True)
class HarnessInspection:
    schema_version: str
    audit_schema_version: str
    protocol_version: str
    rounds: list[dict[str, Any]]
    final_harness_hash: str
    final_harness_surfaces: dict[str, Any]
    retained_ops_count: int
    retained_changed_surfaces: list[str]


def load_audit_run(path: Path) -> AuditRun:
    root = Path(path)
    manifest = _read_json_object(root / "manifest.json")
    schema_version = str(manifest.get("schema_version", ""))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise AuditCorruptError(f"unsupported audit schema_version: {schema_version}")

    lineage_raw = _read_json_value(root / "lineage.json")
    if not isinstance(lineage_raw, list):
        raise AuditCorruptError("lineage.json must contain a list")
    lineage = [_require_object(item, "lineage row") for item in lineage_raw]

    rounds_dir = root / "rounds"
    if not rounds_dir.is_dir():
        raise AuditCorruptError("missing rounds directory")

    rounds: list[AuditRound] = []
    for lineage_row in lineage:
        index_raw = lineage_row.get("round")
        if not isinstance(index_raw, int):
            raise AuditCorruptError("lineage row missing integer round")
        round_dir = rounds_dir / str(index_raw)
        if not round_dir.is_dir():
            raise AuditCorruptError(f"missing round directory: {round_dir}")
        rounds.append(
            AuditRound(
                index=index_raw,
                proposals=_read_jsonl(round_dir / "proposals.jsonl"),
                evaluations=_read_jsonl(round_dir / "evaluations.jsonl"),
                harness_before=_read_json_object(round_dir / "harness_before.json"),
                harness_after=_read_json_object(round_dir / "harness_after.json"),
            )
        )

    return AuditRun(path=root, manifest=manifest, lineage=lineage, rounds=rounds)


def summarize_audit_run(path: Path) -> AuditSummary:
    audit = load_audit_run(path)
    proposals = [row for round_ in audit.rounds for row in round_.proposals]
    final_totals = _committed_final_totals(audit.rounds[-1]) if audit.rounds else []
    return AuditSummary(
        schema_version=str(audit.manifest.get("schema_version", "")),
        protocol_version=str(audit.manifest.get("protocol_version", audit.manifest.get("protocol_hash", ""))),
        rounds=len(audit.rounds),
        final_held_in_score=_score_for_split(final_totals, "held_in"),
        final_held_out_score=_score_for_split(final_totals, "held_out"),
        accepted_count=sum(1 for row in proposals if row.get("status") in {"accepted", "merged"}),
        rejected_count=sum(1 for row in proposals if row.get("status") == "rejected"),
        invalid_count=sum(1 for row in proposals if row.get("status") == "invalid"),
        benchmark_protocol=_optional_str(audit.manifest.get("benchmark_protocol")),
        reproduction_claimed=audit.manifest.get("reproduction_claimed") is True,
    )


def audit_trajectory_rows(path: Path) -> list[dict[str, Any]]:
    """Build paper-style trajectory rows from an existing audit directory."""

    audit = load_audit_run(path)
    lineage_by_round = {int(row["round"]): row for row in audit.lineage if isinstance(row.get("round"), int)}
    return [_trajectory_row(round_, lineage_by_round.get(round_.index, {})) for round_ in audit.rounds]


def write_audit_trajectory(path: Path, out_path: Path | None = None) -> Path:
    """Write stable JSONL trajectory rows and return the output path."""

    root = Path(path)
    destination = out_path or root / "trajectory.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(destination, audit_trajectory_rows(root))
    return destination


def inspect_harness_run(path: Path) -> HarnessInspection:
    """Build a stable retained-edits report from an audit directory."""

    audit = load_audit_run(path)
    final_harness = audit.rounds[-1].harness_after if audit.rounds else {}
    final_hash = str(audit.lineage[-1].get("harness_after_hash", "")) if audit.lineage else ""
    rounds = [_harness_inspection_round(round_, _lineage_for_round(audit, round_.index)) for round_ in audit.rounds]
    retained_surfaces = sorted(
        {
            str(op.get("surface"))
            for row in audit.lineage
            for op in _object_list(row.get("ops_applied"))
            if isinstance(op.get("surface"), str)
        }
    )
    return HarnessInspection(
        schema_version=HARNESS_INSPECTION_SCHEMA_VERSION,
        audit_schema_version=str(audit.manifest.get("schema_version", "")),
        protocol_version=str(audit.manifest.get("protocol_version", audit.manifest.get("protocol_hash", ""))),
        rounds=rounds,
        final_harness_hash=final_hash,
        final_harness_surfaces=_surface_report(final_harness),
        retained_ops_count=sum(len(_object_list(row.get("ops_applied"))) for row in audit.lineage),
        retained_changed_surfaces=retained_surfaces,
    )


def write_harness_inspection(path: Path, out_path: Path | None = None) -> Path:
    """Write a stable harness-inspection JSON report and return the output path."""

    root = Path(path)
    destination = out_path or root / "harness_inspection.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_stable_json(destination, inspect_harness_run(root))
    return destination


def _committed_final_totals(round_: AuditRound) -> list[dict[str, Any]]:
    proposal_id, arm = _committed_eval_selector(round_)
    totals = [
        row
        for row in round_.evaluations
        if row.get("task_id") == "__split_total__"
        and row.get("proposal_id") == proposal_id
        and row.get("arm") == arm
    ]
    if totals:
        return totals
    return _preferred_final_totals(round_.evaluations)


def _committed_eval_selector(round_: AuditRound) -> tuple[str, str]:
    if any(row.get("status") == "merged" for row in round_.proposals):
        return "__merge__", "candidate"
    accepted = sorted(str(row["id"]) for row in round_.proposals if row.get("status") == "accepted" and "id" in row)
    if accepted:
        return accepted[0], "candidate"
    return "__baseline__", "baseline"


def _trajectory_row(round_: AuditRound, lineage_row: dict[str, Any]) -> dict[str, Any]:
    baseline_totals = _totals_for(round_, "__baseline__", "baseline")
    proposal_id, arm = _committed_eval_selector(round_)
    committed_totals = _totals_for(round_, proposal_id, arm)
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "round": round_.index,
        "harness_before_hash": str(lineage_row.get("harness_before_hash", "")),
        "harness_after_hash": str(lineage_row.get("harness_after_hash", "")),
        "baseline_held_in_passed": _passed_for_split(baseline_totals, "held_in"),
        "baseline_held_out_passed": _passed_for_split(baseline_totals, "held_out"),
        "after_held_in_passed": _passed_for_split(committed_totals, "held_in"),
        "after_held_out_passed": _passed_for_split(committed_totals, "held_out"),
        "proposals": [
            _trajectory_proposal_row(row)
            for row in sorted(round_.proposals, key=lambda item: str(item["id"]))
        ],
        "merged": any(row.get("status") == "merged" for row in round_.proposals),
    }


def _trajectory_proposal_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "status": str(row.get("status", "")),
        "pattern_id": str(row.get("pattern_id", "")),
        "changed_surfaces": _string_list(row.get("changed_surfaces", [])),
        "primary_op": str(row.get("op", "")),
        "score_held_in_delta": _int_delta(row.get("passed_held_in"), row.get("baseline_passed_held_in")),
        "score_held_out_delta": _int_delta(row.get("passed_held_out"), row.get("baseline_passed_held_out")),
        "decision_reason": str(row.get("decision_reason", "")),
    }


def _lineage_for_round(audit: AuditRun, round_index: int) -> dict[str, Any]:
    for row in audit.lineage:
        if row.get("round") == round_index:
            return row
    return {}


def _harness_inspection_round(round_: AuditRound, lineage_row: dict[str, Any]) -> dict[str, Any]:
    proposals = sorted(round_.proposals, key=lambda row: str(row.get("id", "")))
    ops_applied = _object_list(lineage_row.get("ops_applied"))
    reverse_ops = _object_list(lineage_row.get("reverse_ops"))
    return {
        "round": round_.index,
        "harness_before_hash": str(lineage_row.get("harness_before_hash", "")),
        "harness_after_hash": str(lineage_row.get("harness_after_hash", "")),
        "accepted_proposal_ids": _string_list(lineage_row.get("accepted_proposal_ids", [])),
        "ops_applied": ops_applied,
        "reverse_ops": reverse_ops,
        "changed_surfaces": sorted(
            {str(op["surface"]) for op in ops_applied if isinstance(op.get("surface"), str)}
        ),
        "proposal_status_counts": _proposal_status_counts(proposals),
        "proposals": [_harness_inspection_proposal(row) for row in proposals],
    }


def _harness_inspection_proposal(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "status": str(row.get("status", "")),
        "pattern_id": str(row.get("pattern_id", "")),
        "changed_surfaces": _string_list(row.get("changed_surfaces", [])),
        "primary_op": str(row.get("op", "")),
        "decision_reason": str(row.get("decision_reason", "")),
        "rejection_reason": str(row.get("rejection_reason", "")) if row.get("rejection_reason") is not None else None,
    }


def _proposal_status_counts(proposals: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in proposals:
        status = str(row.get("status", ""))
        counts[status] = counts.get(status, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def _surface_report(harness: dict[str, Any]) -> dict[str, Any]:
    return {
        surface: {
            "kind": _surface_kind(surface),
            "value_hash": _value_hash(value),
            "value": value,
        }
        for surface, value in sorted(harness.items())
    }


def _surface_kind(surface: str) -> str:
    if surface in {"tools", "skills", "memory_sources", "subagents"}:
        return "list"
    if surface == "runtime_policy":
        return "policy"
    return "text"


def _value_hash(value: Any) -> str:
    from hashlib import sha256

    from self_harness.types import stable_json_dumps

    return sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _totals_for(round_: AuditRound, proposal_id: str, arm: str) -> list[dict[str, Any]]:
    return [
        row
        for row in round_.evaluations
        if row.get("task_id") == "__split_total__"
        and row.get("proposal_id") == proposal_id
        and row.get("arm") == arm
    ]


def _passed_for_split(rows: list[dict[str, Any]], split: str) -> int:
    for row in rows:
        if row.get("split") == split:
            value = row.get("verifier_pass")
            if isinstance(value, int):
                return value
    return 0


def _int_delta(value: object, baseline: object) -> int:
    left = value if isinstance(value, int) else 0
    right = baseline if isinstance(baseline, int) else 0
    return left - right


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def diff_audit_runs(left: Path, right: Path) -> AuditDiff:
    left_root = Path(left)
    right_root = Path(right)
    left_files = _audit_files(left_root)
    right_files = _audit_files(right_root)
    left_rel = {path.relative_to(left_root).as_posix(): path for path in left_files}
    right_rel = {path.relative_to(right_root).as_posix(): path for path in right_files}
    missing_from_left = sorted(set(right_rel) - set(left_rel))
    missing_from_right = sorted(set(left_rel) - set(right_rel))
    changed_files = sorted(
        rel
        for rel in set(left_rel) & set(right_rel)
        if left_rel[rel].read_bytes() != right_rel[rel].read_bytes()
    )
    return AuditDiff(
        equal=not changed_files and not missing_from_left and not missing_from_right,
        changed_files=changed_files,
        missing_from_left=missing_from_left,
        missing_from_right=missing_from_right,
    )


def _score_for_split(rows: list[dict[str, Any]], split: str) -> float | None:
    for row in rows:
        if row.get("split") == split and isinstance(row.get("score"), int | float):
            return float(row["score"])
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _preferred_final_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals = [row for row in rows if row.get("task_id") == "__split_total__"]
    candidate = [row for row in totals if row.get("arm") == "candidate"]
    if candidate:
        return candidate
    return [row for row in totals if row.get("arm") == "baseline"]


def _read_json_object(path: Path) -> dict[str, Any]:
    value = _read_json_value(path)
    return _require_object(value, str(path))


def _read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuditCorruptError(f"missing audit artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuditCorruptError(f"invalid JSON in audit artifact: {path}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise AuditCorruptError(f"missing audit artifact: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_require_object(json.loads(line), f"{path}:{line_no}"))
        except json.JSONDecodeError as exc:
            raise AuditCorruptError(f"invalid JSONL row in {path}:{line_no}") from exc
    return rows


def _audit_files(root: Path) -> list[Path]:
    try:
        files = [path for path in root.rglob("*") if path.is_file()]
    except FileNotFoundError as exc:
        raise AuditCorruptError(f"missing audit directory: {root}") from exc
    if not root.is_dir():
        raise AuditCorruptError(f"missing audit directory: {root}")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuditCorruptError(f"{label} must be a JSON object")
    return value
