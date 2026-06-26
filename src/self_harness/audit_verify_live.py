from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from self_harness._artifact_shapes import artifact_shape_error, artifact_shape_error_from_payload
from self_harness.audit import load_audit_run
from self_harness.audit_verify import (
    AUDIT_VERIFICATION_SCHEMA_VERSION,
    AuditVerificationReport,
    audit_verification_report_to_jsonable,
    verify_audit_run,
)
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError
from self_harness.reproduction_bundle import (
    REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
    REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
)
from self_harness.types import stable_json_dumps

LIVE_AUDIT_PROVENANCE_SCHEMA_VERSION = "1.0"
LIVE_AUDIT_VERIFY_BOUNDARY = (
    "offline live audit provenance verification only; validates an existing audit directory against "
    "an operator-supplied live Harbor audit artifact and detached Ed25519 provenance signature, "
    "does not execute tasks, invoke models, contact Harbor, Docker, registries, scanners, PyPI, "
    "Sigstore, model providers, or cloud providers, and never claims benchmark reproduction"
)

_PROVENANCE_FIELDS = frozenset(
    {
        "schema_version",
        "capture_run_id",
        "harbor_version",
        "captured_at",
        "operator_label",
        "live_harbor_audit_artifact_path",
        "reproduction_claimed",
    }
)
_SIGNATURE_FIELDS = frozenset(
    {
        "schema_version",
        "manifest_sha256",
        "signature_algorithm",
        "signature_b64",
        "public_key_b64",
        "fingerprint",
        "fingerprint_algorithm",
        "provider",
        "key_id",
        "manifest_filename",
    }
)


class LiveAuditVerificationError(ValueError):
    """Raised when live audit verification inputs are malformed."""


@dataclass(frozen=True)
class LiveAuditProvenance:
    schema_version: str
    capture_run_id: str
    harbor_version: str
    captured_at: str
    operator_label: str
    live_harbor_audit_artifact_path: str
    reproduction_claimed: bool


@dataclass(frozen=True)
class LiveAuditVerificationCheck:
    name: str
    status: str
    detail: str
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class LiveAuditVerificationReport:
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
    checks: tuple[LiveAuditVerificationCheck, ...]
    report_hash: str
    boundary: str
    replay_report_hash: str | None
    provenance_sha256: str | None
    provenance_fingerprint: str | None
    capture_run_id: str | None
    live_harbor_audit_artifact_path: str


def load_live_audit_provenance(path: Path) -> LiveAuditProvenance:
    data = _read_json_object(path, label="live audit provenance")
    unknown = sorted(set(data) - _PROVENANCE_FIELDS)
    if unknown:
        raise LiveAuditVerificationError(f"live audit provenance has unknown field(s): {', '.join(unknown)}")
    schema_version = _required_str(data, "schema_version", label="live audit provenance")
    if schema_version != LIVE_AUDIT_PROVENANCE_SCHEMA_VERSION:
        raise LiveAuditVerificationError(f"unsupported live audit provenance schema_version: {schema_version}")
    if data.get("reproduction_claimed") is not False:
        raise LiveAuditVerificationError("live audit provenance reproduction_claimed must be false")
    return LiveAuditProvenance(
        schema_version=schema_version,
        capture_run_id=_required_str(data, "capture_run_id", label="live audit provenance"),
        harbor_version=_required_str(data, "harbor_version", label="live audit provenance"),
        captured_at=_required_str(data, "captured_at", label="live audit provenance"),
        operator_label=_required_str(data, "operator_label", label="live audit provenance"),
        live_harbor_audit_artifact_path=_required_str(
            data,
            "live_harbor_audit_artifact_path",
            label="live audit provenance",
        ),
        reproduction_claimed=False,
    )


