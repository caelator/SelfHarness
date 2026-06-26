from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from self_harness.audit import SUPPORTED_SCHEMA_VERSIONS, AuditRound, AuditRun, load_audit_run
from self_harness.exceptions import AuditVerificationError
from self_harness.harness import harness_hash
from self_harness.types import HarnessSpec, stable_json_dumps

AUDIT_VERIFICATION_SCHEMA_VERSION = "1.0"
AUDIT_VERIFICATION_BOUNDARY = (
    "offline audit integrity verification only; reads an existing audit directory, "
    "does not execute tasks, invoke models, contact Harbor, Docker, registries, scanners, "
    "PyPI, Sigstore, or cloud providers, and is not benchmark reproduction evidence"
)


@dataclass(frozen=True)
class AuditVerificationCheck:
    name: str
    status: str
    detail: str
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class AuditVerificationReport:
    schema_version: str
    audit_schema_version: str
    path: str
    ok: bool
    mode: str
    reproduction_claimed: bool
    held_out_leakage: bool
    proposer_evidence_inspected: bool
    changed_surfaces_recorded: bool
    evaluation_repeats_recorded: bool
    rejected_reasons_recorded: bool
    checks: tuple[AuditVerificationCheck, ...]
    report_hash: str
    boundary: str


def verify_audit_run(path: Path, *, strict_migration: bool = True) -> AuditVerificationReport:
    """Verify internal consistency of an audit tree without mutating or executing it."""

    root = Path(path)
    audit = load_audit_run(root)
    audit_schema_version = str(audit.manifest.get("schema_version", ""))
    checks: list[AuditVerificationCheck] = []

    _add_check(
        checks,
        name="schema_version_supported",
        passed=audit_schema_version in SUPPORTED_SCHEMA_VERSIONS,
        detail=f"audit schema_version is {audit_schema_version}",
        path=root / "manifest.json",
    )
    _check_round_coverage(root, audit.lineage, checks)
    _check_migration_provenance(audit.manifest, audit_schema_version, checks, root=root, strict=strict_migration)

    lineage_by_round = {
        row["round"]: row
        for row in audit.lineage
        if isinstance(row.get("round"), int)
    }
    previous_after: dict[str, Any] | None = None
    for round_ in audit.rounds:
        lineage = lineage_by_round.get(round_.index, {})
        _check_lineage_schema(round_, lineage, audit_schema_version, checks, root=root)
        _check_harness_snapshots(round_, lineage, previous_after, checks, root=root)
        _check_proposals(round_, lineage, audit_schema_version, checks, root=root)
        _check_evaluations(round_, audit_schema_version, checks, root=root)
        previous_after = round_.harness_after

    ok = all(check.status == "pass" for check in checks)
    return _report(
        audit_schema_version=audit_schema_version,
        root=root,
        ok=ok,
        checks=tuple(checks),
        audit=audit,
    )


def audit_verification_report_to_jsonable(report: AuditVerificationReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "audit_schema_version": report.audit_schema_version,
        "path": report.path,
        "ok": report.ok,
        "mode": report.mode,
        "reproduction_claimed": report.reproduction_claimed,
        "held_out_leakage": report.held_out_leakage,
        "proposer_evidence_inspected": report.proposer_evidence_inspected,
        "changed_surfaces_recorded": report.changed_surfaces_recorded,
        "evaluation_repeats_recorded": report.evaluation_repeats_recorded,
        "rejected_reasons_recorded": report.rejected_reasons_recorded,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "metadata": check.metadata,
            }
            for check in report.checks
        ],
        "report_hash": report.report_hash,
        "boundary": report.boundary,
    }


