from __future__ import annotations

from pathlib import Path
from typing import Any

from self_harness.audit import AuditRun, load_audit_run
from self_harness.reporting.provenance import (
    provenance_from_manifest,
    validate_provenance_completeness,
)
from self_harness.types import stable_json_dumps, to_jsonable

BENCHMARK_REPORT_SCHEMA_VERSION = "1.0"


def build_benchmark_report(
    audit_dirs: dict[str, Path],
    *,
    reproduction_claimed: bool = False,
) -> dict[str, Any]:
    audits = {label: load_audit_run(path) for label, path in sorted(audit_dirs.items())}
    provenance = {label: provenance_from_manifest(audit.manifest) for label, audit in audits.items()}
    for item in provenance.values():
        validate_provenance_completeness(item, reproduction_claimed=reproduction_claimed)
    return {
        "schema_version": BENCHMARK_REPORT_SCHEMA_VERSION,
        "reproduction_claimed": reproduction_claimed,
        "provenance_per_model": {label: to_jsonable(item) for label, item in provenance.items()},
        "per_model_summary": {label: _model_summary(audit) for label, audit in audits.items()},
        "per_task_breakdown": {label: _per_task_breakdown(audit) for label, audit in audits.items()},
        "split_gains": {label: _split_gains(audit) for label, audit in audits.items()},
    }


def write_benchmark_report(
    audit_dirs: dict[str, Path],
    out_path: Path,
    *,
    reproduction_claimed: bool = False,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        stable_json_dumps(build_benchmark_report(audit_dirs, reproduction_claimed=reproduction_claimed)) + "\n",
        encoding="utf-8",
    )
    return out_path


def _model_summary(audit: AuditRun) -> dict[str, Any]:
    initial = _initial_totals(audit)
    final = _final_totals(audit)
    return {
        "initial_held_in_passed": _passed_for_split(initial, "held_in"),
        "initial_held_out_passed": _passed_for_split(initial, "held_out"),
        "final_held_in_passed": _passed_for_split(final, "held_in"),
        "final_held_out_passed": _passed_for_split(final, "held_out"),
        "initial_held_in_score": _score_for_split(initial, "held_in"),
        "initial_held_out_score": _score_for_split(initial, "held_out"),
        "final_held_in_score": _score_for_split(final, "held_in"),
        "final_held_out_score": _score_for_split(final, "held_out"),
        "rounds": len(audit.rounds),
    }


def _split_gains(audit: AuditRun) -> dict[str, Any]:
    summary = _model_summary(audit)
    return {
        "held_in_pass_delta": summary["final_held_in_passed"] - summary["initial_held_in_passed"],
        "held_out_pass_delta": summary["final_held_out_passed"] - summary["initial_held_out_passed"],
        "held_in_relative_gain": _relative_gain(
            summary["initial_held_in_score"],
            summary["final_held_in_score"],
        ),
        "held_out_relative_gain": _relative_gain(
            summary["initial_held_out_score"],
            summary["final_held_out_score"],
        ),
    }


def _per_task_breakdown(audit: AuditRun) -> list[dict[str, Any]]:
    if not audit.rounds:
        return []
    proposal_id, arm = _committed_selector(audit.rounds[-1].proposals)
    return [
        {
            "task_id": str(row.get("task_id", "")),
            "split": str(row.get("split", "")),
            "passed": row.get("verifier_pass") == 1,
            "terminal_cause": row.get("terminal_cause"),
            "mechanism": row.get("mechanism"),
        }
        for row in audit.rounds[-1].evaluations
        if row.get("task_id") != "__split_total__"
        and row.get("proposal_id") == proposal_id
        and row.get("arm") == arm
    ]


def _initial_totals(audit: AuditRun) -> list[dict[str, Any]]:
    if not audit.rounds:
        return []
    return _totals_for(audit.rounds[0].evaluations, "__baseline__", "baseline")


def _final_totals(audit: AuditRun) -> list[dict[str, Any]]:
    if not audit.rounds:
        return []
    proposal_id, arm = _committed_selector(audit.rounds[-1].proposals)
    return _totals_for(audit.rounds[-1].evaluations, proposal_id, arm)


def _committed_selector(proposals: list[dict[str, Any]]) -> tuple[str, str]:
    if any(row.get("status") == "merged" for row in proposals):
        return "__merge__", "candidate"
    accepted = sorted(str(row["id"]) for row in proposals if row.get("status") == "accepted" and "id" in row)
    if accepted:
        return accepted[0], "candidate"
    return "__baseline__", "baseline"


def _totals_for(rows: list[dict[str, Any]], proposal_id: str, arm: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("task_id") == "__split_total__"
        and row.get("proposal_id") == proposal_id
        and row.get("arm") == arm
    ]


def _passed_for_split(rows: list[dict[str, Any]], split: str) -> int:
    for row in rows:
        if row.get("split") == split and isinstance(row.get("verifier_pass"), int):
            return int(row["verifier_pass"])
    return 0


def _score_for_split(rows: list[dict[str, Any]], split: str) -> float:
    for row in rows:
        if row.get("split") == split and isinstance(row.get("score"), int | float):
            return float(row["score"])
    return 0.0


def _relative_gain(initial: float, final: float) -> float | None:
    if initial == 0:
        return None
    return (final - initial) / initial