def verify_live_audit_run(
    audit_dir: Path,
    *,
    live_harbor_audit: Path,
    provenance: Path,
    provenance_signature: Path | None = None,
    public_key: Path | str | None = None,
    require_signature: bool = False,
    strict_migration: bool = True,
) -> LiveAuditVerificationReport:
    """Verify replay audit integrity plus signed live Harbor provenance without contacting live services."""

    checks: list[LiveAuditVerificationCheck] = []
    replay_report = verify_audit_run(audit_dir, strict_migration=strict_migration)
    _append_replay_check(checks, replay_report)

    provenance_document: LiveAuditProvenance | None = None
    provenance_bytes: bytes | None = None
    provenance_sha256: str | None = None
    provenance_fingerprint: str | None = None
    try:
        provenance_document = load_live_audit_provenance(provenance)
        provenance_bytes = provenance.read_bytes()
        provenance_sha256 = sha256(provenance_bytes).hexdigest()
        checks.append(_pass("provenance_schema", "live audit provenance schema loaded", path=provenance))
    except (OSError, LiveAuditVerificationError) as exc:
        checks.append(_fail("provenance_schema", str(exc), path=provenance))

    if provenance_document is not None:
        checks.append(_provenance_path_check(provenance, provenance_document, live_harbor_audit))

    signature_fingerprint = _signature_check(
        provenance,
        provenance_bytes,
        provenance_signature,
        public_key,
        require_signature=require_signature,
    )
    checks.append(signature_fingerprint[0])
    provenance_fingerprint = signature_fingerprint[1]

    live_payload: Mapping[str, Any] | None = None
    try:
        live_payload = _read_json_object(live_harbor_audit, label="live Harbor audit")
        shape_error = artifact_shape_error("live_harbor_audit", live_harbor_audit)
        if shape_error is not None:
            checks.append(_fail("live_harbor_audit_shape", shape_error, path=live_harbor_audit))
        else:
            checks.append(_pass("live_harbor_audit_shape", "live Harbor audit shape accepted", path=live_harbor_audit))
    except (OSError, json.JSONDecodeError, LiveAuditVerificationError) as exc:
        checks.append(_fail("live_harbor_audit_shape", str(exc), path=live_harbor_audit))

    if provenance_document is not None and live_payload is not None:
        checks.append(_provenance_capture_run_check(provenance_document, live_harbor_audit, live_payload))

    if live_payload is not None:
        checks.append(_task_binding_check(audit_dir, live_harbor_audit, live_payload))

    if replay_report.reproduction_claimed:
        checks.append(
            _fail("audit_reproduction_claim", "source audit unexpectedly claimed reproduction", path=audit_dir)
        )
    else:
        checks.append(_pass("audit_reproduction_claim", "source audit does not claim reproduction", path=audit_dir))

    ok = all(check.status == "pass" for check in checks)
    mode = "live" if ok else "live_blocked"
    return _report(
        audit_dir=audit_dir,
        live_harbor_audit=live_harbor_audit,
        replay_report=replay_report,
        checks=tuple(checks),
        ok=ok,
        mode=mode,
        provenance=provenance_document,
        provenance_sha256=provenance_sha256,
        provenance_fingerprint=provenance_fingerprint,
    )


def live_audit_verification_report_to_jsonable(report: LiveAuditVerificationReport) -> dict[str, object]:
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
        "checks": [_check_to_jsonable(check) for check in report.checks],
        "report_hash": report.report_hash,
        "boundary": report.boundary,
        "replay_report_hash": report.replay_report_hash,
        "provenance_sha256": report.provenance_sha256,
        "provenance_fingerprint": report.provenance_fingerprint,
        "capture_run_id": report.capture_run_id,
        "live_harbor_audit_artifact_path": report.live_harbor_audit_artifact_path,
    }


def _append_replay_check(checks: list[LiveAuditVerificationCheck], replay_report: AuditVerificationReport) -> None:
    metadata = {
        "mode": replay_report.mode,
        "report_hash": replay_report.report_hash,
        "held_out_leakage": replay_report.held_out_leakage,
        "proposer_evidence_inspected": replay_report.proposer_evidence_inspected,
        "changed_surfaces_recorded": replay_report.changed_surfaces_recorded,
        "evaluation_repeats_recorded": replay_report.evaluation_repeats_recorded,
        "rejected_reasons_recorded": replay_report.rejected_reasons_recorded,
    }
    if replay_report.ok:
        checks.append(_pass("replay_audit_verify", "replay audit verification passed", metadata=metadata))
    else:
        checks.append(_fail("replay_audit_verify", "replay audit verification failed", metadata=metadata))