def _check_round_coverage(root: Path, lineage: list[dict[str, Any]], checks: list[AuditVerificationCheck]) -> None:
    rounds_dir = root / "rounds"
    round_dirs = [item for item in rounds_dir.iterdir() if item.is_dir()]
    non_integer_dirs = sorted(item.name for item in round_dirs if not item.name.isdigit())
    actual_rounds = sorted(int(item.name) for item in round_dirs if item.name.isdigit())
    lineage_rounds = [row.get("round") for row in lineage]
    integer_lineage_rounds = sorted(row for row in lineage_rounds if isinstance(row, int))
    expected = list(range(len(lineage)))

    _add_check(
        checks,
        name="round_directories_are_integer_named",
        passed=not non_integer_dirs,
        detail="round directories use integer names",
        path=rounds_dir,
        metadata={"invalid": non_integer_dirs} if non_integer_dirs else None,
    )
    _add_check(
        checks,
        name="lineage_rounds_are_unique",
        passed=len(set(integer_lineage_rounds)) == len(integer_lineage_rounds) == len(lineage),
        detail="lineage contains one integer row per round",
        path=root / "lineage.json",
        metadata={"lineage_rounds": [str(item) for item in lineage_rounds]},
    )
    _add_check(
        checks,
        name="rounds_match_lineage",
        passed=actual_rounds == integer_lineage_rounds == expected,
        detail="round directories and lineage rows are contiguous from zero",
        path=rounds_dir,
        metadata={
            "round_dirs": actual_rounds,
            "lineage_rounds": integer_lineage_rounds,
            "expected": expected,
        },
    )


def _check_migration_provenance(
    manifest: dict[str, Any],
    audit_schema_version: str,
    checks: list[AuditVerificationCheck],
    *,
    root: Path,
    strict: bool,
) -> None:
    provenance = manifest.get("migration_provenance")
    migration_applied = manifest.get("migration_applied")
    if provenance is None and migration_applied is None:
        _add_check(
            checks,
            name="migration_provenance_optional",
            passed=True,
            detail="no migration provenance block present",
            path=root / "manifest.json",
        )
        return
    if migration_applied is not True or not isinstance(provenance, dict):
        _add_check(
            checks,
            name="migration_provenance_shape",
            passed=False,
            detail="migration_applied=true requires a migration_provenance object",
            path=root / "manifest.json",
        )
        return
    required = {
        "schema_version",
        "source_audit_hash",
        "source_schema_version",
        "target_schema_version",
        "classification",
        "transform_ids",
        "notes",
        "lossy_allowed",
        "boundary",
    }
    unknown = sorted(set(provenance) - required)
    missing = sorted(required - set(provenance))
    _add_check(
        checks,
        name="migration_provenance_schema",
        passed=not missing and (not strict or not unknown),
        detail="migration provenance contains the declared schema fields",
        path=root / "manifest.json",
        metadata={"missing": missing, "unknown": unknown},
    )
    _add_check(
        checks,
        name="migration_provenance_target_schema",
        passed=provenance.get("target_schema_version") == audit_schema_version,
        detail="migration target schema matches manifest schema_version",
        path=root / "manifest.json",
    )
    source_hash = provenance.get("source_audit_hash")
    _add_check(
        checks,
        name="migration_provenance_source_hash",
        passed=isinstance(source_hash, str) and _is_sha256(source_hash),
        detail="migration source audit hash is a lowercase sha256 digest",
        path=root / "manifest.json",
    )
    _add_check(
        checks,
        name="migration_provenance_classification",
        passed=provenance.get("classification") in {"lossless", "lossy", "unsupported"},
        detail="migration classification is recognized",
        path=root / "manifest.json",
    )
    _add_check(
        checks,
        name="migration_provenance_transform_ids",
        passed=_string_list(provenance.get("transform_ids")),
        detail="migration transform ids are recorded",
        path=root / "manifest.json",
    )
    _add_check(
        checks,
        name="migration_provenance_notes",
        passed=_string_list(provenance.get("notes")),
        detail="migration notes are recorded as strings",
        path=root / "manifest.json",
    )
    _add_check(
        checks,
        name="migration_provenance_lossy_allowed",
        passed=isinstance(provenance.get("lossy_allowed"), bool),
        detail="migration lossy_allowed is boolean",
        path=root / "manifest.json",
    )


def _check_lineage_schema(
    round_: AuditRound,
    lineage: dict[str, Any],
    audit_schema_version: str,
    checks: list[AuditVerificationCheck],
    *,
    root: Path,
) -> None:
    row_schema = lineage.get("schema_version")
    passed = row_schema == audit_schema_version or (audit_schema_version == "1.0" and row_schema is None)
    _add_check(
        checks,
        name=f"round_{round_.index}_lineage_schema",
        passed=passed,
        detail="lineage row schema_version matches manifest",
        path=root / "lineage.json",
        metadata={"lineage_schema_version": str(row_schema)} if row_schema is not None else None,
    )


