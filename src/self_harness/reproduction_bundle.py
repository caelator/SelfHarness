from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from self_harness._artifact_shapes import PAPER_MODEL_BACKENDS, _normal_model_backends, artifact_shape_error
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_raw_b64,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError
from self_harness.reproduction_readiness import ReproductionRequirement
from self_harness.types import stable_json_dumps

REPRODUCTION_BUNDLE_SCHEMA_VERSION = "1.0"
REPRODUCTION_BUNDLE_REPORT_SCHEMA_VERSION = "1.0"
REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION = 1
REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM = "ed25519"
REPRODUCTION_BUNDLE_BOUNDARY = (
    "benchmark reproduction evidence bundle verification only; validates operator-supplied "
    "artifact paths, byte sizes, sha256 digests, optional detached Ed25519 signatures, and "
    "class-specific live evidence shapes without contacting Harbor, Docker, registries, "
    "scanners, PyPI, Sigstore, scanner databases, model providers, or cloud services, and never "
    "claims benchmark reproduction"
)

_BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "bundle_id",
        "created_at",
        "operator_label",
        "entries",
        "reproduction_claimed",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "required_artifact_class",
        "path",
        "sha256",
        "byte_size",
        "source",
        "notes",
    }
)
_SOURCE_FIELDS = frozenset({"provider", "url", "captured_at", "operator_label"})
_PRIMARY_CAPTURED_ARTIFACT_CLASSES = frozenset(
    {
        "live_terminal_bench_split_manifest",
        "live_two_repeat_evaluation_report",
        "fixed_protocol_config",
        "live_harbor_preflight_report",
        "container_image_trust_report",
        "model_backend_preflight_report",
        "proposer_llm_request_log",
        "proposer_context_manifest",
        "proposal_validation_manifest",
        "network_resource_controls_attestation",
        "live_harbor_audit",
    }
)
_PROPOSAL_VALIDATION_FAILURE_CATEGORIES = frozenset({"no_editable_surface", "execution_failure"})
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


class ReproductionBundleError(ValueError):
    """Raised when a reproduction evidence bundle is malformed."""


@dataclass(frozen=True)
class ReproductionBundleEntry:
    required_artifact_class: str
    path: str
    sha256: str
    byte_size: int
    source: dict[str, str]
    notes: str | None = None


@dataclass(frozen=True)
class ReproductionBundle:
    schema_version: str
    bundle_id: str
    created_at: str
    operator_label: str
    entries: tuple[ReproductionBundleEntry, ...]
    path: Path
    reproduction_claimed: bool