def _provenance_path_check(
    provenance_path: Path,
    provenance: LiveAuditProvenance,
    live_harbor_audit: Path,
) -> LiveAuditVerificationCheck:
    try:
        recorded = _resolve_recorded_path(provenance_path, provenance.live_harbor_audit_artifact_path)
        supplied = live_harbor_audit.resolve()
    except OSError as exc:
        return _fail("provenance_artifact_binding", str(exc), path=provenance_path)
    if recorded != supplied:
        return _fail(
            "provenance_artifact_binding",
            "provenance live_harbor_audit_artifact_path must resolve to the supplied artifact",
            path=provenance_path,
            metadata={"recorded": str(recorded), "supplied": str(supplied)},
        )
    return _pass("provenance_artifact_binding", "provenance points at the supplied live Harbor audit artifact")


def _provenance_capture_run_check(
    provenance: LiveAuditProvenance,
    live_harbor_audit: Path,
    live_payload: Mapping[str, Any],
) -> LiveAuditVerificationCheck:
    try:
        live_capture_run_id = _required_str(live_payload, "capture_run_id", label="live Harbor audit")
    except LiveAuditVerificationError as exc:
        return _fail("provenance_capture_run_binding", str(exc), path=live_harbor_audit)
    metadata: dict[str, object] = {
        "provenance_capture_run_id": provenance.capture_run_id,
        "live_harbor_audit_capture_run_id": live_capture_run_id,
    }
    if live_capture_run_id != provenance.capture_run_id:
        return _fail(
            "provenance_capture_run_binding",
            "live Harbor audit capture_run_id must match signed provenance capture_run_id",
            path=live_harbor_audit,
            metadata=metadata,
        )
    return _pass(
        "provenance_capture_run_binding",
        "live Harbor audit capture_run_id matches signed provenance",
        path=live_harbor_audit,
        metadata=metadata,
    )


def _signature_check(
    provenance: Path,
    provenance_bytes: bytes | None,
    signature_path: Path | None,
    public_key: Path | str | None,
    *,
    require_signature: bool,
) -> tuple[LiveAuditVerificationCheck, str | None]:
    if provenance_bytes is None:
        return _fail("provenance_signature", "provenance bytes were unavailable for signature verification"), None
    if signature_path is None:
        if require_signature:
            return _fail("provenance_signature", "detached provenance signature is required"), None
        return _fail("provenance_signature", "detached provenance signature is required to emit mode=live"), None
    try:
        signature = _load_signature_sidecar(signature_path)
        _verify_signature_sidecar(
            provenance,
            provenance_bytes,
            signature,
            public_key=public_key,
        )
    except (OSError, LiveAuditVerificationError, CorpusSigningError) as exc:
        return _fail("provenance_signature", str(exc), path=signature_path), None
    return _pass(
        "provenance_signature",
        "detached provenance signature verified",
        path=signature_path,
        metadata={"fingerprint": str(signature["fingerprint"]), "provider": str(signature["provider"])},
    ), str(signature["fingerprint"])


def _verify_signature_sidecar(
    provenance: Path,
    provenance_bytes: bytes,
    signature: Mapping[str, Any],
    *,
    public_key: Path | str | None,
) -> None:
    if signature.get("schema_version") != REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION:
        raise LiveAuditVerificationError("signature sidecar schema_version must be 1")
    if signature.get("signature_algorithm") != REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM:
        raise LiveAuditVerificationError("signature sidecar signature_algorithm must be ed25519")
    if signature.get("fingerprint_algorithm") != FINGERPRINT_ALGORITHM:
        raise LiveAuditVerificationError("signature sidecar fingerprint_algorithm is unsupported")
    if signature.get("manifest_filename") != provenance.name:
        raise LiveAuditVerificationError("signature sidecar manifest_filename must match provenance filename")
    if signature.get("manifest_sha256") != sha256(provenance_bytes).hexdigest():
        raise LiveAuditVerificationError("signature sidecar manifest_sha256 does not match provenance bytes")
    signature_b64 = _required_str(signature, "signature_b64", label="signature sidecar")
    public_key_b64 = _required_str(signature, "public_key_b64", label="signature sidecar")
    fingerprint = _required_str(signature, "fingerprint", label="signature sidecar")
    if public_key_fingerprint(public_key_b64) != fingerprint:
        raise CorpusSigningError("signature sidecar public key fingerprint does not match fingerprint")
    if public_key is not None and public_key_fingerprint(public_key) != fingerprint:
        raise CorpusSigningError("trusted public key fingerprint does not match signature sidecar fingerprint")
    verification_key = public_key if public_key is not None else public_key_b64
    verify_bytes_signature(provenance_bytes, signature_b64, verification_key)