def _check_harness_snapshots(
    round_: AuditRound,
    lineage: dict[str, Any],
    previous_after: dict[str, Any] | None,
    checks: list[AuditVerificationCheck],
    *,
    root: Path,
) -> None:
    before_path = root / "rounds" / str(round_.index) / "harness_before.json"
    after_path = root / "rounds" / str(round_.index) / "harness_after.json"
    before_hash = _safe_harness_hash(round_.harness_before)
    after_hash = _safe_harness_hash(round_.harness_after)

    _add_check(
        checks,
        name=f"round_{round_.index}_harness_before_hash",
        passed=before_hash == lineage.get("harness_before_hash"),
        detail="harness_before hash matches lineage",
        path=before_path,
        metadata={
            "expected": str(lineage.get("harness_before_hash")),
            "actual": before_hash or "invalid-harness-shape",
        },
    )
    _add_check(
        checks,
        name=f"round_{round_.index}_harness_after_hash",
        passed=after_hash == lineage.get("harness_after_hash"),
        detail="harness_after hash matches lineage",
        path=after_path,
        metadata={
            "expected": str(lineage.get("harness_after_hash")),
            "actual": after_hash or "invalid-harness-shape",
        },
    )
    if previous_after is not None:
        _add_check(
            checks,
            name=f"round_{round_.index}_harness_continuity",
            passed=round_.harness_before == previous_after,
            detail="round harness_before equals previous harness_after",
            path=before_path,
        )


def _check_proposals(
    round_: AuditRound,
    lineage: dict[str, Any],
    audit_schema_version: str,
    checks: list[AuditVerificationCheck],
    *,
    root: Path,
) -> None:
    proposal_path = root / "rounds" / str(round_.index) / "proposals.jsonl"
    ids = [str(row.get("id", "")) for row in round_.proposals]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    accepted_status_ids = sorted(
        str(row["id"])
        for row in round_.proposals
        if row.get("status") in {"accepted", "merged"} and isinstance(row.get("id"), str)
    )
    lineage_accepted = sorted(_strings(lineage.get("accepted_proposal_ids")))
    schema_mismatches = [
        str(row.get("id", index))
        for index, row in enumerate(round_.proposals)
        if not _row_schema_matches(row, audit_schema_version)
    ]
    leaked_rows = [
        str(row.get("id", index))
        for index, row in enumerate(round_.proposals)
        if _proposal_leaks_held_out(row)
    ]

    _add_check(
        checks,
        name=f"round_{round_.index}_proposal_ids_unique",
        passed=not duplicate_ids and all(ids),
        detail="proposal ids are present and unique",
        path=proposal_path,
        metadata={"duplicates": duplicate_ids} if duplicate_ids else None,
    )
    _add_check(
        checks,
        name=f"round_{round_.index}_accepted_ids_match_lineage",
        passed=accepted_status_ids == lineage_accepted,
        detail="accepted or merged proposal ids match lineage accepted_proposal_ids",
        path=proposal_path,
        metadata={"proposal_status_ids": accepted_status_ids, "lineage_ids": lineage_accepted},
    )
    _add_check(
        checks,
        name=f"round_{round_.index}_proposal_schema_versions",
        passed=not schema_mismatches,
        detail="proposal row schema_version values match manifest",
        path=proposal_path,
        metadata={"mismatched_rows": schema_mismatches} if schema_mismatches else None,
    )
    _add_check(
        checks,
        name=f"round_{round_.index}_proposal_held_out_leakage",
        passed=not leaked_rows,
        detail="proposal rows do not carry held-out pattern or task evidence",
        path=proposal_path,
        metadata={"leaked_rows": leaked_rows} if leaked_rows else None,
    )