@dataclass(frozen=True)
class ReproductionBundleCheck:
    name: str
    status: str
    detail: str
    artifact_class: str | None = None
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ReproductionBundleReport:
    schema_version: str
    ok: bool
    bundle_path: str
    bundle_id: str | None
    bundle_sha256: str | None
    checks: tuple[ReproductionBundleCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str


@dataclass(frozen=True)
class _TrustImageDigestBinding:
    manifest_digests: frozenset[str]
    child_digests: frozenset[str]
    child_digest_map: tuple[dict[str, object], ...]
    mixed_child_digest_declarations: dict[str, object] | None


def load_reproduction_bundle(path: Path) -> ReproductionBundle:
    data = _read_json_object(path, label="reproduction evidence bundle")
    unknown = sorted(set(data) - _BUNDLE_FIELDS)
    if unknown:
        raise ReproductionBundleError(f"reproduction bundle has unknown field(s): {', '.join(unknown)}")
    schema_version = _required_str(data, "schema_version", label="reproduction bundle")
    if schema_version != REPRODUCTION_BUNDLE_SCHEMA_VERSION:
        raise ReproductionBundleError(f"unsupported reproduction bundle schema_version: {schema_version}")
    if data.get("reproduction_claimed") is not False:
        raise ReproductionBundleError("reproduction bundle reproduction_claimed must be false")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ReproductionBundleError("reproduction bundle entries must be a non-empty list")
    return ReproductionBundle(
        schema_version=schema_version,
        bundle_id=_required_str(data, "bundle_id", label="reproduction bundle"),
        created_at=_required_str(data, "created_at", label="reproduction bundle"),
        operator_label=_required_str(data, "operator_label", label="reproduction bundle"),
        entries=tuple(_entry_from_json(row, index=index) for index, row in enumerate(raw_entries)),
        path=path,
        reproduction_claimed=False,
    )


def verify_reproduction_bundle(
    bundle_path: Path,
    requirements: Sequence[ReproductionRequirement],
    *,
    signature_path: Path | None = None,
    public_key: Path | str | None = None,
    require_signature: bool = False,
) -> ReproductionBundleReport:
    checks: list[ReproductionBundleCheck] = []
    bundle: ReproductionBundle | None = None
    bundle_sha256: str | None = None
    try:
        bundle = load_reproduction_bundle(bundle_path)
        bundle_bytes = bundle_path.read_bytes()
        bundle_sha256 = sha256(bundle_bytes).hexdigest()
        checks.append(_pass("bundle_schema", "bundle schema loaded", path=bundle_path))
    except (OSError, ReproductionBundleError) as exc:
        checks.append(_fail("bundle_schema", str(exc), path=bundle_path))

    if bundle is not None and bundle_sha256 is not None:
        checks.extend(_signature_checks(bundle_path, signature_path, public_key, require_signature=require_signature))
        entry_checks = _entry_checks(bundle, requirements)
        checks.extend(entry_checks)
        if all(check.status != "fail" for check in entry_checks):
            checks.extend(_cross_artifact_invariants(bundle))

    ok = all(check.status != "fail" for check in checks)
    report_without_hash = {
        "schema_version": REPRODUCTION_BUNDLE_REPORT_SCHEMA_VERSION,
        "ok": ok,
        "bundle_path": str(bundle_path),
        "bundle_id": bundle.bundle_id if bundle is not None else None,
        "bundle_sha256": bundle_sha256,
        "checks": [_check_to_jsonable(check) for check in checks],
        "reproduction_claimed": False,
        "boundary": REPRODUCTION_BUNDLE_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return ReproductionBundleReport(
        schema_version=REPRODUCTION_BUNDLE_REPORT_SCHEMA_VERSION,
        ok=ok,
        bundle_path=str(bundle_path),
        bundle_id=bundle.bundle_id if bundle is not None else None,
        bundle_sha256=bundle_sha256,
        checks=tuple(checks),
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=REPRODUCTION_BUNDLE_BOUNDARY,
    )


def reproduction_bundle_report_to_jsonable(report: ReproductionBundleReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "bundle_path": report.bundle_path,
        "bundle_id": report.bundle_id,
        "bundle_sha256": report.bundle_sha256,
        "checks": [_check_to_jsonable(check) for check in report.checks],
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def reproduction_bundle_artifact_index(bundle: ReproductionBundle) -> dict[str, list[Path]]:
    return {
        entry.required_artifact_class: [resolve_bundle_entry_path(bundle.path, entry)]
        for entry in bundle.entries
    }


def resolve_bundle_entry_path(bundle_path: Path, entry: ReproductionBundleEntry) -> Path:
    path = Path(entry.path)
    if path.is_absolute():
        raise ReproductionBundleError(f"reproduction bundle entry path must be relative: {entry.path}")
    bundle_dir = bundle_path.resolve().parent
    resolved = (bundle_dir / path).resolve()
    try:
        resolved.relative_to(bundle_dir)
    except ValueError as exc:
        raise ReproductionBundleError(f"reproduction bundle entry path escapes bundle directory: {entry.path}") from exc
    return resolved


def read_artifact_payload(bundle: ReproductionBundle, artifact_class: str) -> dict[str, object]:
    """Read one bundled artifact JSON payload by class."""

    entries = [entry for entry in bundle.entries if entry.required_artifact_class == artifact_class]
    if not entries:
        raise ReproductionBundleError(f"reproduction bundle missing artifact class: {artifact_class}")
    if len(entries) > 1:
        raise ReproductionBundleError(f"reproduction bundle has duplicate artifact class: {artifact_class}")
    path = resolve_bundle_entry_path(bundle.path, entries[0])
    return _read_json_object(path, label=artifact_class)


def _entry_checks(
    bundle: ReproductionBundle,
    requirements: Sequence[ReproductionRequirement],
) -> list[ReproductionBundleCheck]:
    required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
    required_set = frozenset(required_classes)
    by_class: dict[str, list[ReproductionBundleEntry]] = {}
    checks: list[ReproductionBundleCheck] = []
    for entry in bundle.entries:
        by_class.setdefault(entry.required_artifact_class, []).append(entry)

    missing = [artifact_class for artifact_class in required_classes if artifact_class not in by_class]
    extras = sorted(set(by_class) - required_set)
    duplicates = sorted(artifact_class for artifact_class, entries in by_class.items() if len(entries) > 1)
    if missing or extras or duplicates:
        parts: list[str] = []
        if missing:
            parts.append("missing required class(es): " + ", ".join(missing))
        if extras:
            parts.append("unknown class(es): " + ", ".join(extras))
        if duplicates:
            parts.append("duplicate class(es): " + ", ".join(duplicates))
        checks.append(_fail("class_coverage", "; ".join(parts), metadata={"required_classes": list(required_classes)}))
    else:
        checks.append(_pass("class_coverage", "bundle contains exactly one entry per required artifact class"))

    for artifact_class in sorted(by_class):
        for entry in by_class[artifact_class]:
            checks.append(_artifact_check(bundle.path, entry, known_class=artifact_class in required_set))
    return checks


def _artifact_check(
    bundle_path: Path,
    entry: ReproductionBundleEntry,
    *,
    known_class: bool,
) -> ReproductionBundleCheck:
    try:
        path = resolve_bundle_entry_path(bundle_path, entry)
        payload = path.read_bytes()
    except (OSError, ReproductionBundleError) as exc:
        return _fail(
            _entry_check_name(entry.required_artifact_class),
            str(exc),
            artifact_class=entry.required_artifact_class,
            path=entry.path,
        )
    if not payload:
        return _fail(
            _entry_check_name(entry.required_artifact_class),
            "artifact file must be non-empty",
            artifact_class=entry.required_artifact_class,
            path=str(path),
        )
    actual_size = len(payload)
    actual_sha256 = sha256(payload).hexdigest()
    if actual_size != entry.byte_size:
        return _fail(
            _entry_check_name(entry.required_artifact_class),
            "artifact byte_size mismatch",
            artifact_class=entry.required_artifact_class,
            path=str(path),
            metadata={"expected": entry.byte_size, "actual": actual_size},
        )
    if actual_sha256 != entry.sha256:
        return _fail(
            _entry_check_name(entry.required_artifact_class),
            "artifact sha256 mismatch",
            artifact_class=entry.required_artifact_class,
            path=str(path),
            metadata={"expected": entry.sha256, "actual": actual_sha256},
        )
    if known_class:
        shape_error = artifact_shape_error(entry.required_artifact_class, path)
        if shape_error is not None:
            return _fail(
                _entry_check_name(entry.required_artifact_class),
                "invalid artifact evidence: " + shape_error,
                artifact_class=entry.required_artifact_class,
                path=str(path),
            )
    return _pass(
        _entry_check_name(entry.required_artifact_class),
        "artifact integrity and class shape verified",
        artifact_class=entry.required_artifact_class,
        path=str(path),
        metadata={"sha256": actual_sha256, "byte_size": actual_size},
    )


def _cross_artifact_invariants(bundle: ReproductionBundle) -> list[ReproductionBundleCheck]:
    by_class = {entry.required_artifact_class: entry for entry in bundle.entries}
    split_entry = by_class.get("live_terminal_bench_split_manifest")
    evaluation_entry = by_class.get("live_two_repeat_evaluation_report")
    audit_entry = by_class.get("live_harbor_audit")
    protocol_entry = by_class.get("fixed_protocol_config")
    model_preflight_entry = by_class.get("model_backend_preflight_report")
    harbor_preflight_entry = by_class.get("live_harbor_preflight_report")
    trust_entry = by_class.get("container_image_trust_report")
    proposer_entry = by_class.get("proposer_llm_request_log")
    proposer_context_entry = by_class.get("proposer_context_manifest")
    proposal_validation_entry = by_class.get("proposal_validation_manifest")
    checks: list[ReproductionBundleCheck] = []
    protocol_check = _cross_artifact_protocol_binding(bundle, protocol_entry, evaluation_entry, audit_entry)
    if protocol_check is not None:
        checks.append(protocol_check)
    model_protocol_check = _cross_artifact_model_protocol_binding(bundle, protocol_entry, model_preflight_entry)
    if model_protocol_check is not None:
        checks.append(model_protocol_check)
    proposer_model_check = _cross_artifact_proposer_model_binding(
        bundle,
        proposer_entry,
        model_preflight_entry,
        protocol_entry,
    )
    if proposer_model_check is not None:
        checks.append(proposer_model_check)
    proposer_round_check = _cross_artifact_proposer_round_count(bundle, proposer_entry, protocol_entry)
    if proposer_round_check is not None:
        checks.append(proposer_round_check)
    proposer_context_check = _cross_artifact_proposer_context_binding(
        bundle,
        proposer_context_entry,
        proposer_entry,
        protocol_entry,
    )
    if proposer_context_check is not None:
        checks.append(proposer_context_check)
    proposer_previous_edits_check = _cross_artifact_proposer_previous_edits_binding(
        bundle,
        proposer_context_entry,
        proposer_entry,
    )
    if proposer_previous_edits_check is not None:
        checks.append(proposer_previous_edits_check)
    proposer_context_evidence_check = _cross_artifact_proposer_context_evidence_binding(
        bundle,
        proposer_context_entry,
        proposal_validation_entry,
        split_entry,
    )
    if proposer_context_evidence_check is not None:
        checks.append(proposer_context_evidence_check)
    proposal_validation_check = _cross_artifact_proposal_validation_binding(
        bundle,
        proposal_validation_entry,
        split_entry,
        proposer_entry,
        proposer_context_entry,
        protocol_entry,
        evaluation_entry,
    )
    if proposal_validation_check is not None:
        checks.append(proposal_validation_check)
    harbor_version_check = _cross_artifact_harbor_version_binding(bundle, split_entry, harbor_preflight_entry)
    if harbor_version_check is not None:
        checks.append(harbor_version_check)
    capture_run_id_check = _cross_artifact_capture_run_id_binding(bundle)
    if capture_run_id_check is not None:
        checks.append(capture_run_id_check)
    audit_image_check = _cross_artifact_audit_image_binding(bundle, audit_entry, trust_entry)
    if audit_image_check is not None:
        checks.append(audit_image_check)
    if split_entry is None or evaluation_entry is None:
        return checks
    try:
        split_path = resolve_bundle_entry_path(bundle.path, split_entry)
        evaluation_path = resolve_bundle_entry_path(bundle.path, evaluation_entry)
        split = _read_json_object(split_path, label="live Terminal-Bench split manifest")
        evaluation = _read_json_object(evaluation_path, label="live two-repeat evaluation report")
        held_in_ids = _string_list(split, "held_in_task_ids", label="live Terminal-Bench split manifest")
        held_out_ids = _string_list(split, "held_out_task_ids", label="live Terminal-Bench split manifest")
        manifest_ids = set(held_in_ids) | set(held_out_ids)
        rows = _object_list(evaluation, "per_task_attempts", label="live two-repeat evaluation report")
        evaluation_ids = [_required_row_str(row, "task_id", label="live two-repeat evaluation report") for row in rows]
        evaluation_id_set = set(evaluation_ids)
        task_count = _positive_int(evaluation, "task_count", label="live two-repeat evaluation report")
        attempt_count = _positive_int(evaluation, "attempt_count", label="live two-repeat evaluation report")
    except ReproductionBundleError as exc:
        return [_fail("cross_artifact_split_evaluation_coverage", str(exc))]

    missing = sorted(manifest_ids - evaluation_id_set)
    extra = sorted(evaluation_id_set - manifest_ids)
    failures: list[str] = []
    if task_count != 64:
        failures.append("evaluation task_count must be 64")
    if attempt_count != 128:
        failures.append("evaluation attempt_count must be 128")
    if missing or extra:
        failures.append("evaluation task ids must equal split manifest ids")
    metadata: dict[str, object] = {
        "manifest_total": len(manifest_ids),
        "eval_task_count": task_count,
        "eval_attempt_count": attempt_count,
        "missing": missing,
        "extra": extra,
    }
    if failures:
        checks.append(
            _fail("cross_artifact_split_evaluation_coverage", "; ".join(failures), metadata=metadata)
        )
    else:
        checks.append(
            _pass(
                "cross_artifact_split_evaluation_coverage",
                "two-repeat evaluation covers the fixed 64-task split",
                metadata=metadata,
            )
        )

    if audit_entry is None:
        return checks
    try:
        audit_path = resolve_bundle_entry_path(bundle.path, audit_entry)
        audit = _read_json_object(audit_path, label="live Harbor audit")
        audit_rows = _object_list(audit, "trial_artifacts", label="live Harbor audit")
        audit_ids = [_required_row_str(row, "task_id", label="live Harbor audit") for row in audit_rows]
        audit_id_set = set(audit_ids)
        bad_attempt_rows: list[str] = []
        for row in audit_rows:
            task_id = _required_row_str(row, "task_id", label="live Harbor audit")
            attempts = row.get("attempts")
            if not isinstance(attempts, list) or len(attempts) != 2:
                bad_attempt_rows.append(task_id)
    except ReproductionBundleError as exc:
        checks.append(_fail("cross_artifact_audit_split_coverage", str(exc)))
        return checks

    audit_missing = sorted(manifest_ids - audit_id_set)
    audit_extra = sorted(audit_id_set - manifest_ids)
    audit_eval_missing = sorted(evaluation_id_set - audit_id_set)
    audit_eval_extra = sorted(audit_id_set - evaluation_id_set)
    audit_failures: list[str] = []
    if audit_missing or audit_extra:
        audit_failures.append("live Harbor audit task ids must equal split manifest ids")
    if audit_eval_missing or audit_eval_extra:
        audit_failures.append("live Harbor audit task ids must equal two-repeat evaluation ids")
    if bad_attempt_rows:
        audit_failures.append("live Harbor audit tasks must record exactly two attempts")
    audit_metadata: dict[str, object] = {
        "manifest_total": len(manifest_ids),
        "audit_task_count": len(audit_id_set),
        "missing": audit_missing,
        "extra": audit_extra,
        "missing_from_audit_vs_evaluation": audit_eval_missing,
        "extra_in_audit_vs_evaluation": audit_eval_extra,
        "bad_attempt_rows": sorted(bad_attempt_rows),
    }
    if audit_failures:
        checks.append(
            _fail("cross_artifact_audit_split_coverage", "; ".join(audit_failures), metadata=audit_metadata)
        )
    else:
        checks.append(
            _pass(
                "cross_artifact_audit_split_coverage",
                "live Harbor audit covers the fixed 64-task split and two-repeat task set",
                metadata=audit_metadata,
            )
        )
    checks.append(_cross_artifact_evaluation_audit_outcomes(rows, audit_rows))
    return checks


def _cross_artifact_protocol_binding(
    bundle: ReproductionBundle,
    protocol_entry: ReproductionBundleEntry | None,
    evaluation_entry: ReproductionBundleEntry | None,
    audit_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if protocol_entry is None and evaluation_entry is None and audit_entry is None:
        return None
    if protocol_entry is None:
        return _fail("cross_artifact_protocol_binding", "fixed protocol config artifact is missing")
    failures: list[str] = []
    metadata: dict[str, object] = {}
    try:
        protocol_path = resolve_bundle_entry_path(bundle.path, protocol_entry)
        protocol_hash = sha256(protocol_path.read_bytes()).hexdigest()
        metadata["fixed_protocol_sha256"] = protocol_hash
        if evaluation_entry is None:
            failures.append("live two-repeat evaluation report artifact is missing")
        else:
            evaluation_path = resolve_bundle_entry_path(bundle.path, evaluation_entry)
            evaluation = _read_json_object(evaluation_path, label="live two-repeat evaluation report")
            evaluation_hash = _sha256_field(
                evaluation,
                "fixed_protocol_sha256",
                label="live two-repeat evaluation report",
            )
            metadata["evaluation_fixed_protocol_sha256"] = evaluation_hash
            if evaluation_hash != protocol_hash:
                failures.append(
                    "live two-repeat evaluation report fixed_protocol_sha256 must match fixed protocol config"
                )
        if audit_entry is None:
            failures.append("live Harbor audit artifact is missing")
        else:
            audit_path = resolve_bundle_entry_path(bundle.path, audit_entry)
            audit = _read_json_object(audit_path, label="live Harbor audit")
            audit_hash = _sha256_field(audit, "fixed_protocol_sha256", label="live Harbor audit")
            metadata["audit_fixed_protocol_sha256"] = audit_hash
            if audit_hash != protocol_hash:
                failures.append("live Harbor audit fixed_protocol_sha256 must match fixed protocol config")
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_protocol_binding", str(exc), metadata=metadata)
    if failures:
        return _fail("cross_artifact_protocol_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_protocol_binding",
        "evaluation and live Harbor audit evidence bind to the fixed protocol config",
        metadata=metadata,
    )


def _cross_artifact_model_protocol_binding(
    bundle: ReproductionBundle,
    protocol_entry: ReproductionBundleEntry | None,
    preflight_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if protocol_entry is None and preflight_entry is None:
        return None
    if protocol_entry is None:
        return _fail("cross_artifact_model_protocol_binding", "fixed protocol config artifact is missing")
    if preflight_entry is None:
        return _fail("cross_artifact_model_protocol_binding", "model backend preflight report artifact is missing")
    metadata: dict[str, object] = {}
    try:
        protocol_path = resolve_bundle_entry_path(bundle.path, protocol_entry)
        preflight_path = resolve_bundle_entry_path(bundle.path, preflight_entry)
        protocol = _read_json_object(protocol_path, label="fixed protocol config")
        preflight = _read_json_object(preflight_path, label="model backend preflight report")
        protocol_backends = _normal_model_backends(
            _string_list(protocol, "models", label="fixed protocol config")
        )
        preflight_backends = _normal_model_backends(
            _string_list(preflight, "backends", label="model backend preflight report")
        )
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_model_protocol_binding", str(exc), metadata=metadata)

    metadata = {
        "protocol_backends": sorted(protocol_backends),
        "preflight_backends": sorted(preflight_backends),
        "paper_backends": sorted(PAPER_MODEL_BACKENDS),
    }
    failures: list[str] = []
    if protocol_backends != PAPER_MODEL_BACKENDS:
        failures.append("fixed protocol config models must cover the paper model backends")
    if preflight_backends != PAPER_MODEL_BACKENDS:
        failures.append("model backend preflight report backends must cover the paper model backends")
    if protocol_backends != preflight_backends:
        failures.append("model backend preflight report backends must match fixed protocol config models")
    if failures:
        return _fail("cross_artifact_model_protocol_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_model_protocol_binding",
        "model backend preflight evidence matches the fixed protocol model set",
        metadata=metadata,
    )


def _cross_artifact_proposer_model_binding(
    bundle: ReproductionBundle,
    proposer_entry: ReproductionBundleEntry | None,
    preflight_entry: ReproductionBundleEntry | None,
    protocol_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if proposer_entry is None:
        return None
    if preflight_entry is None:
        return _fail("cross_artifact_proposer_model_binding", "model backend preflight report artifact is missing")
    if protocol_entry is None:
        return _fail("cross_artifact_proposer_model_binding", "fixed protocol config artifact is missing")
    try:
        proposer = read_artifact_payload(bundle, "proposer_llm_request_log")
        preflight = read_artifact_payload(bundle, "model_backend_preflight_report")
        protocol = read_artifact_payload(bundle, "fixed_protocol_config")
        proposer_backends = _proposer_llm_backends(proposer)
        preflight_backends = _normal_model_backends(
            _string_list(preflight, "backends", label="model backend preflight report")
        )
        protocol_backends = _normal_model_backends(
            _string_list(protocol, "models", label="fixed protocol config")
        )
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_proposer_model_binding", str(exc))

    metadata: dict[str, object] = {
        "proposer_backends": sorted(proposer_backends),
        "preflight_backends": sorted(preflight_backends),
        "protocol_backends": sorted(protocol_backends),
        "paper_backends": sorted(PAPER_MODEL_BACKENDS),
        "unexpected_proposer_backends": sorted(proposer_backends - PAPER_MODEL_BACKENDS),
        "missing_from_preflight": sorted(proposer_backends - preflight_backends),
        "missing_from_protocol": sorted(proposer_backends - protocol_backends),
        "missing_from_proposer": sorted(PAPER_MODEL_BACKENDS - proposer_backends),
    }
    failures: list[str] = []
    if proposer_backends != PAPER_MODEL_BACKENDS:
        failures.append("proposer LLM request log must cover the paper model backends")
    if preflight_backends != PAPER_MODEL_BACKENDS:
        failures.append("model backend preflight report backends must cover the paper model backends")
    if protocol_backends != PAPER_MODEL_BACKENDS:
        failures.append("fixed protocol config models must cover the paper model backends")
    if proposer_backends != preflight_backends:
        failures.append("proposer LLM request log backends must match model backend preflight report")
    if proposer_backends != protocol_backends:
        failures.append("proposer LLM request log backends must match fixed protocol config models")
    if failures:
        return _fail("cross_artifact_proposer_model_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_proposer_model_binding",
        "proposer LLM request log backends match model preflight and fixed protocol evidence",
        metadata=metadata,
    )


def _cross_artifact_proposer_round_count(
    bundle: ReproductionBundle,
    proposer_entry: ReproductionBundleEntry | None,
    protocol_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if proposer_entry is None:
        return None
    if protocol_entry is None:
        return _fail("cross_artifact_proposer_round_count", "fixed protocol config artifact is missing")
    try:
        proposer = read_artifact_payload(bundle, "proposer_llm_request_log")
        protocol = read_artifact_payload(bundle, "fixed_protocol_config")
        proposer_round_count = _positive_int(proposer, "round_count", label="proposer LLM request log")
        proposer_rounds = _object_list(proposer, "rounds", label="proposer LLM request log")
        protocol_rounds = _positive_int(protocol, "self_harness_rounds", label="fixed protocol config")
        proposal_width = _positive_int(protocol, "proposal_width", label="fixed protocol config")
        attempted_by_round: list[dict[str, int]] = []
        drifted_rounds: list[dict[str, int]] = []
        for row in proposer_rounds:
            round_index = _nonnegative_int(row, "round_index", label="proposer LLM request log")
            attempted_proposals = _nonnegative_int(
                row,
                "attempted_proposals",
                label="proposer LLM request log",
            )
            attempted_by_round.append(
                {
                    "round_index": round_index,
                    "attempted_proposals": attempted_proposals,
                }
            )
            if attempted_proposals != proposal_width:
                drifted_rounds.append(
                    {
                        "round_index": round_index,
                        "attempted_proposals": attempted_proposals,
                        "expected": proposal_width,
                    }
                )
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_proposer_round_count", str(exc))

    metadata: dict[str, object] = {
        "proposer_round_count": proposer_round_count,
        "proposer_round_rows": len(proposer_rounds),
        "protocol_self_harness_rounds": protocol_rounds,
        "protocol_proposal_width": proposal_width,
        "attempted_proposals_by_round": attempted_by_round,
        "attempted_proposal_drift": drifted_rounds,
    }
    failures: list[str] = []
    if proposer_round_count != protocol_rounds:
        failures.append("proposer LLM request log round_count must match fixed protocol self_harness_rounds")
    if len(proposer_rounds) != protocol_rounds:
        failures.append("proposer LLM request log rounds must match fixed protocol self_harness_rounds")
    if drifted_rounds:
        failures.append("proposer LLM request log attempted_proposals must match fixed protocol proposal_width")
    if failures:
        return _fail("cross_artifact_proposer_round_count", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_proposer_round_count",
        "proposer LLM round count and proposal width match fixed protocol",
        metadata=metadata,
    )


def _cross_artifact_proposer_context_binding(
    bundle: ReproductionBundle,
    context_entry: ReproductionBundleEntry | None,
    proposer_entry: ReproductionBundleEntry | None,
    protocol_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if context_entry is None and proposer_entry is None:
        return None
    if context_entry is None:
        return _fail(
            "cross_artifact_proposer_context_binding",
            "proposer context manifest artifact is missing",
        )
    if proposer_entry is None:
        return _fail(
            "cross_artifact_proposer_context_binding",
            "proposer LLM request log artifact is missing",
        )
    if protocol_entry is None:
        return _fail(
            "cross_artifact_proposer_context_binding",
            "fixed protocol config artifact is missing",
        )
    try:
        context = read_artifact_payload(bundle, "proposer_context_manifest")
        proposer = read_artifact_payload(bundle, "proposer_llm_request_log")
        protocol = read_artifact_payload(bundle, "fixed_protocol_config")
        context_round_count = _positive_int(context, "round_count", label="proposer context manifest")
        proposer_round_count = _positive_int(proposer, "round_count", label="proposer LLM request log")
        protocol_rounds = _positive_int(protocol, "self_harness_rounds", label="fixed protocol config")
        context_rounds = _object_list(context, "rounds", label="proposer context manifest")
        proposer_rounds = _object_list(proposer, "rounds", label="proposer LLM request log")
        context_by_round = {
            _nonnegative_int(row, "round_index", label="proposer context manifest"): row
            for row in context_rounds
        }
        proposer_by_round = {
            _nonnegative_int(row, "round_index", label="proposer LLM request log"): row
            for row in proposer_rounds
        }
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_proposer_context_binding", str(exc))

    context_round_indexes = sorted(context_by_round)
    proposer_round_indexes = sorted(proposer_by_round)
    metadata: dict[str, object] = {
        "context_round_count": context_round_count,
        "proposer_round_count": proposer_round_count,
        "protocol_self_harness_rounds": protocol_rounds,
        "context_round_indexes": context_round_indexes,
        "proposer_round_indexes": proposer_round_indexes,
        "missing_context_rounds": sorted(set(proposer_round_indexes) - set(context_round_indexes)),
        "extra_context_rounds": sorted(set(context_round_indexes) - set(proposer_round_indexes)),
        "empty_ingredient_rounds": [],
        "editable_surface_duplicate_violations": [],
    }
    failures: list[str] = []
    if context_round_count != proposer_round_count:
        failures.append("proposer context manifest round_count must match proposer LLM request log")
    if context_round_count != protocol_rounds:
        failures.append("proposer context manifest round_count must match fixed protocol self_harness_rounds")
    if proposer_round_indexes != context_round_indexes:
        failures.append("proposer context manifest round indexes must match proposer LLM request log")

    editable_surface_duplicate_violations: list[dict[str, object]] = []
    for round_index in sorted(context_by_round):
        seen_surface_sha256s: dict[str, int] = {}
        for surface_index, surface in enumerate(_context_editable_surface_rows(context_by_round[round_index])):
            sha256_value = _sha256_field(
                surface,
                "sha256",
                label="proposer context manifest editable surface",
            )
            name = _required_row_str(
                surface,
                "name",
                label="proposer context manifest editable surface",
            )
            first_seen_surface_index = seen_surface_sha256s.get(sha256_value)
            if first_seen_surface_index is not None:
                editable_surface_duplicate_violations.append(
                    {
                        "round_index": round_index,
                        "surface_index": surface_index,
                        "first_seen_surface_index": first_seen_surface_index,
                        "sha256": sha256_value,
                        "name": name,
                    }
                )
            else:
                seen_surface_sha256s[sha256_value] = surface_index

    empty_ingredient_rounds: list[dict[str, object]] = []
    for round_index in sorted(set(context_by_round) & set(proposer_by_round)):
        proposer_round = proposer_by_round[round_index]
        attempted_proposals = _nonnegative_int(
            proposer_round,
            "attempted_proposals",
            label="proposer LLM request log",
        )
        if attempted_proposals == 0:
            continue
        context_round = context_by_round[round_index]
        missing_blocks = _empty_context_ingredients(context_round, round_index=round_index)
        if missing_blocks:
            empty_ingredient_rounds.append(
                {
                    "round_index": round_index,
                    "attempted_proposals": attempted_proposals,
                    "empty_blocks": missing_blocks,
                }
            )
    metadata["empty_ingredient_rounds"] = empty_ingredient_rounds
    metadata["editable_surface_duplicate_violations"] = editable_surface_duplicate_violations
    if empty_ingredient_rounds:
        failures.append("attempted proposer rounds must include non-empty required context ingredients")
    if editable_surface_duplicate_violations:
        failures.append("editable surfaces must be pairwise distinct within each context round")
    if failures:
        return _fail("cross_artifact_proposer_context_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_proposer_context_binding",
        "proposer context ingredients align with proposer LLM rounds and fixed protocol",
        metadata=metadata,
    )


def _cross_artifact_harbor_version_binding(
    bundle: ReproductionBundle,
    split_entry: ReproductionBundleEntry | None,
    harbor_preflight_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if split_entry is None and harbor_preflight_entry is None:
        return None
    if split_entry is None:
        return _fail("cross_artifact_harbor_version_binding", "live Terminal-Bench split manifest artifact is missing")
    if harbor_preflight_entry is None:
        return _fail("cross_artifact_harbor_version_binding", "live Harbor preflight report artifact is missing")

    metadata: dict[str, object] = {}
    try:
        split_path = resolve_bundle_entry_path(bundle.path, split_entry)
        preflight_path = resolve_bundle_entry_path(bundle.path, harbor_preflight_entry)
        split = _read_json_object(split_path, label="live Terminal-Bench split manifest")
        preflight = _read_json_object(preflight_path, label="live Harbor preflight report")
        split_version = _required_str(split, "harbor_version", label="live Terminal-Bench split manifest")
        preflight_version = _required_str(preflight, "harbor_version", label="live Harbor preflight report")
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_harbor_version_binding", str(exc), metadata=metadata)

    metadata = {
        "split_harbor_version": split_version,
        "preflight_harbor_version": preflight_version,
    }
    if split_version != preflight_version:
        return _fail(
            "cross_artifact_harbor_version_binding",
            "live Terminal-Bench split manifest harbor_version must match live Harbor preflight report",
            metadata=metadata,
        )
    return _pass(
        "cross_artifact_harbor_version_binding",
        "live Terminal-Bench split manifest and Harbor preflight use the same Harbor version",
        metadata=metadata,
    )


def _cross_artifact_proposer_previous_edits_binding(
    bundle: ReproductionBundle,
    context_entry: ReproductionBundleEntry | None,
    proposer_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if context_entry is None or proposer_entry is None:
        return None
    try:
        context = read_artifact_payload(bundle, "proposer_context_manifest")
        proposer = read_artifact_payload(bundle, "proposer_llm_request_log")
        context_rounds = _object_list(context, "rounds", label="proposer context manifest")
        proposer_rounds = _object_list(proposer, "rounds", label="proposer LLM request log")
        context_by_round = {
            _nonnegative_int(row, "round_index", label="proposer context manifest"): row
            for row in context_rounds
        }
        proposer_round_indexes = {
            _nonnegative_int(row, "round_index", label="proposer LLM request log")
            for row in proposer_rounds
        }
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_proposer_previous_edits_binding", str(exc))

    metadata: dict[str, object] = {
        "context_round_indexes": sorted(context_by_round),
        "proposer_round_indexes": sorted(proposer_round_indexes),
        "rounds": [],
        "future_or_current_references": [],
        "missing_prior_rounds": [],
        "unknown_targeted_mechanisms": [],
        "unknown_edited_surfaces": [],
        "causal_status_violations": [],
        "bad_audit_decisions": [],
        "decision_reason_violations": [],
        "previous_edit_duplicate_violations": [],
    }
    future_or_current_references: list[dict[str, object]] = []
    missing_prior_rounds: list[dict[str, object]] = []
    unknown_targeted_mechanisms: list[dict[str, object]] = []
    unknown_edited_surfaces: list[dict[str, object]] = []
    causal_status_violations: list[dict[str, object]] = []
    bad_audit_decisions: list[dict[str, object]] = []
    decision_reason_violations: list[dict[str, object]] = []
    previous_edit_duplicate_violations: list[dict[str, object]] = []
    round_metadata: list[dict[str, object]] = []

    for round_index in sorted(context_by_round):
        row = context_by_round[round_index]
        edits = _context_previous_edits(row)
        edit_metadata: list[dict[str, object]] = []
        previous_edit_signatures: dict[tuple[int, str, str], int] = {}
        for edit_index, edit in enumerate(edits):
            proposal_round_index = _nonnegative_int(
                edit,
                "proposal_round_index",
                label="proposer context manifest previous attempted edit",
            )
            targeted_mechanism_sha256 = _sha256_field(
                edit,
                "targeted_mechanism_sha256",
                label="proposer context manifest previous attempted edit",
            )
            causal_status_sha256 = _optional_sha256_field(
                edit,
                "causal_status_sha256",
                label="proposer context manifest previous attempted edit",
            )
            edited_surface_sha256 = _sha256_field(
                edit,
                "edited_surface_sha256",
                label="proposer context manifest previous attempted edit",
            )
            audit_decision = _required_row_str(
                edit,
                "audit_decision",
                label="proposer context manifest previous attempted edit",
            )
            audit_decision_reason = _required_str(
                edit,
                "audit_decision_reason",
                label="proposer context manifest previous attempted edit",
                allow_empty=True,
            )
            edit_metadata.append(
                {
                    "edit_index": edit_index,
                    "proposal_round_index": proposal_round_index,
                    "targeted_mechanism_sha256": targeted_mechanism_sha256,
                    "causal_status_sha256": causal_status_sha256,
                    "edited_surface_sha256": edited_surface_sha256,
                    "audit_decision": audit_decision,
                }
            )
            edit_location: dict[str, object] = {
                "round_index": round_index,
                "edit_index": edit_index,
                "proposal_round_index": proposal_round_index,
            }
            signature = (proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)
            first_seen_edit_index = previous_edit_signatures.get(signature)
            if first_seen_edit_index is not None:
                previous_edit_duplicate_violations.append(
                    {
                        **edit_location,
                        "first_seen_edit_index": first_seen_edit_index,
                        "targeted_mechanism_sha256": targeted_mechanism_sha256,
                        "edited_surface_sha256": edited_surface_sha256,
                    }
                )
            else:
                previous_edit_signatures[signature] = edit_index
            if proposal_round_index >= round_index:
                future_or_current_references.append(edit_location)
            prior_row = context_by_round.get(proposal_round_index)
            if prior_row is None or proposal_round_index not in proposer_round_indexes:
                missing_prior_rounds.append(edit_location)
                continue
            prior_mechanisms = _context_failure_mechanism_sha256s(prior_row)
            if targeted_mechanism_sha256 not in prior_mechanisms:
                unknown_targeted_mechanisms.append(
                    {
                        **edit_location,
                        "targeted_mechanism_sha256": targeted_mechanism_sha256,
                        "prior_mechanism_sha256s": sorted(prior_mechanisms),
                    }
                )
            prior_causal_statuses = _context_failure_causal_status_sha256s_by_mechanism(prior_row)
            prior_mechanism_causal_statuses = prior_causal_statuses.get(targeted_mechanism_sha256, frozenset())
            causal_reasons: list[str] = []
            if causal_status_sha256 is not None:
                if not prior_mechanism_causal_statuses:
                    causal_reasons.append("missing_prior_causal_status")
                elif len(prior_mechanism_causal_statuses) > 1:
                    causal_reasons.append("mixed_prior_causal_status")
                elif causal_status_sha256 not in prior_mechanism_causal_statuses:
                    causal_reasons.append("declared_causal_status_mismatch")
            if causal_reasons:
                causal_status_violations.append(
                    {
                        **edit_location,
                        "targeted_mechanism_sha256": targeted_mechanism_sha256,
                        "causal_status_sha256": causal_status_sha256,
                        "prior_causal_status_sha256s": sorted(prior_mechanism_causal_statuses),
                        "reasons": causal_reasons,
                    }
                )
            prior_surface_hashes = _context_editable_surface_sha256s(prior_row)
            if edited_surface_sha256 not in prior_surface_hashes:
                unknown_edited_surfaces.append(
                    {
                        **edit_location,
                        "edited_surface_sha256": edited_surface_sha256,
                        "prior_surface_sha256s": sorted(prior_surface_hashes),
                    }
                )
            if audit_decision not in {"accepted", "rejected", "invalid"}:
                bad_audit_decisions.append({**edit_location, "audit_decision": audit_decision})
            if audit_decision != "accepted" and not audit_decision_reason:
                decision_reason_violations.append({**edit_location, "audit_decision": audit_decision})
        round_metadata.append(
            {
                "round_index": round_index,
                "edit_count": len(edits),
                "edits": edit_metadata,
            }
        )

    metadata["rounds"] = round_metadata
    metadata["future_or_current_references"] = future_or_current_references
    metadata["missing_prior_rounds"] = missing_prior_rounds
    metadata["unknown_targeted_mechanisms"] = unknown_targeted_mechanisms
    metadata["unknown_edited_surfaces"] = unknown_edited_surfaces
    metadata["causal_status_violations"] = causal_status_violations
    metadata["bad_audit_decisions"] = bad_audit_decisions
    metadata["decision_reason_violations"] = decision_reason_violations
    metadata["previous_edit_duplicate_violations"] = previous_edit_duplicate_violations

    failures: list[str] = []
    if future_or_current_references:
        failures.append("previous attempted edits must reference a prior proposer round")
    if missing_prior_rounds:
        failures.append("previous attempted edits must reference proposer and context rounds that exist")
    if unknown_targeted_mechanisms:
        failures.append("previous attempted edits targeted_mechanism_sha256 must exist in the prior round")
    if unknown_edited_surfaces:
        failures.append("previous attempted edits edited_surface_sha256 must exist in the prior round")
    if causal_status_violations:
        failures.append("previous attempted edits causal_status_sha256 must match the prior round failure pattern")
    if bad_audit_decisions:
        failures.append("previous attempted edits audit_decision must be accepted, rejected, or invalid")
    if decision_reason_violations:
        failures.append("rejected or invalid previous attempted edits must carry audit_decision_reason")
    if previous_edit_duplicate_violations:
        failures.append("previous attempted edits must be pairwise distinct within each round")
    if failures:
        return _fail(
            "cross_artifact_proposer_previous_edits_binding",
            "; ".join(failures),
            metadata=metadata,
        )
    return _pass(
        "cross_artifact_proposer_previous_edits_binding",
        "previous attempted edits bind to prior proposer mechanisms, causal statuses, and editable surfaces",
        metadata=metadata,
    )


def _cross_artifact_proposer_context_evidence_binding(
    bundle: ReproductionBundle,
    context_entry: ReproductionBundleEntry | None,
    validation_entry: ReproductionBundleEntry | None,
    split_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if context_entry is None:
        return None
    if validation_entry is None:
        return _fail(
            "cross_artifact_proposer_context_evidence_binding",
            "proposal validation manifest artifact is missing",
        )
    if split_entry is None:
        return _fail(
            "cross_artifact_proposer_context_evidence_binding",
            "live Terminal-Bench split manifest artifact is missing",
        )
    try:
        context = read_artifact_payload(bundle, "proposer_context_manifest")
        validation = read_artifact_payload(bundle, "proposal_validation_manifest")
        split = read_artifact_payload(bundle, "live_terminal_bench_split_manifest")
        held_in_ids = set(_string_list(split, "held_in_task_ids", label="live Terminal-Bench split manifest"))
        rounds = _object_list(context, "rounds", label="proposer context manifest")
        validation_by_round = _validation_rounds_by_index(
            _object_list(validation, "rounds", label="proposal validation manifest")
        )
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_proposer_context_evidence_binding", str(exc))

    metadata: dict[str, object] = {
        "held_in_task_count": len(held_in_ids),
        "baseline_rounds": [],
        "failure_pattern_rounds": [],
        "passing_summary_rounds": [],
        "opaque_mechanism_sha256_count": 0,
        "opaque_shared_symptoms_sha256_count": 0,
        "opaque_verifier_evidence_sha256_count": 0,
        "presentation_order_declared_count": 0,
        "actionability_hint_sha256_count": 0,
        "support_rank_rule": (
            "presentation_order must follow size descending when sizes differ; "
            "equal-size ties may be ordered by actionability; support_rank is not stored"
        ),
        "opaque_preserved_behavior_sha256_count": 0,
    }
    failures: list[str] = []

    missing_validation_rounds: list[int] = []
    missing_baseline_task_outcome_rounds: list[int] = []
    bad_baseline_task_ids: list[dict[str, object]] = []
    bad_failure_task_ids: list[dict[str, object]] = []
    bad_failure_sizes: list[dict[str, object]] = []
    bad_failure_task_overlaps: list[dict[str, object]] = []
    bad_failure_unions: list[dict[str, object]] = []
    bad_failure_categories: list[dict[str, object]] = []
    bad_presentation_orders: list[dict[str, object]] = []
    bad_passing_task_ids: list[dict[str, object]] = []
    bad_passing_unions: list[dict[str, object]] = []
    bad_passing_hashes: list[dict[str, object]] = []
    mechanism_hash_count = 0
    preserved_behavior_hash_count = 0
    baseline_round_metadata: list[dict[str, object]] = []
    failure_round_metadata: list[dict[str, object]] = []
    passing_round_metadata: list[dict[str, object]] = []
    shared_symptoms_hash_count = 0
    verifier_evidence_hash_count = 0
    presentation_order_count = 0
    actionability_hint_hash_count = 0

    for round_row in rounds:
        round_index = _nonnegative_int(round_row, "round_index", label="proposer context manifest")
        validation_round = validation_by_round.get(round_index)
        baseline_failing: frozenset[str] = frozenset()
        baseline_passing: frozenset[str] = frozenset()
        baseline_failure_categories: dict[str, frozenset[str]] = {}
        if validation_round is None:
            missing_validation_rounds.append(round_index)
        else:
            baseline = _object_field(
                validation_round,
                "baseline_split_outcomes",
                label=f"proposal validation manifest round {round_index}",
            )
            task_outcomes = _optional_task_outcomes(
                baseline,
                label=f"proposal validation manifest round {round_index} baseline_split_outcomes",
            )
            if not task_outcomes:
                missing_baseline_task_outcome_rounds.append(round_index)
            else:
                baseline_failing, baseline_passing, missing_task_ids, extra_task_ids = (
                    _held_in_pass_sets_from_task_outcomes(
                        task_outcomes,
                        held_in_ids,
                        label=f"proposal validation manifest round {round_index} baseline_split_outcomes",
                    )
                )
                if missing_task_ids or extra_task_ids:
                    bad_baseline_task_ids.append(
                        {
                            "round_index": round_index,
                            "missing_task_ids": missing_task_ids,
                            "extra_task_ids": extra_task_ids,
                        }
                    )
                baseline_failure_categories = _held_in_failure_categories_from_task_outcomes(
                    task_outcomes,
                    label=f"proposal validation manifest round {round_index} baseline_split_outcomes",
                )
        baseline_round_metadata.append(
            {
                "round_index": round_index,
                "baseline_held_in_failing_task_ids": sorted(baseline_failing),
                "baseline_held_in_passing_task_ids": sorted(baseline_passing),
                "baseline_held_in_failure_categories_by_task": {
                    task_id: sorted(baseline_failure_categories[task_id])
                    for task_id in sorted(baseline_failure_categories)
                },
            }
        )
        failure_patterns = _context_failure_patterns(round_row)
        failure_union: set[str] = set()
        failure_task_clusters: dict[str, list[str]] = {}
        failure_rows: list[dict[str, object]] = []
        round_presentation_orders: list[int] = []
        presentation_order_declared = False
        for pattern in failure_patterns:
            cluster_id = _required_row_str(pattern, "cluster_id", label="proposer context manifest failure pattern")
            task_ids = set(_task_id_list(pattern, "task_ids", label=f"failure pattern {cluster_id}"))
            for task_id in task_ids:
                failure_task_clusters.setdefault(task_id, []).append(cluster_id)
            mechanism_hash_count += 1 if _required_row_str(pattern, "mechanism_sha256", label="failure pattern") else 0
            shared_symptoms_sha256 = _optional_sha256_field(
                pattern,
                "shared_symptoms_sha256",
                label="proposer context manifest failure pattern",
            )
            verifier_evidence_sha256 = _optional_sha256_field(
                pattern,
                "verifier_evidence_sha256",
                label="proposer context manifest failure pattern",
            )
            shared_symptoms_hash_count += 1 if shared_symptoms_sha256 is not None else 0
            verifier_evidence_hash_count += 1 if verifier_evidence_sha256 is not None else 0
            presentation_order = pattern.get("presentation_order")
            if presentation_order is not None:
                presentation_order_declared = True
                if (
                    not isinstance(presentation_order, int)
                    or isinstance(presentation_order, bool)
                    or presentation_order < 0
                ):
                    raise ReproductionBundleError(
                        "proposer context manifest failure pattern presentation_order must be a non-negative integer "
                        "or null"
                    )
                presentation_order_count += 1
                round_presentation_orders.append(presentation_order)
            actionability_hint_sha256 = _optional_sha256_field(
                pattern,
                "actionability_hint_sha256",
                label="proposer context manifest failure pattern",
            )
            actionability_hint_hash_count += 1 if actionability_hint_sha256 is not None else 0
            failure_union.update(task_ids)
            unexpected = sorted(task_ids - baseline_failing)
            if unexpected:
                bad_failure_task_ids.append(
                    {"round_index": round_index, "cluster_id": cluster_id, "unexpected_task_ids": unexpected}
                )
            size = _nonnegative_int(pattern, "size", label=f"failure pattern {cluster_id}")
            if size != len(task_ids):
                bad_failure_sizes.append(
                    {
                        "round_index": round_index,
                        "cluster_id": cluster_id,
                        "size": size,
                        "task_id_count": len(task_ids),
                    }
                )
            declared_category = pattern.get("failure_category")
            if declared_category is not None and not isinstance(declared_category, str):
                raise ReproductionBundleError("proposer context manifest failure_category must be a string or null")
            observed_categories = sorted(
                {
                    category
                    for task_id in task_ids
                    for category in baseline_failure_categories.get(task_id, frozenset())
                }
            )
            category_reasons: list[str] = []
            if len(observed_categories) > 1:
                category_reasons.append("mixed_baseline_failure_categories")
            if (
                isinstance(declared_category, str)
                and observed_categories
                and observed_categories != [declared_category]
            ):
                category_reasons.append("declared_failure_category_mismatch")
            if category_reasons:
                bad_failure_categories.append(
                    {
                        "round_index": round_index,
                        "cluster_id": cluster_id,
                        "reasons": category_reasons,
                        "failure_category": declared_category,
                        "baseline_failure_categories": observed_categories,
                        "task_ids": sorted(task_ids),
                    }
                )
            failure_rows.append(
                {
                    "cluster_id": cluster_id,
                    "task_ids": sorted(task_ids),
                    "size": size,
                    "failure_category": declared_category,
                    "baseline_failure_categories": observed_categories,
                    "shared_symptoms_sha256": shared_symptoms_sha256,
                    "verifier_evidence_sha256": verifier_evidence_sha256,
                    "presentation_order": presentation_order,
                    "actionability_hint_sha256": actionability_hint_sha256,
                }
            )
        if presentation_order_declared:
            expected_orders = list(range(len(failure_patterns)))
            if (
                len(round_presentation_orders) != len(failure_patterns)
                or sorted(round_presentation_orders) != expected_orders
            ):
                bad_presentation_orders.append(
                    {
                        "round_index": round_index,
                        "expected": expected_orders,
                        "actual": sorted(round_presentation_orders),
                    }
                )
        task_overlap_rows = [
            {"task_id": task_id, "clusters": sorted(clusters)}
            for task_id, clusters in sorted(failure_task_clusters.items())
            if len(clusters) > 1
        ]
        if task_overlap_rows:
            bad_failure_task_overlaps.append(
                {
                    "round_index": round_index,
                    "overlapping_task_ids": task_overlap_rows,
                }
            )
        if failure_union != baseline_failing:
            bad_failure_unions.append(
                {
                    "round_index": round_index,
                    "missing_task_ids": sorted(baseline_failing - failure_union),
                    "extra_task_ids": sorted(failure_union - baseline_failing),
                }
            )
        failure_round_metadata.append(
            {
                "round_index": round_index,
                "pattern_count": len(failure_patterns),
                "task_ids": sorted(failure_union),
                "patterns": failure_rows,
            }
        )

        passing_summaries = _context_passing_summaries(round_row)
        passing_union: set[str] = set()
        summary_rows: list[dict[str, object]] = []
        for summary_index, summary in enumerate(passing_summaries):
            task_ids = set(
                _task_id_list(
                    summary,
                    "task_ids",
                    label=f"passing summary round {round_index} summary {summary_index}",
                )
            )
            preserved_behavior_hash_count += 1 if _required_row_str(
                summary,
                "preserved_behavior_sha256",
                label="passing behavior summary",
            ) else 0
            passing_union.update(task_ids)
            unexpected = sorted(task_ids - baseline_passing)
            if unexpected:
                bad_passing_task_ids.append(
                    {
                        "round_index": round_index,
                        "summary_index": summary_index,
                        "unexpected_task_ids": unexpected,
                    }
                )
            expected_hash = _task_id_set_sha256(task_ids)
            actual_hash = _required_row_str(
                summary,
                "task_id_set_sha256",
                label="passing behavior summary",
            )
            if actual_hash != expected_hash:
                bad_passing_hashes.append(
                    {
                        "round_index": round_index,
                        "summary_index": summary_index,
                        "expected": expected_hash,
                        "actual": actual_hash,
                    }
                )
            summary_rows.append(
                {
                    "summary_index": summary_index,
                    "task_ids": sorted(task_ids),
                    "task_id_set_sha256": actual_hash,
                }
            )
        if passing_union != baseline_passing:
            bad_passing_unions.append(
                {
                    "round_index": round_index,
                    "missing_task_ids": sorted(baseline_passing - passing_union),
                    "extra_task_ids": sorted(passing_union - baseline_passing),
                }
            )
        passing_round_metadata.append(
            {
                "round_index": round_index,
                "summary_count": len(passing_summaries),
                "task_ids": sorted(passing_union),
                "summaries": summary_rows,
            }
        )

    metadata["baseline_rounds"] = baseline_round_metadata
    metadata["missing_validation_rounds"] = missing_validation_rounds
    metadata["missing_baseline_task_outcome_rounds"] = missing_baseline_task_outcome_rounds
    metadata["baseline_task_id_violations"] = bad_baseline_task_ids
    metadata["failure_pattern_rounds"] = failure_round_metadata
    metadata["passing_summary_rounds"] = passing_round_metadata
    metadata["failure_pattern_task_id_violations"] = bad_failure_task_ids
    metadata["failure_pattern_size_violations"] = bad_failure_sizes
    metadata["failure_pattern_task_overlap_violations"] = bad_failure_task_overlaps
    metadata["failure_pattern_union_violations"] = bad_failure_unions
    metadata["failure_pattern_category_violations"] = bad_failure_categories
    metadata["presentation_order_violations"] = bad_presentation_orders
    metadata["passing_summary_task_id_violations"] = bad_passing_task_ids
    metadata["passing_summary_union_violations"] = bad_passing_unions
    metadata["passing_summary_hash_violations"] = bad_passing_hashes
    metadata["opaque_mechanism_sha256_count"] = mechanism_hash_count
    metadata["opaque_shared_symptoms_sha256_count"] = shared_symptoms_hash_count
    metadata["opaque_verifier_evidence_sha256_count"] = verifier_evidence_hash_count
    metadata["presentation_order_declared_count"] = presentation_order_count
    metadata["actionability_hint_sha256_count"] = actionability_hint_hash_count
    metadata["opaque_preserved_behavior_sha256_count"] = preserved_behavior_hash_count

    if missing_validation_rounds:
        failures.append("proposer context rounds must exist in proposal validation manifest")
    if missing_baseline_task_outcome_rounds:
        failures.append("proposal validation baselines must disclose task outcomes when proposer context is bundled")
    if bad_baseline_task_ids:
        failures.append("proposal validation baseline task outcomes must cover the held-in split")
    if bad_failure_task_ids:
        failures.append("held-in failure pattern task_ids must reference same-round baseline held-in failing tasks")
    if bad_failure_sizes:
        failures.append("held-in failure pattern size must equal task_ids length")
    if bad_failure_task_overlaps:
        failures.append("held-in failure pattern task_ids must be pairwise disjoint within each round")
    if bad_failure_unions:
        failures.append("held-in failure pattern task_ids must cover all same-round baseline held-in failing tasks")
    if bad_failure_categories:
        failures.append("held-in failure pattern failure_category must match same-round baseline failures")
    if bad_presentation_orders:
        failures.append("held-in failure pattern presentation_order must be a contiguous permutation")
    if bad_passing_task_ids:
        failures.append("passing behavior summary task_ids must reference same-round baseline held-in passing tasks")
    if bad_passing_unions:
        failures.append("passing behavior summary task_ids must cover all same-round baseline held-in passing tasks")
    if bad_passing_hashes:
        failures.append("passing behavior summary task_id_set_sha256 must match task_ids")
    if failures:
        return _fail(
            "cross_artifact_proposer_context_evidence_binding",
            "; ".join(failures),
            metadata=metadata,
        )
    return _pass(
        "cross_artifact_proposer_context_evidence_binding",
        "proposer context failure and passing task ids derive from same-round baseline validation evidence",
        metadata=metadata,
    )


def _cross_artifact_proposal_validation_binding(
    bundle: ReproductionBundle,
    validation_entry: ReproductionBundleEntry | None,
    split_entry: ReproductionBundleEntry | None,
    proposer_entry: ReproductionBundleEntry | None,
    context_entry: ReproductionBundleEntry | None,
    protocol_entry: ReproductionBundleEntry | None,
    evaluation_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if validation_entry is None:
        return None
    if protocol_entry is None:
        return _fail("cross_artifact_proposal_validation_binding", "fixed protocol config artifact is missing")
    if split_entry is None:
        return _fail(
            "cross_artifact_proposal_validation_binding",
            "live Terminal-Bench split manifest artifact is missing",
        )
    try:
        validation = read_artifact_payload(bundle, "proposal_validation_manifest")
        split = read_artifact_payload(bundle, "live_terminal_bench_split_manifest")
        protocol = read_artifact_payload(bundle, "fixed_protocol_config")
        protocol_path = resolve_bundle_entry_path(bundle.path, protocol_entry)
        protocol_hash = sha256(protocol_path.read_bytes()).hexdigest()
        validation_hash = _sha256_field(
            validation,
            "fixed_protocol_sha256",
            label="proposal validation manifest",
        )
        validation_round_count = _positive_int(
            validation,
            "round_count",
            label="proposal validation manifest",
        )
        validation_rounds = _object_list(validation, "rounds", label="proposal validation manifest")
        split_held_in_count = _positive_int(
            split,
            "held_in_count",
            label="live Terminal-Bench split manifest",
        )
        split_held_out_count = _positive_int(
            split,
            "held_out_count",
            label="live Terminal-Bench split manifest",
        )
        protocol_rounds = _positive_int(protocol, "self_harness_rounds", label="fixed protocol config")
        proposal_width = _positive_int(protocol, "proposal_width", label="fixed protocol config")
        validation_by_round = _validation_rounds_by_index(validation_rounds)
        proposer_by_round: dict[int, dict[str, object]] = {}
        if proposer_entry is not None:
            proposer = read_artifact_payload(bundle, "proposer_llm_request_log")
            proposer_by_round = {
                _nonnegative_int(row, "round_index", label="proposer LLM request log"): row
                for row in _object_list(proposer, "rounds", label="proposer LLM request log")
            }
        context_by_round: dict[int, dict[str, object]] = {}
        if context_entry is not None:
            context = read_artifact_payload(bundle, "proposer_context_manifest")
            context_by_round = {
                _nonnegative_int(row, "round_index", label="proposer context manifest"): row
                for row in _object_list(context, "rounds", label="proposer context manifest")
            }
        attempts_per_task: int | None = None
        if evaluation_entry is not None:
            evaluation = read_artifact_payload(bundle, "live_two_repeat_evaluation_report")
            attempts_per_task = _positive_int(
                evaluation,
                "attempts_per_task",
                label="live two-repeat evaluation report",
            )
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_proposal_validation_binding", str(exc))

    failures: list[str] = []
    candidate_count_drift: list[dict[str, object]] = []
    committed_count_drift: list[dict[str, object]] = []
    proposer_round_missing: list[int] = []
    repeat_drift: list[dict[str, object]] = []
    evaluation_repeats_mismatch_violations: list[dict[str, object]] = []
    baseline_total_violations: list[dict[str, object]] = []
    candidate_total_violations: list[dict[str, object]] = []
    acceptance_rule_violations: list[dict[str, object]] = []
    validation_failure_category_violations: list[dict[str, object]] = []
    baseline_task_outcome_violations: list[dict[str, object]] = []
    proposer_round_traffic_violations: list[dict[str, object]] = []
    candidate_mechanism_violations: list[dict[str, object]] = []
    candidate_surface_violations: list[dict[str, object]] = []
    candidate_surface_name_violations: list[dict[str, object]] = []
    candidate_distinctness_violations: list[dict[str, object]] = []
    merge_surface_conflict_violations: list[dict[str, object]] = []
    previous_edit_violations: list[dict[str, object]] = []
    lineage_continuity_violations: list[dict[str, object]] = []
    lineage_continuity_skipped_rounds: list[dict[str, object]] = []
    merged_split_outcomes_by_round: dict[int, dict[str, object] | None] = {}
    merged_split_outcome_lineage_closed_rounds: list[dict[str, object]] = []
    harness_hashes_by_round: dict[int, tuple[str, str] | None] = {}
    merged_harness_hashes_by_round: dict[int, str | None] = {}
    harness_continuity_violations: list[dict[str, object]] = []
    harness_continuity_missing_rounds: list[dict[str, object]] = []
    harness_continuity_skipped_rounds: list[dict[str, object]] = []
    harness_merged_hash_violations: list[dict[str, object]] = []
    round_metadata: list[dict[str, object]] = []

    if validation_hash != protocol_hash:
        failures.append("proposal validation manifest fixed_protocol_sha256 must match fixed protocol config")
    if validation_round_count != protocol_rounds:
        failures.append("proposal validation manifest round_count must match fixed protocol self_harness_rounds")
    if len(validation_rounds) != protocol_rounds:
        failures.append("proposal validation manifest rounds must match fixed protocol self_harness_rounds")

    for round_index in sorted(validation_by_round):
        round_row = validation_by_round[round_index]
        candidates = _object_list(round_row, "candidates", label="proposal validation manifest")
        committed = _string_list(round_row, "committed_proposal_ids", label="proposal validation manifest")
        candidate_ids = [
            _required_row_str(candidate, "proposal_id", label="proposal validation manifest candidate")
            for candidate in candidates
        ]
        row_metadata: dict[str, object] = {
            "round_index": round_index,
            "candidate_count": len(candidates),
            "committed_count": len(committed),
            "candidate_ids": candidate_ids,
            "committed_proposal_ids": list(committed),
        }
        harness_before_hash = _optional_sha256_field(
            round_row,
            "harness_before_sha256",
            label="proposal validation manifest round",
        )
        harness_after_hash = _optional_sha256_field(
            round_row,
            "harness_after_sha256",
            label="proposal validation manifest round",
        )
        harness_after_merged_hash = _optional_sha256_field(
            round_row,
            "harness_after_merged_sha256",
            label="proposal validation manifest round",
        )
        merged_harness_hashes_by_round[round_index] = harness_after_merged_hash
        if (harness_before_hash is None) != (harness_after_hash is None):
            harness_hashes_by_round[round_index] = None
            harness_continuity_missing_rounds.append(
                {
                    "round_index": round_index,
                    "missing_fields": [
                        field
                        for field, value in (
                            ("harness_before_sha256", harness_before_hash),
                            ("harness_after_sha256", harness_after_hash),
                        )
                        if value is None
                    ],
                }
            )
        elif harness_before_hash is None:
            harness_hashes_by_round[round_index] = None
            row_metadata["harness_hashes_present"] = False
        else:
            assert harness_after_hash is not None
            harness_hashes_by_round[round_index] = (harness_before_hash, harness_after_hash)
            row_metadata["harness_hashes_present"] = True
            row_metadata["harness_before_sha256"] = harness_before_hash
            row_metadata["harness_after_sha256"] = harness_after_hash
        if harness_after_merged_hash is not None:
            row_metadata["harness_after_merged_sha256"] = harness_after_merged_hash
            if harness_before_hash is None or harness_after_hash is None:
                harness_merged_hash_violations.append(
                    {
                        "round_index": round_index,
                        "reason": "merged hash declared without paired harness hashes",
                    }
                )
            elif len(committed) < 2:
                harness_merged_hash_violations.append(
                    {
                        "round_index": round_index,
                        "reason": "merged hash declared for non-multi-commit round",
                        "committed_proposal_ids": list(committed),
                    }
                )
            elif harness_after_merged_hash != harness_after_hash:
                harness_merged_hash_violations.append(
                    {
                        "round_index": round_index,
                        "reason": "merged hash must match round harness_after_sha256",
                        "harness_after_sha256": harness_after_hash,
                        "harness_after_merged_sha256": harness_after_merged_hash,
                    }
                )
        elif len(committed) >= 2 and harness_before_hash is not None and harness_after_hash is not None:
            harness_merged_hash_violations.append(
                {
                    "round_index": round_index,
                    "reason": "multi-commit round with harness hashes must declare merged hash",
                    "committed_proposal_ids": list(committed),
                }
            )
        context_round = context_by_round.get(round_index)
        allowed_mechanisms: frozenset[str] = frozenset()
        allowed_surfaces: frozenset[str] = frozenset()
        allowed_surface_names: frozenset[str] = frozenset()
        if context_round is not None:
            allowed_mechanisms = _context_failure_mechanism_sha256s(context_round)
            allowed_surfaces = _context_editable_surface_sha256s(context_round)
            allowed_surface_names = _context_editable_surface_names(context_round)
        row_metadata["context_mechanism_sha256s"] = sorted(allowed_mechanisms)
        row_metadata["context_editable_surface_sha256s"] = sorted(allowed_surfaces)
        row_metadata["context_editable_surface_names"] = sorted(allowed_surface_names)
        validation_request_hash = round_row.get("proposer_round_request_sha256")
        validation_response_hash = round_row.get("proposer_round_response_sha256")
        validation_declares_traffic = validation_request_hash is not None or validation_response_hash is not None
        row_metadata["proposer_round_traffic_binding_declared"] = validation_declares_traffic
        if validation_declares_traffic:
            if not isinstance(validation_request_hash, str) or not isinstance(validation_response_hash, str):
                proposer_round_traffic_violations.append(
                    {
                        "round_index": round_index,
                        "reason": "validation traffic hashes must be present together",
                    }
                )
            else:
                row_metadata["proposer_round_request_sha256"] = validation_request_hash
                row_metadata["proposer_round_response_sha256"] = validation_response_hash
                proposer_round = proposer_by_round.get(round_index)
                if proposer_round is None:
                    proposer_round_traffic_violations.append(
                        {
                            "round_index": round_index,
                            "reason": "missing proposer LLM request log round",
                            "validation_request_sha256": validation_request_hash,
                            "validation_response_sha256": validation_response_hash,
                        }
                    )
                else:
                    proposer_request_hash = _sha256_field(
                        proposer_round,
                        "request_sha256",
                        label="proposer LLM request log",
                    )
                    proposer_response_hash = _sha256_field(
                        proposer_round,
                        "response_sha256",
                        label="proposer LLM request log",
                    )
                    row_metadata["proposer_log_request_sha256"] = proposer_request_hash
                    row_metadata["proposer_log_response_sha256"] = proposer_response_hash
                    traffic_reasons: list[str] = []
                    if validation_request_hash != proposer_request_hash:
                        traffic_reasons.append("request_sha256_mismatch")
                    if validation_response_hash != proposer_response_hash:
                        traffic_reasons.append("response_sha256_mismatch")
                    if traffic_reasons:
                        proposer_round_traffic_violations.append(
                            {
                                "round_index": round_index,
                                "reasons": traffic_reasons,
                                "validation_request_sha256": validation_request_hash,
                                "proposer_request_sha256": proposer_request_hash,
                                "validation_response_sha256": validation_response_hash,
                                "proposer_response_sha256": proposer_response_hash,
                            }
                        )
        baseline_outcomes = _object_field(
            round_row,
            "baseline_split_outcomes",
            label="proposal validation manifest",
        )
        baseline_held_in_total = _nonnegative_int(
            baseline_outcomes,
            "held_in_total",
            label="proposal validation manifest baseline_split_outcomes",
        )
        baseline_held_out_total = _nonnegative_int(
            baseline_outcomes,
            "held_out_total",
            label="proposal validation manifest baseline_split_outcomes",
        )
        baseline_held_in_passed = _nonnegative_int(
            baseline_outcomes,
            "held_in_passed",
            label="proposal validation manifest baseline_split_outcomes",
        )
        baseline_held_out_passed = _nonnegative_int(
            baseline_outcomes,
            "held_out_passed",
            label="proposal validation manifest baseline_split_outcomes",
        )
        baseline_evaluation_repeats = _positive_int(
            baseline_outcomes,
            "evaluation_repeats",
            label="proposal validation manifest baseline_split_outcomes",
        )
        row_metadata["baseline_held_in_passed"] = baseline_held_in_passed
        row_metadata["baseline_held_out_passed"] = baseline_held_out_passed
        row_metadata["baseline_held_in_total"] = baseline_held_in_total
        row_metadata["baseline_held_out_total"] = baseline_held_out_total
        row_metadata["baseline_evaluation_repeats"] = baseline_evaluation_repeats
        baseline_task_outcomes = _optional_task_outcomes(
            baseline_outcomes,
            label="proposal validation manifest baseline_split_outcomes",
        )
        merged_split_outcomes: dict[str, object] | None = None
        if round_row.get("merged_split_outcomes") is not None:
            merged_split_outcomes = _object_field(
                round_row,
                "merged_split_outcomes",
                label="proposal validation manifest",
            )
            row_metadata["merged_split_outcomes_present"] = True
            row_metadata["merged_split_outcomes"] = _split_outcome_lineage_projection(
                merged_split_outcomes,
                label="proposal validation manifest merged_split_outcomes",
            )
        else:
            row_metadata["merged_split_outcomes_present"] = False
        merged_split_outcomes_by_round[round_index] = merged_split_outcomes
        if baseline_task_outcomes:
            baseline_failing_held_in = _failing_held_in_task_ids_from_task_outcomes(baseline_task_outcomes)
            row_metadata["baseline_task_outcomes_present"] = True
            row_metadata["baseline_task_outcomes_held_in_failing_task_ids"] = sorted(baseline_failing_held_in)
            if context_round is not None:
                for pattern in _context_failure_patterns(context_round):
                    cluster_id = _required_row_str(
                        pattern,
                        "cluster_id",
                        label="proposer context manifest failure pattern",
                    )
                    task_ids = set(_task_id_list(pattern, "task_ids", label=f"failure pattern {cluster_id}"))
                    missing = sorted(task_ids - baseline_failing_held_in)
                    if missing:
                        baseline_task_outcome_violations.append(
                            {
                                "round_index": round_index,
                                "cluster_id": cluster_id,
                                "missing_baseline_failing_task_ids": missing,
                                "baseline_failing_held_in_task_ids": sorted(baseline_failing_held_in),
                            }
                        )
        else:
            row_metadata["baseline_task_outcomes_present"] = False
        if baseline_held_in_total != split_held_in_count or baseline_held_out_total != split_held_out_count:
            baseline_total_violations.append(
                {
                    "round_index": round_index,
                    "held_in_total": baseline_held_in_total,
                    "held_out_total": baseline_held_out_total,
                    "expected_held_in_total": split_held_in_count,
                    "expected_held_out_total": split_held_out_count,
                }
            )
        if len(candidates) != proposal_width:
            candidate_count_drift.append(
                {
                    "round_index": round_index,
                    "candidate_count": len(candidates),
                    "expected": proposal_width,
                }
            )
        proposer_round = proposer_by_round.get(round_index)
        if proposer_entry is not None and proposer_round is None:
            proposer_round_missing.append(round_index)
        elif proposer_round is not None:
            attempted = _nonnegative_int(
                proposer_round,
                "attempted_proposals",
                label="proposer LLM request log",
            )
            committed_count = _nonnegative_int(
                proposer_round,
                "committed_proposals",
                label="proposer LLM request log",
            )
            row_metadata["proposer_attempted_proposals"] = attempted
            row_metadata["proposer_committed_proposals"] = committed_count
            if len(candidates) != attempted:
                candidate_count_drift.append(
                    {
                        "round_index": round_index,
                        "candidate_count": len(candidates),
                        "expected": attempted,
                        "source": "proposer_llm_request_log",
                    }
                )
            if len(committed) != committed_count:
                committed_count_drift.append(
                    {
                        "round_index": round_index,
                        "committed_count": len(committed),
                        "expected": committed_count,
                    }
                )
        candidate_signatures: list[tuple[str, str]] = []
        candidate_signature_rows: list[dict[str, object]] = []
        accepted_merged_surface_candidates: dict[str, list[str]] = {}
        for candidate in candidates:
            proposal_id = _required_row_str(candidate, "proposal_id", label="proposal validation manifest candidate")
            split_outcomes = _object_field(
                candidate,
                "split_outcomes",
                label="proposal validation manifest candidate",
            )
            targeted_mechanism_sha256 = _sha256_field(
                candidate,
                "targeted_mechanism_sha256",
                label="proposal validation manifest candidate",
            )
            edited_surface_sha256 = _sha256_field(
                candidate,
                "edited_surface_sha256",
                label="proposal validation manifest candidate",
            )
            candidate_signatures.append((targeted_mechanism_sha256, edited_surface_sha256))
            candidate_signature_rows.append(
                {
                    "proposal_id": proposal_id,
                    "targeted_mechanism_sha256": targeted_mechanism_sha256,
                    "edited_surface_sha256": edited_surface_sha256,
                }
            )
            candidate_held_in_passed = _nonnegative_int(
                split_outcomes,
                "held_in_passed",
                label="proposal validation manifest split_outcomes",
            )
            candidate_held_out_passed = _nonnegative_int(
                split_outcomes,
                "held_out_passed",
                label="proposal validation manifest split_outcomes",
            )
            candidate_held_in_total = _nonnegative_int(
                split_outcomes,
                "held_in_total",
                label="proposal validation manifest split_outcomes",
            )
            candidate_held_out_total = _nonnegative_int(
                split_outcomes,
                "held_out_total",
                label="proposal validation manifest split_outcomes",
            )
            candidate_evaluation_repeats = _positive_int(
                split_outcomes,
                "evaluation_repeats",
                label="proposal validation manifest split_outcomes",
            )
            if candidate_evaluation_repeats != baseline_evaluation_repeats:
                evaluation_repeats_mismatch_violations.append(
                    {
                        "round_index": round_index,
                        "proposal_id": proposal_id,
                        "baseline_evaluation_repeats": baseline_evaluation_repeats,
                        "candidate_evaluation_repeats": candidate_evaluation_repeats,
                    }
                )
            if (
                candidate_held_in_total != split_held_in_count
                or candidate_held_out_total != split_held_out_count
            ):
                candidate_total_violations.append(
                    {
                        "round_index": round_index,
                        "proposal_id": proposal_id,
                        "held_in_total": candidate_held_in_total,
                        "held_out_total": candidate_held_out_total,
                        "expected_held_in_total": split_held_in_count,
                        "expected_held_out_total": split_held_out_count,
                    }
                )
            audit_decision = _required_row_str(
                candidate,
                "audit_decision",
                label="proposal validation manifest candidate",
            )
            validation_failure_category = candidate.get("validation_failure_category")
            changed_surfaces = _string_list(
                candidate,
                "changed_surfaces",
                label="proposal validation manifest candidate",
            )
            if context_round is not None:
                if targeted_mechanism_sha256 not in allowed_mechanisms:
                    candidate_mechanism_violations.append(
                        {
                            "round_index": round_index,
                            "proposal_id": proposal_id,
                            "targeted_mechanism_sha256": targeted_mechanism_sha256,
                            "allowed_mechanism_sha256s": sorted(allowed_mechanisms),
                        }
                    )
                if changed_surfaces and edited_surface_sha256 not in allowed_surfaces:
                    candidate_surface_violations.append(
                        {
                            "round_index": round_index,
                            "proposal_id": proposal_id,
                            "edited_surface_sha256": edited_surface_sha256,
                            "allowed_surface_sha256s": sorted(allowed_surfaces),
                        }
                    )
                unknown_surface_names = sorted(set(changed_surfaces) - allowed_surface_names)
                if changed_surfaces and unknown_surface_names:
                    candidate_surface_name_violations.append(
                        {
                            "round_index": round_index,
                            "proposal_id": proposal_id,
                            "changed_surfaces": list(changed_surfaces),
                            "unknown_surface_names": unknown_surface_names,
                            "allowed_surface_names": sorted(allowed_surface_names),
                        }
                    )
            category_reasons: list[str] = []
            if audit_decision == "invalid":
                if (
                    not isinstance(validation_failure_category, str)
                    or validation_failure_category not in _PROPOSAL_VALIDATION_FAILURE_CATEGORIES
                ):
                    category_reasons.append("invalid_candidate_missing_failure_category")
            elif validation_failure_category is not None:
                category_reasons.append("non_invalid_candidate_has_failure_category")
            if validation_failure_category == "no_editable_surface" and changed_surfaces:
                category_reasons.append("no_editable_surface_changed_surfaces_not_empty")
            if validation_failure_category == "execution_failure" and not changed_surfaces:
                category_reasons.append("execution_failure_missing_changed_surface")
            if category_reasons:
                validation_failure_category_violations.append(
                    {
                        "round_index": round_index,
                        "proposal_id": proposal_id,
                        "audit_decision": audit_decision,
                        "validation_failure_category": validation_failure_category,
                        "changed_surfaces": list(changed_surfaces),
                        "reasons": category_reasons,
                    }
                )
            if audit_decision in {"accepted", "merged"}:
                accepted_merged_surface_candidates.setdefault(edited_surface_sha256, []).append(proposal_id)
                reasons: list[str] = []
                if candidate_held_in_passed < baseline_held_in_passed:
                    reasons.append("held_in_regression")
                if candidate_held_out_passed < baseline_held_out_passed:
                    reasons.append("held_out_regression")
                if (
                    not reasons
                    and candidate_held_in_passed == baseline_held_in_passed
                    and candidate_held_out_passed == baseline_held_out_passed
                ):
                    reasons.append("no_improvement")
                if reasons:
                    acceptance_rule_violations.append(
                        {
                            "round_index": round_index,
                            "proposal_id": proposal_id,
                            "audit_decision": audit_decision,
                            "reasons": reasons,
                            "baseline_held_in_passed": baseline_held_in_passed,
                            "candidate_held_in_passed": candidate_held_in_passed,
                            "baseline_held_out_passed": baseline_held_out_passed,
                            "candidate_held_out_passed": candidate_held_out_passed,
                        }
                    )
            if attempts_per_task is not None:
                if candidate_evaluation_repeats != attempts_per_task:
                    repeat_drift.append(
                        {
                            "round_index": round_index,
                            "proposal_id": proposal_id,
                            "evaluation_repeats": candidate_evaluation_repeats,
                            "expected": attempts_per_task,
                        }
                    )
        row_metadata["candidate_signatures"] = candidate_signature_rows
        accepted_merged_surface_sha256s = {
            surface_sha256: sorted(proposal_ids)
            for surface_sha256, proposal_ids in sorted(accepted_merged_surface_candidates.items())
        }
        row_metadata["accepted_merged_surface_sha256s"] = accepted_merged_surface_sha256s
        for surface_sha256, proposal_ids in accepted_merged_surface_sha256s.items():
            if len(proposal_ids) > 1:
                merge_surface_conflict_violations.append(
                    {
                        "round_index": round_index,
                        "edited_surface_sha256": surface_sha256,
                        "proposal_ids": proposal_ids,
                    }
                )
        if len(set(candidate_signatures)) != len(candidate_signatures):
            seen_signatures: set[tuple[str, str]] = set()
            duplicates: list[dict[str, object]] = []
            for candidate_signature in candidate_signature_rows:
                signature = (
                    str(candidate_signature["targeted_mechanism_sha256"]),
                    str(candidate_signature["edited_surface_sha256"]),
                )
                if signature in seen_signatures:
                    duplicates.append(candidate_signature)
                seen_signatures.add(signature)
            candidate_distinctness_violations.append(
                {
                    "round_index": round_index,
                    "duplicate_signatures": duplicates,
                    "candidate_signatures": candidate_signature_rows,
                }
            )
        round_metadata.append(row_metadata)

    for round_index in sorted(validation_by_round):
        if round_index == 0:
            continue
        previous_index = round_index - 1
        previous_round = validation_by_round.get(previous_index)
        current_round = validation_by_round[round_index]
        if previous_round is None:
            lineage_continuity_violations.append(
                {
                    "round_index": round_index,
                    "previous_round_index": previous_index,
                    "reason": "missing_previous_round",
                }
            )
            continue
        previous_committed = _string_list(
            previous_round,
            "committed_proposal_ids",
            label="proposal validation manifest previous round",
        )
        expected_outcomes: dict[str, object] | None = None
        expected_source: dict[str, object] | None = None
        if not previous_committed:
            expected_outcomes = _object_field(
                previous_round,
                "baseline_split_outcomes",
                label="proposal validation manifest previous round",
            )
            expected_source = {"kind": "previous_baseline"}
        elif len(previous_committed) == 1:
            committed_id = previous_committed[0]
            previous_candidates = _object_list(
                previous_round,
                "candidates",
                label="proposal validation manifest previous round",
            )
            committed_candidate = next(
                (
                    candidate
                    for candidate in previous_candidates
                    if candidate.get("proposal_id") == committed_id
                ),
                None,
            )
            if committed_candidate is None:
                lineage_continuity_violations.append(
                    {
                        "round_index": round_index,
                        "previous_round_index": previous_index,
                        "reason": "missing_committed_candidate",
                        "committed_proposal_id": committed_id,
                    }
                )
                continue
            expected_outcomes = _object_field(
                committed_candidate,
                "split_outcomes",
                label="proposal validation manifest committed candidate",
            )
            expected_source = {
                "kind": "single_committed_candidate",
                "proposal_id": committed_id,
            }
        else:
            merged_split_outcomes = merged_split_outcomes_by_round.get(previous_index)
            if merged_split_outcomes is None:
                lineage_continuity_skipped_rounds.append(
                    {
                        "round_index": round_index,
                        "previous_round_index": previous_index,
                        "reason": "missing_merged_split_outcomes",
                        "committed_proposal_ids": list(previous_committed),
                    }
                )
                continue
            expected_outcomes = merged_split_outcomes
            expected_source = {
                "kind": "merged_split_outcomes",
                "proposal_ids": list(previous_committed),
            }
            merged_split_outcome_lineage_closed_rounds.append(
                {
                    "round_index": round_index,
                    "previous_round_index": previous_index,
                    "committed_proposal_ids": list(previous_committed),
                }
            )

        assert expected_outcomes is not None
        assert expected_source is not None
        current_baseline = _object_field(
            current_round,
            "baseline_split_outcomes",
            label="proposal validation manifest current round",
        )
        expected_projection = _split_outcome_lineage_projection(
            expected_outcomes,
            label="proposal validation manifest expected lineage state",
        )
        actual_projection = _split_outcome_lineage_projection(
            current_baseline,
            label="proposal validation manifest current baseline",
        )
        if actual_projection != expected_projection:
            lineage_continuity_violations.append(
                {
                    "round_index": round_index,
                    "previous_round_index": previous_index,
                    "expected_source": expected_source,
                    "expected": expected_projection,
                    "actual": actual_projection,
                }
            )

    if any(pair is not None for pair in harness_hashes_by_round.values()):
        for round_index in sorted(validation_by_round):
            if round_index == 0:
                continue
            previous_index = round_index - 1
            previous_round = validation_by_round.get(previous_index)
            if previous_round is None:
                harness_continuity_violations.append(
                    {
                        "round_index": round_index,
                        "previous_round_index": previous_index,
                        "reason": "missing_previous_round",
                    }
                )
                continue
            previous_hashes = harness_hashes_by_round.get(previous_index)
            current_hashes = harness_hashes_by_round.get(round_index)
            missing_hash_rounds = [
                index
                for index, hashes in (
                    (previous_index, previous_hashes),
                    (round_index, current_hashes),
                )
                if hashes is None
            ]
            if missing_hash_rounds:
                harness_continuity_missing_rounds.append(
                    {
                        "round_index": round_index,
                        "previous_round_index": previous_index,
                        "missing_hash_rounds": missing_hash_rounds,
                    }
                )
                continue
            assert previous_hashes is not None
            assert current_hashes is not None
            previous_committed = _string_list(
                previous_round,
                "committed_proposal_ids",
                label="proposal validation manifest previous round",
            )
            expected_before_hash: str | None = None
            expected_harness_source: dict[str, object] | None = None
            if not previous_committed:
                expected_before_hash = previous_hashes[0]
                expected_harness_source = {"kind": "previous_baseline_harness"}
            elif len(previous_committed) == 1:
                expected_before_hash = previous_hashes[1]
                expected_harness_source = {
                    "kind": "single_committed_harness_state",
                    "proposal_id": previous_committed[0],
                }
            else:
                merged_hash = merged_harness_hashes_by_round.get(previous_index)
                if merged_hash is None:
                    harness_merged_hash_violations.append(
                        {
                            "round_index": round_index,
                            "previous_round_index": previous_index,
                            "reason": "missing merged hash for multi-commit transition",
                            "committed_proposal_ids": list(previous_committed),
                        }
                    )
                    continue
                expected_before_hash = merged_hash
                expected_harness_source = {
                    "kind": "multi_committed_harness_state",
                    "proposal_ids": list(previous_committed),
                }
            actual_before_hash = current_hashes[0]
            if actual_before_hash != expected_before_hash:
                assert expected_harness_source is not None
                harness_continuity_violations.append(
                    {
                        "round_index": round_index,
                        "previous_round_index": previous_index,
                        "expected_source": expected_harness_source,
                        "expected_harness_before_sha256": expected_before_hash,
                        "actual_harness_before_sha256": actual_before_hash,
                    }
                )

    for context_round_index, context_round in sorted(context_by_round.items()):
        for edit_index, edit in enumerate(_context_previous_edits(context_round)):
            proposal_round_index = _nonnegative_int(
                edit,
                "proposal_round_index",
                label="proposer context manifest previous attempted edit",
            )
            validation_round = validation_by_round.get(proposal_round_index)
            if validation_round is None:
                previous_edit_violations.append(
                    {
                        "round_index": context_round_index,
                        "edit_index": edit_index,
                        "proposal_round_index": proposal_round_index,
                        "reason": "missing validation round",
                    }
                )
                continue
            candidates = _object_list(validation_round, "candidates", label="proposal validation manifest")
            targeted_mechanism_sha256 = _sha256_field(
                edit,
                "targeted_mechanism_sha256",
                label="proposer context manifest previous attempted edit",
            )
            edited_surface_sha256 = _sha256_field(
                edit,
                "edited_surface_sha256",
                label="proposer context manifest previous attempted edit",
            )
            audit_decision = _required_row_str(
                edit,
                "audit_decision",
                label="proposer context manifest previous attempted edit",
            )
            if not any(
                candidate.get("targeted_mechanism_sha256") == targeted_mechanism_sha256
                and candidate.get("edited_surface_sha256") == edited_surface_sha256
                and candidate.get("audit_decision") == audit_decision
                for candidate in candidates
            ):
                previous_edit_violations.append(
                    {
                        "round_index": context_round_index,
                        "edit_index": edit_index,
                        "proposal_round_index": proposal_round_index,
                        "targeted_mechanism_sha256": targeted_mechanism_sha256,
                        "edited_surface_sha256": edited_surface_sha256,
                        "audit_decision": audit_decision,
                    }
                )

    metadata: dict[str, object] = {
        "fixed_protocol_sha256": protocol_hash,
        "validation_fixed_protocol_sha256": validation_hash,
        "split_manifest_held_in_count": split_held_in_count,
        "split_manifest_held_out_count": split_held_out_count,
        "validation_round_count": validation_round_count,
        "protocol_self_harness_rounds": protocol_rounds,
        "protocol_proposal_width": proposal_width,
        "validation_round_indexes": sorted(validation_by_round),
        "rounds": round_metadata,
        "candidate_count_drift": candidate_count_drift,
        "committed_count_drift": committed_count_drift,
        "missing_proposer_rounds": proposer_round_missing,
        "evaluation_repeat_drift": repeat_drift,
        "evaluation_repeats_mismatch_violations": evaluation_repeats_mismatch_violations,
        "baseline_total_violations": baseline_total_violations,
        "candidate_total_violations": candidate_total_violations,
        "acceptance_rule_violations": acceptance_rule_violations,
        "validation_failure_category_violations": validation_failure_category_violations,
        "baseline_task_outcome_violations": baseline_task_outcome_violations,
        "proposer_round_traffic_violations": proposer_round_traffic_violations,
        "candidate_mechanism_violations": candidate_mechanism_violations,
        "candidate_surface_violations": candidate_surface_violations,
        "candidate_surface_name_violations": candidate_surface_name_violations,
        "candidate_distinctness_violations": candidate_distinctness_violations,
        "merge_surface_conflict_violations": merge_surface_conflict_violations,
        "lineage_continuity_violations": lineage_continuity_violations,
        "lineage_continuity_skipped_rounds": lineage_continuity_skipped_rounds,
        "merged_split_outcome_lineage_closed_rounds": merged_split_outcome_lineage_closed_rounds,
        "harness_continuity_violations": harness_continuity_violations,
        "harness_continuity_missing_rounds": harness_continuity_missing_rounds,
        "harness_continuity_skipped_rounds": harness_continuity_skipped_rounds,
        "harness_merged_hash_violations": harness_merged_hash_violations,
        "acceptance_rule_boundary": (
            "accepted and merged proposal validation candidates are compared with their own "
            "round baseline split outcomes, not with the post-commit two-repeat evaluation; invalid "
            "candidates are exempt from this acceptance-rule check and instead record the paper "
            "Section 3.4 no-surface versus execution-failure validation category; accepted or merged "
            "candidates must also target pairwise-distinct editable surface hashes within a round "
            "before MERGEACCEPTED compatibility is trusted"
        ),
        "split_total_binding_boundary": (
            "proposal validation split totals bind to the canonical live split; pass counts are "
            "independent baseline or per-candidate harness-state observations and are not compared "
            "with the post-commit two-repeat evaluation"
        ),
        "previous_edit_validation_violations": previous_edit_violations,
    }
    if candidate_count_drift:
        failures.append("proposal validation candidate counts must match fixed protocol and proposer attempts")
    if committed_count_drift:
        failures.append("proposal validation committed ids must match proposer committed_proposals")
    if proposer_round_missing:
        failures.append("proposal validation rounds must exist in proposer LLM request log")
    if repeat_drift:
        failures.append("proposal validation evaluation_repeats must match live two-repeat evaluation attempts")
    if evaluation_repeats_mismatch_violations:
        failures.append(
            "proposal validation candidate evaluation_repeats must match baseline evaluation_repeats "
            "within each round"
        )
    if baseline_total_violations or candidate_total_violations:
        failures.append("proposal validation split totals must match the canonical live split manifest")
    if acceptance_rule_violations:
        failures.append(
            "accepted or merged candidates must improve at least one split and degrade neither split "
            "versus the round baseline"
        )
    if validation_failure_category_violations:
        failures.append("proposal validation invalid candidates must carry valid failure categories")
    if baseline_task_outcome_violations:
        failures.append(
            "proposal validation baseline task outcomes must cover proposer held-in failure pattern tasks"
        )
    if proposer_round_traffic_violations:
        failures.append("proposal validation proposer-round traffic hashes must match proposer LLM request log")
    if candidate_mechanism_violations:
        failures.append("proposal validation candidates must target same-round proposer context mechanisms")
    if candidate_surface_violations:
        failures.append(
            "proposal validation candidates with changed surfaces must bind to same-round editable surfaces"
        )
    if candidate_surface_name_violations:
        failures.append(
            "proposal validation candidate changed_surfaces must exist in same-round proposer context editable surfaces"
        )
    if candidate_distinctness_violations:
        failures.append("proposal validation candidates must be materially distinct by mechanism and surface")
    if merge_surface_conflict_violations:
        failures.append(
            "accepted or merged proposal validation candidates must target pairwise-distinct editable "
            "surfaces within a round"
        )
    if lineage_continuity_violations:
        failures.append("proposal validation baselines must follow prior committed validation state")
    if harness_continuity_missing_rounds:
        failures.append("proposal validation harness hashes must be complete when declared")
    if harness_merged_hash_violations:
        failures.append("proposal validation multi-commit harness hashes must declare a valid merged state")
    if harness_continuity_violations:
        failures.append("proposal validation harness hashes must follow prior committed validation state")
    if previous_edit_violations:
        failures.append("previous attempted edits must bind to proposal validation candidates")
    if failures:
        return _fail("cross_artifact_proposal_validation_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_proposal_validation_binding",
        (
            "proposal validation manifest binds audit decisions to protocol, proposer, context evidence, "
            "canonical split totals, and same-round acceptance-rule outcomes"
        ),
        metadata=metadata,
    )


def _cross_artifact_capture_run_id_binding(bundle: ReproductionBundle) -> ReproductionBundleCheck | None:
    observed: dict[str, str] = {}
    missing: list[str] = []
    try:
        observed, missing = primary_capture_run_ids(bundle)
    except (OSError, ReproductionBundleError) as exc:
        return _fail(
            "cross_artifact_capture_run_id_binding",
            str(exc),
            metadata={"capture_run_ids_by_artifact": observed, "missing_capture_run_id": sorted(missing)},
        )
    if not observed and not missing:
        return None

    unique_ids = sorted(set(observed.values()))
    metadata: dict[str, object] = {
        "capture_run_ids_by_artifact": observed,
        "missing_capture_run_id": sorted(missing),
        "unique_capture_run_ids": unique_ids,
    }
    failures: list[str] = []
    if missing:
        failures.append("primary captured artifacts must record capture_run_id")
    if len(unique_ids) > 1:
        failures.append("primary captured artifacts must share one capture_run_id")
    if failures:
        return _fail("cross_artifact_capture_run_id_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_capture_run_id_binding",
        "primary captured artifacts share one capture_run_id",
        metadata=metadata,
    )


def _cross_artifact_audit_image_binding(
    bundle: ReproductionBundle,
    audit_entry: ReproductionBundleEntry | None,
    trust_entry: ReproductionBundleEntry | None,
) -> ReproductionBundleCheck | None:
    if audit_entry is None:
        return None
    metadata: dict[str, object] = {}
    try:
        audit = read_artifact_payload(bundle, "live_harbor_audit")
        audit_digests = _audit_image_digests(audit)
        metadata["audit_image_digests"] = sorted(audit_digests)
        if not audit_digests:
            return None
        if trust_entry is None:
            return _fail(
                "cross_artifact_audit_image_binding",
                "container image trust report artifact is missing",
                metadata=metadata,
            )
        trust = read_artifact_payload(bundle, "container_image_trust_report")
        trust_binding = _trust_image_digest_binding(trust)
        metadata["trust_manifest_digests"] = sorted(trust_binding.manifest_digests)
        if trust_binding.mixed_child_digest_declarations:
            metadata["mixed_child_digest_declarations"] = trust_binding.mixed_child_digest_declarations
            return _fail(
                "cross_artifact_audit_image_binding",
                "container image trust report child_digests must be declared for every image or none",
                metadata=metadata,
            )
    except (OSError, ReproductionBundleError) as exc:
        return _fail("cross_artifact_audit_image_binding", str(exc), metadata=metadata)

    if trust_binding.child_digests:
        trust_child_digests = trust_binding.child_digests
        missing_from_trust = sorted(audit_digests - trust_child_digests)
        extra_in_trust = sorted(trust_child_digests - audit_digests)
        metadata["trust_image_binding_mode"] = "child-digests"
        metadata["trust_child_digests"] = sorted(trust_child_digests)
        metadata["trust_child_digest_map"] = list(trust_binding.child_digest_map)
        metadata["missing_from_trust_children"] = missing_from_trust
        metadata["extra_in_trust_children"] = extra_in_trust
        failure_detail = "live Harbor audit image_digest values must exist in container image trust child_digests"
    else:
        trust_digests = trust_binding.manifest_digests
        missing_from_trust = sorted(audit_digests - trust_digests)
        extra_in_trust = sorted(trust_digests - audit_digests)
        metadata["trust_image_binding_mode"] = "manifest-digests"
        metadata["trust_image_digests"] = sorted(trust_digests)
        metadata["missing_from_trust"] = missing_from_trust
        metadata["extra_in_trust"] = extra_in_trust
        failure_detail = "live Harbor audit image_digest values must exist in container image trust report"
    failures: list[str] = []
    if missing_from_trust:
        failures.append(failure_detail)
    if extra_in_trust and not trust_binding.child_digests:
        failures.append("container image trust report digests must match live Harbor audit image_digest values")
    if failures:
        return _fail("cross_artifact_audit_image_binding", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_audit_image_binding",
        "live Harbor audit image digests match the container image trust report",
        metadata=metadata,
    )


def primary_capture_run_ids(bundle: ReproductionBundle) -> tuple[dict[str, str], list[str]]:
    """Return primary captured artifact capture_run_id values keyed by artifact class."""

    primary_entries = sorted(
        (
            entry
            for entry in bundle.entries
            if entry.required_artifact_class in _PRIMARY_CAPTURED_ARTIFACT_CLASSES
        ),
        key=lambda entry: entry.required_artifact_class,
    )
    observed: dict[str, str] = {}
    missing: list[str] = []
    for entry in primary_entries:
        payload = read_artifact_payload(bundle, entry.required_artifact_class)
        value = payload.get("capture_run_id")
        if isinstance(value, str) and value:
            observed[entry.required_artifact_class] = value
        else:
            missing.append(entry.required_artifact_class)
    return observed, sorted(missing)


def _split_outcome_lineage_projection(
    row: Mapping[str, object],
    *,
    label: str,
) -> dict[str, int]:
    return {
        "held_in_passed": _nonnegative_int(row, "held_in_passed", label=label),
        "held_in_total": _nonnegative_int(row, "held_in_total", label=label),
        "held_out_passed": _nonnegative_int(row, "held_out_passed", label=label),
        "held_out_total": _nonnegative_int(row, "held_out_total", label=label),
        "evaluation_repeats": _positive_int(row, "evaluation_repeats", label=label),
    }


def _cross_artifact_evaluation_audit_outcomes(
    evaluation_rows: Sequence[Mapping[str, object]],
    audit_rows: Sequence[Mapping[str, object]],
) -> ReproductionBundleCheck:
    evaluation_by_task: dict[str, tuple[bool, ...]] = {}
    audit_by_task: dict[str, tuple[bool, ...]] = {}
    audit_outcomes: dict[str, str] = {}
    try:
        for row in evaluation_rows:
            task_id = _required_row_str(row, "task_id", label="live two-repeat evaluation report")
            attempts = _object_list(row, "attempts", label="live two-repeat evaluation report")
            evaluation_by_task[task_id] = tuple(
                _required_bool(attempt, "pass", label="live two-repeat evaluation report")
                for attempt in attempts
            )
        for row in audit_rows:
            task_id = _required_row_str(row, "task_id", label="live Harbor audit")
            attempts = _object_list(row, "attempts", label="live Harbor audit")
            by_index: dict[int, bool] = {}
            for attempt in attempts:
                attempt_index = _nonnegative_int(
                    attempt,
                    "attempt_index",
                    label="live Harbor audit",
                )
                by_index[attempt_index] = _required_bool(attempt, "pass", label="live Harbor audit")
            audit_by_task[task_id] = tuple(by_index[index] for index in sorted(by_index))
            audit_outcomes[task_id] = _required_row_str(row, "verifier_outcome", label="live Harbor audit")
    except ReproductionBundleError as exc:
        return _fail("cross_artifact_evaluation_audit_outcomes", str(exc))

    evaluation_ids = set(evaluation_by_task)
    audit_ids = set(audit_by_task)
    missing_from_audit = sorted(evaluation_ids - audit_ids)
    extra_in_audit = sorted(audit_ids - evaluation_ids)
    per_attempt_mismatches: list[dict[str, object]] = []
    verifier_outcome_mismatches: list[dict[str, object]] = []
    for task_id in sorted(evaluation_ids & audit_ids):
        evaluation_attempts = evaluation_by_task[task_id]
        audit_attempts = audit_by_task[task_id]
        for attempt_index, (evaluation_pass, audit_pass) in enumerate(
            zip(evaluation_attempts, audit_attempts, strict=False)
        ):
            if evaluation_pass != audit_pass:
                per_attempt_mismatches.append(
                    {
                        "task_id": task_id,
                        "attempt_index": attempt_index,
                        "evaluation_pass": evaluation_pass,
                        "audit_pass": audit_pass,
                    }
                )
        if len(evaluation_attempts) != len(audit_attempts):
            per_attempt_mismatches.append(
                {
                    "task_id": task_id,
                    "evaluation_attempt_count": len(evaluation_attempts),
                    "audit_attempt_count": len(audit_attempts),
                }
            )
        expected_outcome = "pass" if all(evaluation_attempts) else "fail"
        actual_outcome = audit_outcomes[task_id]
        if actual_outcome != expected_outcome:
            verifier_outcome_mismatches.append(
                {
                    "task_id": task_id,
                    "expected": expected_outcome,
                    "actual": actual_outcome,
                }
            )

    metadata: dict[str, object] = {
        "evaluation_task_count": len(evaluation_by_task),
        "audit_task_count": len(audit_by_task),
        "missing_from_audit": missing_from_audit,
        "extra_in_audit": extra_in_audit,
        "per_attempt_mismatches": per_attempt_mismatches,
        "verifier_outcome_mismatches": verifier_outcome_mismatches,
    }
    failures: list[str] = []
    if missing_from_audit or extra_in_audit:
        failures.append("live Harbor audit task ids must equal two-repeat evaluation ids")
    if per_attempt_mismatches:
        failures.append("live Harbor audit attempt pass values must match two-repeat evaluation attempts")
    if verifier_outcome_mismatches:
        failures.append("live Harbor audit verifier_outcome must match evaluation-derived task outcome")
    if failures:
        return _fail("cross_artifact_evaluation_audit_outcomes", "; ".join(failures), metadata=metadata)
    return _pass(
        "cross_artifact_evaluation_audit_outcomes",
        "two-repeat evaluation outcomes match live Harbor audit attempt outcomes",
        metadata=metadata,
    )


def _held_in_pass_sets_from_evaluation(
    evaluation: Mapping[str, object],
    held_in_ids: set[str],
) -> tuple[frozenset[str], frozenset[str]]:
    rows = _object_list(evaluation, "per_task_attempts", label="live two-repeat evaluation report")
    observed: set[str] = set()
    failing: set[str] = set()
    passing: set[str] = set()
    for row in rows:
        task_id = _required_row_str(row, "task_id", label="live two-repeat evaluation report")
        if task_id not in held_in_ids:
            continue
        observed.add(task_id)
        attempts = _object_list(row, "attempts", label="live two-repeat evaluation report")
        pass_values = [
            _required_bool(attempt, "pass", label="live two-repeat evaluation report")
            for attempt in attempts
        ]
        if all(pass_values):
            passing.add(task_id)
        else:
            failing.add(task_id)
    missing = sorted(held_in_ids - observed)
    if missing:
        raise ReproductionBundleError(
            "live two-repeat evaluation report missing held-in task ids: " + ", ".join(missing)
        )
    return frozenset(failing), frozenset(passing)


def _held_in_pass_sets_from_audit(
    audit: Mapping[str, object],
    held_in_ids: set[str],
) -> tuple[frozenset[str], frozenset[str]]:
    rows = _object_list(audit, "trial_artifacts", label="live Harbor audit")
    observed: set[str] = set()
    failing: set[str] = set()
    passing: set[str] = set()
    for row in rows:
        task_id = _required_row_str(row, "task_id", label="live Harbor audit")
        if task_id not in held_in_ids:
            continue
        observed.add(task_id)
        attempts = _object_list(row, "attempts", label="live Harbor audit")
        pass_values = [
            _required_bool(attempt, "pass", label="live Harbor audit")
            for attempt in attempts
        ]
        if all(pass_values):
            passing.add(task_id)
        else:
            failing.add(task_id)
    missing = sorted(held_in_ids - observed)
    if missing:
        raise ReproductionBundleError("live Harbor audit missing held-in task ids: " + ", ".join(missing))
    return frozenset(failing), frozenset(passing)


def _context_failure_patterns(row: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    block = row.get("held_in_failure_patterns")
    if not isinstance(block, dict):
        raise ReproductionBundleError("proposer context manifest held_in_failure_patterns must be an object")
    return _object_list(block, "patterns", label="proposer context manifest held_in_failure_patterns")


def _context_passing_summaries(row: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    block = row.get("passing_behavior_summaries")
    if not isinstance(block, dict):
        raise ReproductionBundleError("proposer context manifest passing_behavior_summaries must be an object")
    return _object_list(block, "summaries", label="proposer context manifest passing_behavior_summaries")


def _context_previous_edits(row: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    block = row.get("previous_attempted_edits")
    if not isinstance(block, dict):
        raise ReproductionBundleError("proposer context manifest previous_attempted_edits must be an object")
    return _object_list(block, "edits", label="proposer context manifest previous_attempted_edits")


def _optional_task_outcomes(row: Mapping[str, object], *, label: str) -> tuple[dict[str, object], ...]:
    value = row.get("task_outcomes")
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ReproductionBundleError(f"{label}.task_outcomes must be a list of objects")
    return tuple(dict(item) for item in value)


def _failing_held_in_task_ids_from_task_outcomes(rows: Sequence[Mapping[str, object]]) -> frozenset[str]:
    failing: set[str] = set()
    for row in rows:
        split = _required_row_str(row, "split", label="proposal validation task outcome")
        if split != "held_in":
            continue
        task_id = _required_row_str(row, "task_id", label="proposal validation task outcome")
        passed = row.get("pass")
        if not isinstance(passed, bool):
            raise ReproductionBundleError("proposal validation task outcome pass must be boolean")
        if not passed:
            failing.add(task_id)
    return frozenset(failing)


def _held_in_failure_categories_from_task_outcomes(
    rows: Sequence[Mapping[str, object]],
    *,
    label: str,
) -> dict[str, frozenset[str]]:
    categories_by_task: dict[str, set[str]] = {}
    for row in rows:
        split = _required_row_str(row, "split", label=f"{label} task_outcome")
        if split != "held_in":
            continue
        passed = row.get("pass")
        if not isinstance(passed, bool):
            raise ReproductionBundleError(f"{label} task_outcome pass must be boolean")
        if passed:
            continue
        category = row.get("failure_category")
        if category is None:
            continue
        if not isinstance(category, str) or not category:
            raise ReproductionBundleError(f"{label} task_outcome failure_category must be a non-empty string or null")
        task_id = _required_row_str(row, "task_id", label=f"{label} task_outcome")
        categories_by_task.setdefault(task_id, set()).add(category)
    return {task_id: frozenset(categories) for task_id, categories in categories_by_task.items()}


def _held_in_pass_sets_from_task_outcomes(
    rows: Sequence[Mapping[str, object]],
    held_in_ids: set[str],
    *,
    label: str,
) -> tuple[frozenset[str], frozenset[str], list[str], list[str]]:
    observed: set[str] = set()
    failing: set[str] = set()
    passing: set[str] = set()
    for row in rows:
        split = _required_row_str(row, "split", label=f"{label} task_outcome")
        if split != "held_in":
            continue
        task_id = _required_row_str(row, "task_id", label=f"{label} task_outcome")
        passed = row.get("pass")
        if not isinstance(passed, bool):
            raise ReproductionBundleError(f"{label} task_outcome pass must be boolean")
        observed.add(task_id)
        if passed:
            passing.add(task_id)
        else:
            failing.add(task_id)
    missing_task_ids = sorted(held_in_ids - observed)
    extra_task_ids = sorted(observed - held_in_ids)
    return frozenset(failing), frozenset(passing), missing_task_ids, extra_task_ids


def _context_failure_mechanism_sha256s(row: Mapping[str, object]) -> frozenset[str]:
    return frozenset(
        _sha256_field(pattern, "mechanism_sha256", label="proposer context manifest failure pattern")
        for pattern in _context_failure_patterns(row)
    )


def _context_failure_causal_status_sha256s_by_mechanism(
    row: Mapping[str, object],
) -> dict[str, frozenset[str]]:
    values: dict[str, set[str]] = {}
    for pattern in _context_failure_patterns(row):
        mechanism_sha256 = _sha256_field(
            pattern,
            "mechanism_sha256",
            label="proposer context manifest failure pattern",
        )
        causal_status_sha256 = _optional_sha256_field(
            pattern,
            "causal_status_sha256",
            label="proposer context manifest failure pattern",
        )
        if causal_status_sha256 is not None:
            values.setdefault(mechanism_sha256, set()).add(causal_status_sha256)
    return {mechanism: frozenset(causal_statuses) for mechanism, causal_statuses in values.items()}


def _context_editable_surface_rows(row: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    block = row.get("editable_surfaces")
    if not isinstance(block, dict):
        raise ReproductionBundleError("proposer context manifest editable_surfaces must be an object")
    surfaces = _object_list(block, "surfaces", label="proposer context manifest editable_surfaces")
    return tuple(surfaces)


def _context_editable_surface_sha256s(row: Mapping[str, object]) -> frozenset[str]:
    return frozenset(
        _sha256_field(surface, "sha256", label="proposer context manifest editable surface")
        for surface in _context_editable_surface_rows(row)
    )


def _context_editable_surface_names(row: Mapping[str, object]) -> frozenset[str]:
    return frozenset(
        _required_row_str(surface, "name", label="proposer context manifest editable surface")
        for surface in _context_editable_surface_rows(row)
    )


def _validation_rounds_by_index(
    rounds: Sequence[Mapping[str, object]],
) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for row in rounds:
        round_index = _nonnegative_int(row, "round_index", label="proposal validation manifest")
        if round_index in result:
            raise ReproductionBundleError("proposal validation manifest round indexes must be unique")
        result[round_index] = dict(row)
    return result


def _task_id_list(data: Mapping[str, object], key: str, *, label: str) -> tuple[str, ...]:
    values = _string_list(data, key, label=label)
    if not values or any(not value for value in values):
        raise ReproductionBundleError(f"{label} {key} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise ReproductionBundleError(f"{label} {key} must not contain duplicates")
    return values


def _task_id_set_sha256(task_ids: set[str]) -> str:
    payload = {"task_ids": sorted(task_ids)}
    return sha256((stable_json_dumps(payload) + "\n").encode("utf-8")).hexdigest()


def _empty_context_ingredients(row: Mapping[str, object], *, round_index: int) -> list[str]:
    empty: list[str] = []
    block_specs = (
        ("editable_surfaces", "surface_count"),
        ("held_in_failure_patterns", "pattern_count"),
        ("passing_behavior_summaries", "summary_count"),
    )
    for block_name, count_key in block_specs:
        block = row.get(block_name)
        if not isinstance(block, dict):
            empty.append(block_name)
            continue
        count = block.get(count_key)
        if not isinstance(count, int) or count < 1:
            empty.append(block_name)
    previous = row.get("previous_attempted_edits")
    if not isinstance(previous, dict):
        empty.append("previous_attempted_edits")
    else:
        count = previous.get("edit_count")
        if round_index > 0 and (not isinstance(count, int) or count < 1):
            empty.append("previous_attempted_edits")
        elif round_index == 0 and (not isinstance(count, int) or count < 0):
            empty.append("previous_attempted_edits")
    return empty


def _signature_checks(
    bundle_path: Path,
    signature_path: Path | None,
    public_key: Path | str | None,
    *,
    require_signature: bool,
) -> list[ReproductionBundleCheck]:
    if signature_path is None:
        if require_signature:
            return [_fail("bundle_signature", "bundle signature is required but was not supplied")]
        return [_pass("bundle_signature", "bundle signature not supplied; optional for advisory verification")]
    try:
        bundle_bytes = bundle_path.read_bytes()
        signature = _load_signature(signature_path)
        expected_hash = sha256(bundle_bytes).hexdigest()
        if signature["manifest_sha256"] != expected_hash:
            raise ReproductionBundleError("bundle signature manifest_sha256 does not match bundle bytes")
        if signature["manifest_filename"] != bundle_path.name:
            raise ReproductionBundleError("bundle signature manifest_filename does not match bundle path")
        embedded_public_key = str(signature["public_key_b64"])
        embedded_fingerprint = public_key_fingerprint(embedded_public_key)
        if signature["fingerprint"] != embedded_fingerprint:
            raise ReproductionBundleError("bundle signature embedded public key does not match fingerprint")
        verification_key: Path | str = embedded_public_key if public_key is None else public_key
        if public_key is not None:
            trusted_fingerprint = public_key_fingerprint(public_key)
            if trusted_fingerprint != signature["fingerprint"]:
                raise ReproductionBundleError("trusted public key does not match bundle signature fingerprint")
            if signature["public_key_b64"] != public_key_raw_b64(public_key):
                raise ReproductionBundleError("bundle signature embedded public key does not match trusted public key")
        verify_bytes_signature(bundle_bytes, str(signature["signature_b64"]), verification_key)
    except (OSError, CorpusSigningError, ReproductionBundleError) as exc:
        return [_fail("bundle_signature", str(exc), path=signature_path)]
    return [
        _pass(
            "bundle_signature",
            "bundle signature verified",
            path=signature_path,
            metadata={"fingerprint": str(signature["fingerprint"]), "key_id": str(signature["key_id"])},
        )
    ]


def _load_signature(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReproductionBundleError(f"missing bundle signature sidecar: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReproductionBundleError(f"invalid bundle signature JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReproductionBundleError("bundle signature sidecar must be a JSON object")
    signature = cast(dict[str, object], value)
    unknown = sorted(set(signature) - _SIGNATURE_FIELDS)
    if unknown:
        raise ReproductionBundleError(f"bundle signature has unknown field(s): {', '.join(unknown)}")
    if signature.get("schema_version") != REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION:
        raise ReproductionBundleError("unsupported bundle signature schema_version")
    if signature.get("signature_algorithm") != REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM:
        raise ReproductionBundleError("unsupported bundle signature_algorithm")
    if signature.get("fingerprint_algorithm") != FINGERPRINT_ALGORITHM:
        raise ReproductionBundleError("unsupported bundle signature fingerprint_algorithm")
    for key in ("manifest_sha256", "fingerprint"):
        _sha256_field(signature, key, label="bundle signature")
    for key in ("signature_b64", "public_key_b64", "provider", "key_id", "manifest_filename"):
        _required_str(signature, key, label="bundle signature", allow_empty=(key == "key_id"))
    if Path(str(signature["manifest_filename"])).name != signature["manifest_filename"]:
        raise ReproductionBundleError("bundle signature manifest_filename must be a basename")
    return signature


def _entry_from_json(value: object, *, index: int) -> ReproductionBundleEntry:
    if not isinstance(value, dict):
        raise ReproductionBundleError(f"reproduction bundle entry {index} must be an object")
    data = cast(dict[str, object], value)
    unknown = sorted(set(data) - _ENTRY_FIELDS)
    if unknown:
        raise ReproductionBundleError(f"reproduction bundle entry {index} has unknown field(s): {', '.join(unknown)}")
    source = _source(data.get("source"), index)
    notes_value = data.get("notes")
    if notes_value is not None and not isinstance(notes_value, str):
        raise ReproductionBundleError(f"reproduction bundle entry {index} notes must be a string when present")
    return ReproductionBundleEntry(
        required_artifact_class=_required_str(data, "required_artifact_class", label=f"entry {index}"),
        path=_required_str(data, "path", label=f"entry {index}"),
        sha256=_sha256_field(data, "sha256", label=f"entry {index}"),
        byte_size=_nonnegative_int(data, "byte_size", label=f"entry {index}"),
        source=source,
        notes=notes_value,
    )


def _source(value: object, index: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ReproductionBundleError(f"reproduction bundle entry {index} source must be an object")
    source = cast(dict[str, object], value)
    unknown = sorted(set(source) - _SOURCE_FIELDS)
    if unknown:
        formatted = ", ".join(unknown)
        raise ReproductionBundleError(
            f"reproduction bundle entry {index} source has unknown field(s): {formatted}"
        )
    result: dict[str, str] = {}
    for key, item in source.items():
        if not isinstance(item, str) or not item:
            raise ReproductionBundleError(f"reproduction bundle entry {index} source {key} must be a non-empty string")
        result[key] = item
    return result


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReproductionBundleError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReproductionBundleError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ReproductionBundleError(f"{label} must be a JSON object")
    return cast(dict[str, object], data)


def _string_list(data: Mapping[str, object], key: str, *, label: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReproductionBundleError(f"{label} {key} must be a list of strings")
    return tuple(value)


def _object_list(data: Mapping[str, object], key: str, *, label: str) -> tuple[dict[str, object], ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ReproductionBundleError(f"{label} {key} must be a list of objects")
    return tuple(cast(dict[str, object], item) for item in value)


def _object_field(data: Mapping[str, object], key: str, *, label: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ReproductionBundleError(f"{label} {key} must be an object")
    return cast(dict[str, object], value)


def _audit_image_digests(audit: Mapping[str, object]) -> frozenset[str]:
    rows = _object_list(audit, "trial_artifacts", label="live Harbor audit")
    digests: set[str] = set()
    for row in rows:
        task_id = _required_row_str(row, "task_id", label="live Harbor audit")
        value = row.get("image_digest")
        if value is None:
            continue
        digests.add(_image_digest_field({"image_digest": value}, "image_digest", label=f"live Harbor audit {task_id}"))
    return frozenset(digests)


def _trust_image_digests(trust: Mapping[str, object]) -> frozenset[str]:
    return _trust_image_digest_binding(trust).manifest_digests


def _proposer_llm_backends(proposer: Mapping[str, object]) -> frozenset[str]:
    rounds = _object_list(proposer, "rounds", label="proposer LLM request log")
    backends = [
        _required_row_str(row, "backend", label="proposer LLM request log")
        for row in rounds
    ]
    return _normal_model_backends(tuple(backends))


def _trust_image_digest_binding(trust: Mapping[str, object]) -> _TrustImageDigestBinding:
    images = _object_list(trust, "images", label="container image trust report")
    manifest_digests: set[str] = set()
    child_digests: set[str] = set()
    child_digest_map: list[dict[str, object]] = []
    with_children: list[str] = []
    without_children: list[str] = []
    for index, image in enumerate(images):
        name = _required_row_str(image, "name", label="container image trust report")
        manifest_digest = _image_digest_field(image, "digest", label="container image trust report")
        manifest_digests.add(manifest_digest)
        if "child_digests" not in image:
            without_children.append(name)
            continue
        values = _image_digest_list_field(
            image,
            "child_digests",
            label=f"container image trust report images[{index}]",
        )
        with_children.append(name)
        child_digests.update(values)
        child_digest_map.append(
            {
                "name": name,
                "manifest_digest": manifest_digest,
                "child_digests": sorted(values),
            }
        )
    mixed: dict[str, object] | None = None
    if with_children and without_children:
        mixed = {
            "with_child_digests": sorted(with_children),
            "without_child_digests": sorted(without_children),
        }
    return _TrustImageDigestBinding(
        manifest_digests=frozenset(manifest_digests),
        child_digests=frozenset(child_digests),
        child_digest_map=tuple(child_digest_map),
        mixed_child_digest_declarations=mixed,
    )


def _required_row_str(data: Mapping[str, object], key: str, *, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ReproductionBundleError(f"{label} {key} must be a non-empty string")
    return value


def _check_to_jsonable(check: ReproductionBundleCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "artifact_class": check.artifact_class,
        "path": check.path,
        "metadata": check.metadata,
    }


def _pass(
    name: str,
    detail: str,
    *,
    artifact_class: str | None = None,
    path: Path | str | None = None,
    metadata: dict[str, object] | None = None,
) -> ReproductionBundleCheck:
    return ReproductionBundleCheck(
        name=name,
        status="pass",
        detail=detail,
        artifact_class=artifact_class,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _fail(
    name: str,
    detail: str,
    *,
    artifact_class: str | None = None,
    path: Path | str | None = None,
    metadata: dict[str, object] | None = None,
) -> ReproductionBundleCheck:
    return ReproductionBundleCheck(
        name=name,
        status="fail",
        detail=detail,
        artifact_class=artifact_class,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _entry_check_name(artifact_class: str) -> str:
    return "artifact_" + artifact_class


def _required_str(
    data: Mapping[str, object],
    key: str,
    *,
    label: str,
    allow_empty: bool = False,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ReproductionBundleError(f"{label} missing non-empty string field: {key}")
    return value


def _sha256_field(data: Mapping[str, object], key: str, *, label: str) -> str:
    value = _required_str(data, key, label=label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ReproductionBundleError(f"{label} {key} must be a lowercase sha256 digest")
    return value


def _optional_sha256_field(data: Mapping[str, object], key: str, *, label: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ReproductionBundleError(f"{label} {key} must be a lowercase sha256 digest or null")
    return value


def _image_digest_field(data: Mapping[str, object], key: str, *, label: str) -> str:
    value = _required_str(data, key, label=label)
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise ReproductionBundleError(f"{label} {key} must be sha256:<64 lowercase hex>")
    digest = value.removeprefix(prefix)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ReproductionBundleError(f"{label} {key} must be sha256:<64 lowercase hex>")
    return value


def _image_digest_list_field(data: Mapping[str, object], key: str, *, label: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ReproductionBundleError(f"{label} {key} must be a non-empty list")
    digests = tuple(_image_digest_field({"digest": item}, "digest", label=f"{label} {key}") for item in value)
    if len(set(digests)) != len(digests):
        raise ReproductionBundleError(f"{label} {key} must not contain duplicates")
    return digests


def _nonnegative_int(data: Mapping[str, object], key: str, *, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value < 0:
        raise ReproductionBundleError(f"{label} {key} must be a non-negative integer")
    return value


def _required_bool(data: Mapping[str, object], key: str, *, label: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ReproductionBundleError(f"{label} {key} must be boolean")
    return value


def _positive_int(data: Mapping[str, object], key: str, *, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ReproductionBundleError(f"{label} {key} must be a positive integer")
    return value