def _task_binding_check(
    audit_dir: Path,
    live_harbor_audit: Path,
    live_payload: Mapping[str, Any],
) -> LiveAuditVerificationCheck:
    try:
        audit_task_hashes = _audit_task_hashes(audit_dir)
        live_task_hashes = _live_task_hashes(live_payload)
    except (OSError, json.JSONDecodeError) as exc:
        return _fail("live_harbor_audit_task_binding", str(exc), path=live_harbor_audit)
    audit_tasks = sorted(audit_task_hashes)
    live_tasks = sorted(live_task_hashes)
    if not audit_tasks:
        return _fail("live_harbor_audit_task_binding", "audit evaluations contain no task rows", path=audit_dir)
    if audit_tasks != live_tasks:
        return _fail(
            "live_harbor_audit_task_binding",
            "audit task ids must match live Harbor audit trial artifact task ids exactly",
            path=live_harbor_audit,
            metadata={"audit_task_ids": audit_tasks, "live_task_ids": live_tasks},
        )
    mismatches: list[dict[str, str]] = []
    for task_id, audit_hash in audit_task_hashes.items():
        if audit_hash is None:
            continue
        live_hash = live_task_hashes[task_id]
        if live_hash != audit_hash:
            mismatches.append(
                {
                    "task_id": task_id,
                    "audit_task_source_hash": audit_hash,
                    "live_task_source_hash": str(live_hash),
                }
            )
    if mismatches:
        return _fail(
            "live_harbor_audit_task_binding",
            "audit task_source_hash values must match live Harbor audit trial artifacts when present",
            path=live_harbor_audit,
            metadata={"mismatches": mismatches},
        )
    return _pass(
        "live_harbor_audit_task_binding",
        "audit task ids and available task_source_hash values bind to the live Harbor audit artifact",
        path=live_harbor_audit,
        metadata={"task_count": len(audit_tasks)},
    )


def _audit_task_hashes(audit_dir: Path) -> dict[str, str | None]:
    audit = load_audit_run(audit_dir)
    task_hashes: dict[str, str | None] = {}
    for round_ in audit.rounds:
        for row in round_.evaluations:
            task_id = row.get("task_id")
            if not isinstance(task_id, str) or not task_id or task_id == "__split_total__":
                continue
            source_hash = row.get("task_source_hash")
            existing = task_hashes.get(task_id)
            if isinstance(source_hash, str) and source_hash:
                if existing is not None and existing != source_hash:
                    raise LiveAuditVerificationError(f"conflicting task_source_hash values for task: {task_id}")
                task_hashes[task_id] = source_hash
            elif task_id not in task_hashes:
                task_hashes[task_id] = None
    return task_hashes


def _live_task_hashes(live_payload: Mapping[str, Any]) -> dict[str, str | None]:
    artifacts = live_payload.get("trial_artifacts")
    if not isinstance(artifacts, list):
        raise LiveAuditVerificationError("live Harbor audit trial_artifacts must be a list")
    task_hashes: dict[str, str | None] = {}
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise LiveAuditVerificationError(f"live Harbor audit trial_artifacts[{index}] must be an object")
        task_id = artifact.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise LiveAuditVerificationError(f"live Harbor audit trial_artifacts[{index}].task_id must be non-empty")
        if task_id in task_hashes:
            raise LiveAuditVerificationError(f"duplicate live Harbor audit task_id: {task_id}")
        source_hash = artifact.get("task_source_hash")
        task_hashes[task_id] = source_hash if isinstance(source_hash, str) and source_hash else None
    return task_hashes