def _check_evaluations(
    round_: AuditRound,
    audit_schema_version: str,
    checks: list[AuditVerificationCheck],
    *,
    root: Path,
) -> None:
    evaluation_path = root / "rounds" / str(round_.index) / "evaluations.jsonl"
    schema_mismatches = [
        str(index)
        for index, row in enumerate(round_.evaluations)
        if not _row_schema_matches(row, audit_schema_version)
    ]
    baseline_missing = _missing_split_totals(round_.evaluations, proposal_id="__baseline__", arm="baseline")
    committed_id, committed_arm = _committed_eval_selector(round_)
    committed_missing = _missing_split_totals(round_.evaluations, proposal_id=committed_id, arm=committed_arm)

    _add_check(
        checks,
        name=f"round_{round_.index}_evaluation_schema_versions",
        passed=not schema_mismatches,
        detail="evaluation row schema_version values match manifest",
        path=evaluation_path,
        metadata={"mismatched_rows": schema_mismatches} if schema_mismatches else None,
    )
    _add_check(
        checks,
        name=f"round_{round_.index}_baseline_split_totals",
        passed=not baseline_missing,
        detail="baseline held-in and held-out split totals are present",
        path=evaluation_path,
        metadata={"missing": baseline_missing} if baseline_missing else None,
    )
    _add_check(
        checks,
        name=f"round_{round_.index}_committed_split_totals",
        passed=not committed_missing,
        detail="committed after-state split totals are present",
        path=evaluation_path,
        metadata={
            "proposal_id": committed_id,
            "arm": committed_arm,
            "missing": committed_missing,
        } if committed_missing else {"proposal_id": committed_id, "arm": committed_arm},
    )


def _committed_eval_selector(round_: AuditRound) -> tuple[str, str]:
    if any(row.get("status") == "merged" for row in round_.proposals):
        return "__merge__", "candidate"
    accepted = sorted(str(row["id"]) for row in round_.proposals if row.get("status") == "accepted" and "id" in row)
    if accepted:
        return accepted[0], "candidate"
    return "__baseline__", "baseline"


def _missing_split_totals(rows: list[dict[str, Any]], *, proposal_id: str, arm: str) -> list[str]:
    missing: list[str] = []
    for split in ("held_in", "held_out"):
        if not any(
            row.get("proposal_id") == proposal_id
            and row.get("arm") == arm
            and row.get("task_id") == "__split_total__"
            and row.get("split") == split
            for row in rows
        ):
            missing.append(split)
    return missing


def _row_schema_matches(row: dict[str, Any], audit_schema_version: str) -> bool:
    row_schema = row.get("schema_version")
    return row_schema == audit_schema_version or (audit_schema_version == "1.0" and row_schema is None)


def _proposal_leaks_held_out(row: dict[str, Any]) -> bool:
    pattern_id = row.get("pattern_id")
    if isinstance(pattern_id, str) and pattern_id.replace("-", "_").startswith("held_out"):
        return True
    for key, value in row.items():
        if key == "task_ids" or key.endswith("_task_ids"):
            if any(item.replace("-", "_").startswith("held_out") for item in _strings(value)):
                return True
    return False


def _safe_harness_hash(value: dict[str, Any]) -> str | None:
    try:
        return harness_hash(_harness_from_json(value))
    except AuditVerificationError:
        return None


def _harness_from_json(value: dict[str, Any]) -> HarnessSpec:
    required = {
        "system_prompt": str,
        "bootstrap": str,
        "execution": str,
        "verification": str,
        "failure_recovery": str,
        "runtime_policy": dict,
        "tools": list,
        "skills": list,
        "memory_sources": list,
        "subagents": list,
    }
    for key, expected_type in required.items():
        if key not in value or not isinstance(value[key], expected_type):
            raise AuditVerificationError(f"harness snapshot missing valid {key}")
    if not all(isinstance(item, str) for item in value["tools"]):
        raise AuditVerificationError("harness tools must be strings")
    if not all(isinstance(item, str) for item in value["skills"]):
        raise AuditVerificationError("harness skills must be strings")
    if not all(isinstance(item, str) for item in value["memory_sources"]):
        raise AuditVerificationError("harness memory_sources must be strings")
    if not all(isinstance(item, dict) for item in value["subagents"]):
        raise AuditVerificationError("harness subagents must be objects")
    return HarnessSpec(
        system_prompt=value["system_prompt"],
        bootstrap=value["bootstrap"],
        execution=value["execution"],
        verification=value["verification"],
        failure_recovery=value["failure_recovery"],
        runtime_policy=dict(value["runtime_policy"]),
        tools=list(value["tools"]),
        skills=list(value["skills"]),
        memory_sources=list(value["memory_sources"]),
        subagents=[dict(item) for item in value["subagents"]],
    )