def _report(
    *,
    audit_dir: Path,
    live_harbor_audit: Path,
    replay_report: AuditVerificationReport,
    checks: tuple[LiveAuditVerificationCheck, ...],
    ok: bool,
    mode: str,
    provenance: LiveAuditProvenance | None,
    provenance_sha256: str | None,
    provenance_fingerprint: str | None,
) -> LiveAuditVerificationReport:
    report_without_hash = {
        "schema_version": AUDIT_VERIFICATION_SCHEMA_VERSION,
        "audit_schema_version": replay_report.audit_schema_version,
        "path": str(audit_dir),
        "ok": ok,
        "mode": mode,
        "reproduction_claimed": False,
        "held_out_leakage": replay_report.held_out_leakage,
        "proposer_evidence_inspected": replay_report.proposer_evidence_inspected,
        "changed_surfaces_recorded": replay_report.changed_surfaces_recorded,
        "evaluation_repeats_recorded": replay_report.evaluation_repeats_recorded,
        "rejected_reasons_recorded": replay_report.rejected_reasons_recorded,
        "checks": [_check_to_jsonable(check) for check in checks],
        "boundary": LIVE_AUDIT_VERIFY_BOUNDARY,
        "replay_report_hash": replay_report.report_hash,
        "provenance_sha256": provenance_sha256,
        "provenance_fingerprint": provenance_fingerprint,
        "capture_run_id": provenance.capture_run_id if provenance is not None else None,
        "live_harbor_audit_artifact_path": str(live_harbor_audit),
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return LiveAuditVerificationReport(
        schema_version=AUDIT_VERIFICATION_SCHEMA_VERSION,
        audit_schema_version=replay_report.audit_schema_version,
        path=str(audit_dir),
        ok=ok,
        mode=mode,
        reproduction_claimed=False,
        held_out_leakage=replay_report.held_out_leakage,
        proposer_evidence_inspected=replay_report.proposer_evidence_inspected,
        changed_surfaces_recorded=replay_report.changed_surfaces_recorded,
        evaluation_repeats_recorded=replay_report.evaluation_repeats_recorded,
        rejected_reasons_recorded=replay_report.rejected_reasons_recorded,
        checks=checks,
        report_hash=report_hash,
        boundary=LIVE_AUDIT_VERIFY_BOUNDARY,
        replay_report_hash=replay_report.report_hash,
        provenance_sha256=provenance_sha256,
        provenance_fingerprint=provenance_fingerprint,
        capture_run_id=provenance.capture_run_id if provenance is not None else None,
        live_harbor_audit_artifact_path=str(live_harbor_audit),
    )


def assert_live_audit_verification_shape(report: LiveAuditVerificationReport) -> None:
    error = artifact_shape_error_from_payload("audit_verify_report", live_audit_verification_report_to_jsonable(report))
    if error is not None:
        raise LiveAuditVerificationError(error)


def _load_signature_sidecar(path: Path) -> Mapping[str, Any]:
    data = _read_json_object(path, label="signature sidecar")
    unknown = sorted(set(data) - _SIGNATURE_FIELDS)
    if unknown:
        raise LiveAuditVerificationError(f"signature sidecar has unknown field(s): {', '.join(unknown)}")
    for key in _SIGNATURE_FIELDS:
        if key not in data:
            raise LiveAuditVerificationError(f"signature sidecar missing required field: {key}")
    return data


def _read_json_object(path: Path, *, label: str) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise LiveAuditVerificationError(f"{label} must be a JSON object")
    return cast(Mapping[str, Any], data)


def _required_str(data: Mapping[str, Any], key: str, *, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise LiveAuditVerificationError(f"{label} {key} must be a non-empty string")
    return value


def _resolve_recorded_path(provenance: Path, value: str) -> Path:
    recorded = Path(value)
    if recorded.is_absolute():
        return recorded.resolve()
    return (provenance.resolve().parent / recorded).resolve()


def _check_to_jsonable(check: LiveAuditVerificationCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "path": check.path,
        "metadata": check.metadata,
    }


def _pass(
    name: str,
    detail: str,
    *,
    path: Path | None = None,
    metadata: dict[str, object] | None = None,
) -> LiveAuditVerificationCheck:
    return LiveAuditVerificationCheck(
        name=name,
        status="pass",
        detail=detail,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _fail(
    name: str,
    detail: str,
    *,
    path: Path | None = None,
    metadata: dict[str, object] | None = None,
) -> LiveAuditVerificationCheck:
    return LiveAuditVerificationCheck(
        name=name,
        status="fail",
        detail=detail,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def replay_audit_report_to_jsonable(report: AuditVerificationReport) -> dict[str, object]:
    return audit_verification_report_to_jsonable(report)