def _report(
    *,
    audit_schema_version: str,
    root: Path,
    ok: bool,
    checks: tuple[AuditVerificationCheck, ...],
    audit: AuditRun,
) -> AuditVerificationReport:
    auditability = _auditability_metadata(audit, checks)
    report_without_hash = {
        "schema_version": AUDIT_VERIFICATION_SCHEMA_VERSION,
        "audit_schema_version": audit_schema_version,
        "path": str(root),
        "ok": ok,
        "mode": "replay",
        "reproduction_claimed": False,
        **auditability,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "metadata": check.metadata,
            }
            for check in checks
        ],
        "boundary": AUDIT_VERIFICATION_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return AuditVerificationReport(
        schema_version=AUDIT_VERIFICATION_SCHEMA_VERSION,
        audit_schema_version=audit_schema_version,
        path=str(root),
        ok=ok,
        mode="replay",
        reproduction_claimed=False,
        held_out_leakage=bool(auditability["held_out_leakage"]),
        proposer_evidence_inspected=bool(auditability["proposer_evidence_inspected"]),
        changed_surfaces_recorded=bool(auditability["changed_surfaces_recorded"]),
        evaluation_repeats_recorded=bool(auditability["evaluation_repeats_recorded"]),
        rejected_reasons_recorded=bool(auditability["rejected_reasons_recorded"]),
        checks=checks,
        report_hash=report_hash,
        boundary=AUDIT_VERIFICATION_BOUNDARY,
    )


def _auditability_metadata(audit: AuditRun, checks: tuple[AuditVerificationCheck, ...]) -> dict[str, bool]:
    leakage_checks = [check for check in checks if check.name.endswith("_proposal_held_out_leakage")]
    return {
        "held_out_leakage": any(check.status == "fail" for check in leakage_checks),
        "proposer_evidence_inspected": bool(leakage_checks),
        "changed_surfaces_recorded": _changed_surfaces_recorded(audit),
        "evaluation_repeats_recorded": _evaluation_repeats_recorded(audit),
        "rejected_reasons_recorded": _rejected_reasons_recorded(audit),
    }


def _changed_surfaces_recorded(audit: AuditRun) -> bool:
    for round_ in audit.rounds:
        for row in round_.proposals:
            value = row.get("changed_surfaces")
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                return False
    return True


def _evaluation_repeats_recorded(audit: AuditRun) -> bool:
    manifest_repeats = audit.manifest.get("evaluation_repeats")
    if not isinstance(manifest_repeats, int) or manifest_repeats < 1:
        return False
    for round_ in audit.rounds:
        for row in [*round_.proposals, *round_.evaluations]:
            value = row.get("evaluation_repeats")
            if not isinstance(value, int) or value < 1:
                return False
    return True


def _rejected_reasons_recorded(audit: AuditRun) -> bool:
    for round_ in audit.rounds:
        for row in round_.proposals:
            status = row.get("status")
            if status in {"invalid", "rejected", "superseded"}:
                decision_reason = row.get("decision_reason")
                rejection_reason = row.get("rejection_reason")
                if not isinstance(decision_reason, str) or not decision_reason:
                    return False
                if not isinstance(rejection_reason, str) or not rejection_reason:
                    return False
    return True


def _add_check(
    checks: list[AuditVerificationCheck],
    *,
    name: str,
    passed: bool,
    detail: str,
    path: Path | None,
    metadata: dict[str, object] | None = None,
) -> None:
    checks.append(
        AuditVerificationCheck(
            name=name,
            status="pass" if passed else "fail",
            detail=detail,
            path=str(path) if path is not None else None,
            metadata=metadata,
        )
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
