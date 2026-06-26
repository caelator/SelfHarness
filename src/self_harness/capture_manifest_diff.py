from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from self_harness._artifact_shapes import _normal_model_backends
from self_harness.capture_manifest import (
    CaptureManifest,
    CaptureManifestEntry,
    CaptureManifestError,
    load_capture_manifest,
    load_capture_manifest_signature,
    verify_capture_manifest,
)
from self_harness.reproduction_bundle import (
    ReproductionBundle,
    ReproductionBundleEntry,
    ReproductionBundleError,
    load_reproduction_bundle,
    primary_capture_run_ids,
    read_artifact_payload,
    verify_reproduction_bundle,
)
from self_harness.reproduction_readiness import ReproductionRequirement
from self_harness.types import stable_json_dumps

CAPTURE_MANIFEST_DIFF_SCHEMA_VERSION = "1.0"
CAPTURE_MANIFEST_DIFF_BOUNDARY = (
    "operator capture plan versus reproduction bundle diff only; compares existing local "
    "manifest and bundle metadata without contacting Harbor, Docker, registries, scanners, "
    "PyPI, Sigstore, model providers, or cloud services, and never claims benchmark reproduction"
)
TASK_OUTCOMES_DIGEST_VERSION = 2
_PROPOSAL_VALIDATION_FAILURE_CATEGORY_KEYS = ("execution_failure", "no_editable_surface", "none")


@dataclass(frozen=True)
class CaptureManifestDiffFinding:
    category: str
    status: str
    detail: str
    artifact_class: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class CaptureManifestDiffReport:
    schema_version: str
    ok: bool
    manifest_path: str
    bundle_path: str
    manifest_id: str | None
    bundle_id: str | None
    findings: tuple[CaptureManifestDiffFinding, ...]
    matched_count: int
    report_hash: str
    reproduction_claimed: bool
    boundary: str


@dataclass(frozen=True)
class _TrustImageDigestBinding:
    manifest_digests: frozenset[str]
    child_digests: frozenset[str]
    child_digest_map: tuple[dict[str, object], ...]
    mixed_child_digest_declarations: dict[str, object] | None


def diff_capture_manifest_to_bundle(
    manifest_path: Path,
    bundle_path: Path,
    requirements: Sequence[ReproductionRequirement],
    *,
    manifest_signature_path: Path | None = None,
    bundle_signature_path: Path | None = None,
    require_manifest_signature: bool = False,
    require_bundle_signature: bool = False,
) -> CaptureManifestDiffReport:
    findings: list[CaptureManifestDiffFinding] = []
    manifest: CaptureManifest | None = None
    bundle: ReproductionBundle | None = None
    manifest_report = verify_capture_manifest(
        manifest_path,
        requirements,
        signature_path=manifest_signature_path,
        require_signature=require_manifest_signature,
    )
    if not manifest_report.ok:
        findings.append(
            _fail(
                "manifest-invalid",
                "capture manifest verification failed",
                metadata={"report_hash": manifest_report.report_hash},
            )
        )
    try:
        manifest = load_capture_manifest(manifest_path)
    except CaptureManifestError as exc:
        findings.append(_fail("manifest-load", str(exc)))

    bundle_report = verify_reproduction_bundle(
        bundle_path,
        requirements,
        signature_path=bundle_signature_path,
        require_signature=require_bundle_signature,
    )
    if not bundle_report.ok:
        findings.append(
            _fail(
                "bundle-invalid",
                "reproduction bundle verification failed",
                metadata={"report_hash": bundle_report.report_hash},
            )
        )
    try:
        bundle = load_reproduction_bundle(bundle_path)
    except ReproductionBundleError as exc:
        findings.append(_fail("bundle-load", str(exc)))

    matched_count = 0
    if manifest is not None and bundle is not None:
        binding_findings = _binding_findings(manifest, bundle)
        findings.extend(binding_findings)
        entry_findings, matched_count = _entry_findings(manifest, bundle)
        findings.extend(entry_findings)
        findings.extend(_capture_run_id_findings(manifest, bundle))
        findings.extend(_fixed_protocol_findings(manifest, bundle))
        findings.extend(_proposer_context_evidence_findings(manifest, bundle))
        findings.extend(_proposal_validation_findings(manifest, bundle))
        findings.extend(_audit_image_findings(manifest, bundle))
        findings.extend(_network_control_findings(manifest, bundle))
        findings.extend(_custody_findings(manifest, bundle_signature_path))

    ok = all(finding.status != "fail" for finding in findings)
    report_without_hash = {
        "schema_version": CAPTURE_MANIFEST_DIFF_SCHEMA_VERSION,
        "ok": ok,
        "manifest_path": str(manifest_path),
        "bundle_path": str(bundle_path),
        "manifest_id": manifest.manifest_id if manifest is not None else None,
        "bundle_id": bundle.bundle_id if bundle is not None else None,
        "findings": [_finding_to_jsonable(finding) for finding in findings],
        "matched_count": matched_count,
        "reproduction_claimed": False,
        "boundary": CAPTURE_MANIFEST_DIFF_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return CaptureManifestDiffReport(
        schema_version=CAPTURE_MANIFEST_DIFF_SCHEMA_VERSION,
        ok=ok,
        manifest_path=str(manifest_path),
        bundle_path=str(bundle_path),
        manifest_id=manifest.manifest_id if manifest is not None else None,
        bundle_id=bundle.bundle_id if bundle is not None else None,
        findings=tuple(findings),
        matched_count=matched_count,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=CAPTURE_MANIFEST_DIFF_BOUNDARY,
    )


def capture_manifest_diff_report_to_jsonable(report: CaptureManifestDiffReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "manifest_path": report.manifest_path,
        "bundle_path": report.bundle_path,
        "manifest_id": report.manifest_id,
        "bundle_id": report.bundle_id,
        "findings": [_finding_to_jsonable(finding) for finding in report.findings],
        "matched_count": report.matched_count,
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def _binding_findings(manifest: CaptureManifest, bundle: ReproductionBundle) -> list[CaptureManifestDiffFinding]:
    findings: list[CaptureManifestDiffFinding] = []
    if manifest.bundle_id == bundle.bundle_id:
        findings.append(_pass("bundle-binding", "capture manifest bundle_id matches reproduction bundle"))
    else:
        findings.append(
            _fail(
                "bundle-binding",
                "capture manifest bundle_id does not match reproduction bundle",
                metadata={"expected": manifest.bundle_id, "actual": bundle.bundle_id},
            )
        )
    if manifest.operator_label == bundle.operator_label:
        findings.append(_pass("operator-label", "operator label matches"))
    else:
        findings.append(
            _fail(
                "operator-label",
                "operator label drift",
                metadata={"expected": manifest.operator_label, "actual": bundle.operator_label},
            )
        )
    return findings


def _entry_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> tuple[list[CaptureManifestDiffFinding], int]:
    findings: list[CaptureManifestDiffFinding] = []
    manifest_by_class = {entry.required_artifact_class: entry for entry in manifest.entries}
    bundle_by_class = {entry.required_artifact_class: entry for entry in bundle.entries}
    missing = sorted(set(manifest_by_class) - set(bundle_by_class))
    extra = sorted(set(bundle_by_class) - set(manifest_by_class))
    for artifact_class in missing:
        findings.append(_fail("missing-in-bundle", "planned artifact class missing from bundle", artifact_class))
    for artifact_class in extra:
        findings.append(_fail("extra-in-bundle", "bundle contains unplanned artifact class", artifact_class))
    matched_count = 0
    for artifact_class in sorted(set(manifest_by_class) & set(bundle_by_class)):
        planned = manifest_by_class[artifact_class]
        actual = bundle_by_class[artifact_class]
        findings.extend(_source_findings(planned, actual))
        matched_count += 1
    return findings, matched_count


def _source_findings(
    planned: CaptureManifestEntry,
    actual: ReproductionBundleEntry,
) -> list[CaptureManifestDiffFinding]:
    findings: list[CaptureManifestDiffFinding] = []
    planned_source = planned.planned_source
    actual_source = actual.source
    if actual_source.get("provider") == planned_source["provider"]:
        findings.append(_pass("source-provider", "source provider matches", planned.required_artifact_class))
    else:
        findings.append(
            _fail(
                "source-provider-drift",
                "source provider drift",
                planned.required_artifact_class,
                metadata={"expected": planned_source["provider"], "actual": actual_source.get("provider")},
            )
        )
    if actual_source.get("operator_label") != planned_source["operator_label"]:
        findings.append(
            _fail(
                "source-operator-drift",
                "source operator label drift",
                planned.required_artifact_class,
                metadata={"expected": planned_source["operator_label"], "actual": actual_source.get("operator_label")},
            )
        )
    captured_at = actual_source.get("captured_at")
    if captured_at is None:
        findings.append(
            _fail("capture-window-drift", "bundle source missing captured_at", planned.required_artifact_class)
        )
    elif planned_source["captured_after"] <= captured_at <= planned_source["captured_before"]:
        findings.append(
            _pass("capture-window", "capture timestamp is inside planned window", planned.required_artifact_class)
        )
    else:
        findings.append(
            _advisory(
                "capture-window-drift",
                "capture timestamp is outside planned window",
                planned.required_artifact_class,
                metadata={
                    "captured_after": planned_source["captured_after"],
                    "captured_before": planned_source["captured_before"],
                    "actual": captured_at,
                },
            )
        )
    return findings


def _capture_run_id_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> list[CaptureManifestDiffFinding]:
    expected = manifest.planned_run.get("run_id")
    if not isinstance(expected, str) or not expected:
        return [_fail("capture-run-id-binding", "capture manifest planned_run.run_id must be a non-empty string")]
    try:
        observed, missing = primary_capture_run_ids(bundle)
    except (OSError, ReproductionBundleError) as exc:
        return [_fail("capture-run-id-binding", str(exc))]
    if not observed and not missing:
        return []

    unique_ids = sorted(set(observed.values()))
    metadata: dict[str, object] = {
        "expected": expected,
        "actual": unique_ids[0] if len(unique_ids) == 1 else unique_ids,
        "capture_run_ids_by_artifact": observed,
        "missing_capture_run_id": missing,
    }
    failures: list[str] = []
    if missing:
        failures.append("primary captured artifacts must record capture_run_id")
    if len(unique_ids) > 1:
        failures.append("primary captured artifacts must share one capture_run_id")
    if len(unique_ids) == 1 and unique_ids[0] != expected:
        failures.append("primary captured artifact capture_run_id must match manifest planned_run.run_id")
    if failures:
        return [_fail("capture-run-id-binding", "; ".join(failures), metadata=metadata)]
    return [
        _pass(
            "capture-run-id-binding",
            "primary captured artifact capture_run_id matches manifest planned_run.run_id",
            metadata=metadata,
        )
    ]


def _network_control_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> list[CaptureManifestDiffFinding]:
    artifact_class = "network_resource_controls_attestation"
    if not any(entry.required_artifact_class == artifact_class for entry in bundle.entries):
        return []
    try:
        payload = read_artifact_payload(bundle, artifact_class)
    except (OSError, ReproductionBundleError) as exc:
        return [_fail("network-control-binding", str(exc), artifact_class)]

    expected_cap = manifest.planned_run.get("outbound_bandwidth_cap_bps")
    expected_resources_raw = manifest.planned_run.get("mirrored_resources")
    actual_cap = payload.get("outbound_bandwidth_cap_bps")
    actual_resources_raw = payload.get("mirrored_resources")
    expected_resources = _string_set(expected_resources_raw)
    actual_resources = _string_set(actual_resources_raw)
    metadata: dict[str, object] = {
        "expected": {
            "outbound_bandwidth_cap_bps": expected_cap,
            "mirrored_resources": (
                sorted(expected_resources) if expected_resources is not None else expected_resources_raw
            ),
        },
        "actual": {
            "outbound_bandwidth_cap_bps": actual_cap,
            "mirrored_resources": sorted(actual_resources) if actual_resources is not None else actual_resources_raw,
        },
        "missing": [],
        "extra": [],
    }
    failures: list[str] = []
    if not isinstance(expected_cap, int) or expected_cap <= 0:
        failures.append("capture manifest planned_run.outbound_bandwidth_cap_bps must be positive")
    elif actual_cap != expected_cap:
        failures.append("network controls outbound_bandwidth_cap_bps must match manifest planned_run")
    if expected_resources is None:
        failures.append("capture manifest planned_run.mirrored_resources must be a list of strings")
    elif actual_resources is None:
        failures.append("network controls mirrored_resources must be a list of strings")
    else:
        missing = sorted(expected_resources - actual_resources)
        extra = sorted(actual_resources - expected_resources)
        metadata["missing"] = missing
        metadata["extra"] = extra
        if missing or extra:
            failures.append("network controls mirrored_resources must match manifest planned_run")

    if failures:
        return [_fail("network-control-binding", "; ".join(failures), artifact_class, metadata=metadata)]
    return [
        _pass(
            "network-control-binding",
            "network controls attestation matches manifest planned_run",
            artifact_class,
            metadata=metadata,
        )
    ]


def _fixed_protocol_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> list[CaptureManifestDiffFinding]:
    artifact_class = "fixed_protocol_config"
    planned = next((entry for entry in manifest.entries if entry.required_artifact_class == artifact_class), None)
    if planned is None or not any(entry.required_artifact_class == artifact_class for entry in bundle.entries):
        return []
    try:
        planned_hash = _protocol_core_hash(planned.planned_artifact)
        actual_hash = _protocol_core_hash(read_artifact_payload(bundle, artifact_class))
    except (OSError, ReproductionBundleError, ValueError) as exc:
        return [_fail("fixed-protocol-binding", str(exc), artifact_class)]

    metadata: dict[str, object] = {"expected": planned_hash, "actual": actual_hash}
    if planned_hash != actual_hash:
        return [
            _fail(
                "fixed-protocol-binding",
                "fixed protocol config core fields differ from manifest planned artifact",
                artifact_class,
                metadata=metadata,
            )
        ]
    return [
        _pass(
            "fixed-protocol-binding",
            "fixed protocol config core fields match manifest planned artifact",
            artifact_class,
            metadata=metadata,
        )
    ]


def _protocol_core_hash(payload: Mapping[str, object]) -> str:
    models = payload.get("models")
    if not isinstance(models, list) or not all(isinstance(item, str) and item for item in models):
        raise ValueError("fixed protocol config models must be a list of strings")
    decoding_budget = payload.get("decoding_budget")
    if not isinstance(decoding_budget, dict):
        raise ValueError("fixed protocol config decoding_budget must be an object")
    core = {
        "benchmark_protocol": _non_empty_str(payload, "benchmark_protocol", "fixed protocol config"),
        "models": sorted(_normal_model_backends(tuple(models))),
        "evaluator": _non_empty_str(payload, "evaluator", "fixed protocol config"),
        "tool_set": _non_empty_str(payload, "tool_set", "fixed protocol config"),
        "decoding_budget": decoding_budget,
        "self_harness_rounds": _positive_int(payload, "self_harness_rounds", "fixed protocol config"),
        "proposal_width": _positive_int(payload, "proposal_width", "fixed protocol config"),
        "fixed_across_variants": _required_bool(payload, "fixed_across_variants", "fixed protocol config"),
    }
    return sha256((stable_json_dumps(core) + "\n").encode("utf-8")).hexdigest()


def _proposer_context_evidence_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> list[CaptureManifestDiffFinding]:
    artifact_class = "proposer_context_manifest"
    planned_split = next(
        (entry for entry in manifest.entries if entry.required_artifact_class == "live_terminal_bench_split_manifest"),
        None,
    )
    planned_context = next(
        (entry for entry in manifest.entries if entry.required_artifact_class == artifact_class),
        None,
    )
    actual_present = any(entry.required_artifact_class == artifact_class for entry in bundle.entries)
    if planned_split is None or not actual_present:
        return []
    try:
        planned_held_in_task_ids = _string_list_set(
            planned_split.planned_artifact,
            "held_in_task_ids",
            label="capture manifest live Terminal-Bench split planned artifact",
        )
        context = read_artifact_payload(bundle, artifact_class)
        rounds = _object_list(context, "rounds", label="proposer context manifest")
        planned_category_rounds = (
            _proposer_context_failure_category_summary(
                planned_context.planned_artifact,
                label="capture manifest proposer context planned artifact",
            )
            if planned_context is not None
            else []
        )
        actual_category_rounds = _proposer_context_failure_category_summary(
            context,
            label="proposer context manifest",
        )
        round_metadata: list[dict[str, object]] = []
        coverage_violations: list[dict[str, object]] = []
        for row in rounds:
            round_index = _nonnegative_int(row, "round_index", "proposer context manifest")
            failure_task_ids = _context_task_ids(
                row,
                block_name="held_in_failure_patterns",
                list_key="patterns",
                label=f"proposer context manifest rounds[{round_index}].held_in_failure_patterns",
            )
            passing_task_ids = _context_task_ids(
                row,
                block_name="passing_behavior_summaries",
                list_key="summaries",
                label=f"proposer context manifest rounds[{round_index}].passing_behavior_summaries",
            )
            combined = failure_task_ids | passing_task_ids
            missing = sorted(planned_held_in_task_ids - combined)
            extra = sorted(combined - planned_held_in_task_ids)
            round_record = {
                "round_index": round_index,
                "failure_task_ids": sorted(failure_task_ids),
                "passing_task_ids": sorted(passing_task_ids),
                "combined_task_ids": sorted(combined),
                "missing_task_ids": missing,
                "extra_task_ids": extra,
            }
            round_metadata.append(round_record)
            if missing or extra:
                coverage_violations.append(round_record)
        category_violations = _proposer_context_failure_category_drifts(
            planned_category_rounds,
            actual_category_rounds,
        )
        causal_status_violations = _proposer_context_causal_status_drifts(
            planned_category_rounds,
            actual_category_rounds,
        )
        shared_symptoms_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="failure_pattern_shared_symptoms_sha256s",
            label="shared symptom hashes",
        )
        verifier_evidence_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="failure_pattern_verifier_evidence_sha256s",
            label="verifier evidence hashes",
        )
        presentation_order_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="failure_pattern_presentation_orders",
            label="presentation orders",
        )
        actionability_hint_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="failure_pattern_actionability_hint_sha256s",
            label="actionability hint hashes",
        )
        task_overlap_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="failure_pattern_task_overlap_count",
            label="task overlap counts",
        )
        editable_surface_duplicate_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="editable_surface_duplicate_count",
            label="editable surface duplicate counts",
        )
        previous_edit_duplicate_violations = _proposer_context_pattern_hash_drifts(
            planned_category_rounds,
            actual_category_rounds,
            field="previous_attempted_edit_signature_duplicate_count",
            label="previous attempted edit duplicate counts",
        )
    except (OSError, ReproductionBundleError, ValueError) as exc:
        return [_fail("proposer-context-evidence-derivation", str(exc), artifact_class)]

    metadata: dict[str, object] = {
        "planned_held_in_task_ids": sorted(planned_held_in_task_ids),
        "rounds": round_metadata,
        "coverage_violations": coverage_violations,
        "planned_failure_category_rounds": planned_category_rounds,
        "actual_failure_category_rounds": actual_category_rounds,
        "failure_category_violations": category_violations,
        "causal_status_violations": causal_status_violations,
        "shared_symptoms_violations": shared_symptoms_violations,
        "verifier_evidence_violations": verifier_evidence_violations,
        "presentation_order_violations": presentation_order_violations,
        "actionability_hint_violations": actionability_hint_violations,
        "task_overlap_violations": task_overlap_violations,
        "editable_surface_duplicate_violations": editable_surface_duplicate_violations,
        "previous_edit_duplicate_violations": previous_edit_duplicate_violations,
    }
    evidence_hash_violations = shared_symptoms_violations or verifier_evidence_violations
    ordering_violations = presentation_order_violations or actionability_hint_violations
    if (
        coverage_violations
        or category_violations
        or causal_status_violations
        or evidence_hash_violations
        or ordering_violations
        or task_overlap_violations
        or editable_surface_duplicate_violations
        or previous_edit_duplicate_violations
    ):
        detail = "realized proposer context task ids must cover exactly the planned held-in task set"
        if task_overlap_violations:
            detail += " and failure-pattern task ids must be disjoint within each round"
        if editable_surface_duplicate_violations:
            detail += " and editable surfaces must be pairwise distinct within each round"
        if previous_edit_duplicate_violations:
            detail += " and previous attempted edits must be pairwise distinct within each round"
        if category_violations:
            detail += " and failure categories must match the planned context"
        if causal_status_violations:
            detail += " and causal status hashes must match the planned context"
        if evidence_hash_violations:
            detail += " and failure-pattern evidence hashes must match the planned context"
        if ordering_violations:
            detail += " and failure-pattern ordering evidence must match the planned context"
        return [
            _fail(
                "proposer-context-evidence-derivation",
                detail,
                artifact_class,
                metadata=metadata,
            )
        ]
    return [
        _pass(
            "proposer-context-evidence-derivation",
            "realized proposer context task ids, failure categories, causal status hashes, evidence hashes, and "
            "ordering evidence match the planned held-in evidence shape",
            artifact_class,
            metadata=metadata,
        )
    ]


def _proposer_context_failure_category_summary(
    payload: Mapping[str, object],
    *,
    label: str,
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for row in _object_list(payload, "rounds", label=label):
        round_index = _nonnegative_int(row, "round_index", f"{label} round")
        block = _object_field(
            row,
            "held_in_failure_patterns",
            label=f"{label} round {round_index}",
        )
        categories: dict[str, object] = {}
        causal_statuses: dict[str, object] = {}
        shared_symptoms: dict[str, object] = {}
        verifier_evidence: dict[str, object] = {}
        presentation_orders: dict[str, object] = {}
        actionability_hints: dict[str, object] = {}
        editable_surface_sha256s: list[str] = []
        previous_edit_signatures: list[tuple[int, str, str]] = []
        task_ref_count = 0
        task_id_union: set[str] = set()
        editable_surfaces = _object_field(
            row,
            "editable_surfaces",
            label=f"{label} round {round_index}",
        )
        for surface_index, surface in enumerate(
            _object_list(
                editable_surfaces,
                "surfaces",
                label=f"{label} round {round_index}.editable_surfaces",
            )
        ):
            sha256_value = surface.get("sha256")
            if not _is_sha256(sha256_value):
                raise ValueError(
                    f"{label} round {round_index}.editable_surfaces.surfaces[{surface_index}] "
                    "sha256 must be 64 lowercase hex"
                )
            editable_surface_sha256s.append(str(sha256_value))
        for pattern_index, pattern in enumerate(
            _object_list(
                block,
                "patterns",
                label=f"{label} round {round_index}.held_in_failure_patterns",
            )
        ):
            cluster_id = _non_empty_str(
                pattern,
                "cluster_id",
                f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}]",
            )
            category = pattern.get("failure_category")
            if category is not None and not isinstance(category, str):
                raise ValueError(
                    f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}] "
                    "failure_category must be a string or null"
                )
            causal_status_sha256 = pattern.get("causal_status_sha256")
            if causal_status_sha256 is not None and not _is_sha256(causal_status_sha256):
                raise ValueError(
                    f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}] "
                    "causal_status_sha256 must be 64 lowercase hex or null"
                )
            shared_symptoms_sha256 = pattern.get("shared_symptoms_sha256")
            if shared_symptoms_sha256 is not None and not _is_sha256(shared_symptoms_sha256):
                raise ValueError(
                    f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}] "
                    "shared_symptoms_sha256 must be 64 lowercase hex or null"
                )
            verifier_evidence_sha256 = pattern.get("verifier_evidence_sha256")
            if verifier_evidence_sha256 is not None and not _is_sha256(verifier_evidence_sha256):
                raise ValueError(
                    f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}] "
                    "verifier_evidence_sha256 must be 64 lowercase hex or null"
                )
            presentation_order = pattern.get("presentation_order")
            if presentation_order is not None and (
                not isinstance(presentation_order, int)
                or isinstance(presentation_order, bool)
                or presentation_order < 0
            ):
                raise ValueError(
                    f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}] "
                    "presentation_order must be a non-negative integer or null"
                )
            actionability_hint_sha256 = pattern.get("actionability_hint_sha256")
            if actionability_hint_sha256 is not None and not _is_sha256(actionability_hint_sha256):
                raise ValueError(
                    f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}] "
                    "actionability_hint_sha256 must be 64 lowercase hex or null"
                )
            task_ids = _string_list(
                pattern,
                "task_ids",
                label=f"{label} round {round_index}.held_in_failure_patterns.patterns[{pattern_index}]",
            )
            task_ref_count += len(task_ids)
            task_id_union.update(task_ids)
            categories[cluster_id] = category
            causal_statuses[cluster_id] = causal_status_sha256
            shared_symptoms[cluster_id] = shared_symptoms_sha256
            verifier_evidence[cluster_id] = verifier_evidence_sha256
            presentation_orders[cluster_id] = presentation_order
            actionability_hints[cluster_id] = actionability_hint_sha256
        previous_attempted_edits = _object_field(
            row,
            "previous_attempted_edits",
            label=f"{label} round {round_index}",
        )
        for edit_index, edit in enumerate(
            _object_list(
                previous_attempted_edits,
                "edits",
                label=f"{label} round {round_index}.previous_attempted_edits",
            )
        ):
            proposal_round_index = _nonnegative_int(
                edit,
                "proposal_round_index",
                f"{label} round {round_index}.previous_attempted_edits.edits[{edit_index}]",
            )
            targeted_mechanism_sha256 = edit.get("targeted_mechanism_sha256")
            if not _is_sha256(targeted_mechanism_sha256):
                raise ValueError(
                    f"{label} round {round_index}.previous_attempted_edits.edits[{edit_index}] "
                    "targeted_mechanism_sha256 must be 64 lowercase hex"
                )
            edited_surface_sha256 = edit.get("edited_surface_sha256")
            if not _is_sha256(edited_surface_sha256):
                raise ValueError(
                    f"{label} round {round_index}.previous_attempted_edits.edits[{edit_index}] "
                    "edited_surface_sha256 must be 64 lowercase hex"
                )
            previous_edit_signatures.append(
                (
                    proposal_round_index,
                    str(targeted_mechanism_sha256),
                    str(edited_surface_sha256),
                )
            )
        summaries.append(
            {
                "round_index": round_index,
                "failure_pattern_categories": {
                    cluster_id: categories[cluster_id] for cluster_id in sorted(categories)
                },
                "failure_pattern_causal_status_sha256s": {
                    cluster_id: causal_statuses[cluster_id] for cluster_id in sorted(causal_statuses)
                },
                "failure_pattern_shared_symptoms_sha256s": {
                    cluster_id: shared_symptoms[cluster_id] for cluster_id in sorted(shared_symptoms)
                },
                "failure_pattern_verifier_evidence_sha256s": {
                    cluster_id: verifier_evidence[cluster_id] for cluster_id in sorted(verifier_evidence)
                },
                "failure_pattern_presentation_orders": {
                    cluster_id: presentation_orders[cluster_id] for cluster_id in sorted(presentation_orders)
                },
                "failure_pattern_actionability_hint_sha256s": {
                    cluster_id: actionability_hints[cluster_id] for cluster_id in sorted(actionability_hints)
                },
                "failure_pattern_task_overlap_count": task_ref_count - len(task_id_union),
                "editable_surface_duplicate_count": len(editable_surface_sha256s)
                - len(set(editable_surface_sha256s)),
                "previous_attempted_edit_signature_duplicate_count": len(previous_edit_signatures)
                - len(set(previous_edit_signatures)),
            }
        )
    return summaries


def _proposer_context_failure_category_drifts(
    planned_rounds: Sequence[Mapping[str, object]],
    actual_rounds: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    planned_by_round = {
        _nonnegative_int(row, "round_index", "planned proposer context failure categories"): row
        for row in planned_rounds
    }
    actual_by_round = {
        _nonnegative_int(row, "round_index", "actual proposer context failure categories"): row
        for row in actual_rounds
    }
    violations: list[dict[str, object]] = []
    for round_index in sorted(set(planned_by_round) | set(actual_by_round)):
        planned = planned_by_round.get(round_index)
        actual = actual_by_round.get(round_index)
        expected = planned.get("failure_pattern_categories") if planned is not None else None
        observed = actual.get("failure_pattern_categories") if actual is not None else None
        if expected != observed:
            violations.append(
                {
                    "round_index": round_index,
                    "expected": expected,
                    "actual": observed,
                }
            )
    return violations


def _proposer_context_causal_status_drifts(
    planned_rounds: Sequence[Mapping[str, object]],
    actual_rounds: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    return _proposer_context_pattern_hash_drifts(
        planned_rounds,
        actual_rounds,
        field="failure_pattern_causal_status_sha256s",
        label="causal statuses",
    )


def _proposer_context_pattern_hash_drifts(
    planned_rounds: Sequence[Mapping[str, object]],
    actual_rounds: Sequence[Mapping[str, object]],
    *,
    field: str,
    label: str,
) -> list[dict[str, object]]:
    planned_by_round = {
        _nonnegative_int(row, "round_index", f"planned proposer context {label}"): row
        for row in planned_rounds
    }
    actual_by_round = {
        _nonnegative_int(row, "round_index", f"actual proposer context {label}"): row
        for row in actual_rounds
    }
    violations: list[dict[str, object]] = []
    for round_index in sorted(set(planned_by_round) | set(actual_by_round)):
        planned = planned_by_round.get(round_index)
        actual = actual_by_round.get(round_index)
        expected = planned.get(field) if planned is not None else None
        observed = actual.get(field) if actual is not None else None
        if expected != observed:
            violations.append(
                {
                    "round_index": round_index,
                    "expected": expected,
                    "actual": observed,
                }
            )
    return violations


def _proposal_validation_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> list[CaptureManifestDiffFinding]:
    artifact_class = "proposal_validation_manifest"
    planned = next((entry for entry in manifest.entries if entry.required_artifact_class == artifact_class), None)
    actual_present = any(entry.required_artifact_class == artifact_class for entry in bundle.entries)
    if planned is None or not actual_present:
        return []
    try:
        actual = read_artifact_payload(bundle, artifact_class)
        planned_rounds = _proposal_validation_round_summary(
            planned.planned_artifact,
            label="capture manifest proposal validation planned artifact",
        )
        actual_rounds = _proposal_validation_round_summary(actual, label="proposal validation manifest")
    except (OSError, ReproductionBundleError, ValueError) as exc:
        return [_fail("proposal-validation-derivation", str(exc), artifact_class)]

    planned_by_round = {
        _nonnegative_int(row, "round_index", "proposal validation planned summary"): row
        for row in planned_rounds
    }
    actual_by_round = {
        _nonnegative_int(row, "round_index", "proposal validation actual summary"): row
        for row in actual_rounds
    }
    planned_indexes = sorted(planned_by_round)
    actual_indexes = sorted(actual_by_round)
    round_violations: list[dict[str, object]] = []
    for round_index in sorted(set(planned_by_round) & set(actual_by_round)):
        planned_row = planned_by_round[round_index]
        actual_row = actual_by_round[round_index]
        drift: dict[str, object] = {"round_index": round_index}
        for key in (
            "candidate_count",
            "committed_count",
            "decision_counts",
            "validation_failure_category_counts",
            "changed_surfaces_empty_count",
            "single_surface_violation_count",
            "harness_hash_presence_count",
            "harness_after_merged_sha256",
            "multi_commit_merged_hash_violation_count",
            "merged_split_outcomes_present",
            "merged_split_outcomes_digest",
            "task_outcomes_present_count",
            "proposer_round_request_sha256",
            "proposer_round_response_sha256",
            "candidate_changed_surface_names",
            "accepted_merged_surface_sha256s",
        ):
            if planned_row[key] != actual_row[key]:
                drift[key] = {"expected": planned_row[key], "actual": actual_row[key]}
        if planned_row["baseline_task_outcomes_digest"] != actual_row["baseline_task_outcomes_digest"]:
            drift["baseline_task_outcome_digest_drift"] = {
                "expected": planned_row["baseline_task_outcomes_digest"],
                "actual": actual_row["baseline_task_outcomes_digest"],
            }
        candidate_digest_drifts = _candidate_task_outcome_digest_drifts(
            planned_row["candidate_task_outcomes_digests"],
            actual_row["candidate_task_outcomes_digests"],
        )
        if candidate_digest_drifts:
            drift["candidate_task_outcome_digest_drifts"] = candidate_digest_drifts
        if len(drift) > 1:
            round_violations.append(drift)

    metadata: dict[str, object] = {
        "planned_round_indexes": planned_indexes,
        "actual_round_indexes": actual_indexes,
        "missing_rounds": sorted(set(planned_indexes) - set(actual_indexes)),
        "extra_rounds": sorted(set(actual_indexes) - set(planned_indexes)),
        "task_outcomes_digest_version": TASK_OUTCOMES_DIGEST_VERSION,
        "planned_rounds": planned_rounds,
        "actual_rounds": actual_rounds,
        "round_violations": round_violations,
    }
    failures: list[str] = []
    if planned_indexes != actual_indexes:
        failures.append("realized proposal validation rounds must match planned rounds")
    if round_violations:
        failures.append(
            "realized proposal validation candidate, decision, validation-failure-category, "
            "changed-surface names, single-surface counts, harness-hash presence counts, "
            "multi-commit merged-hash values, merged split outcomes, "
            "accepted-surface hashes, proposer traffic hashes, task-outcome presence counts, "
            "and task-outcome content digests must match planned shape"
        )
    if failures:
        return [
            _fail(
                "proposal-validation-derivation",
                "; ".join(failures),
                artifact_class,
                metadata=metadata,
            )
        ]
    return [
        _pass(
            "proposal-validation-derivation",
            "realized proposal validation rounds match planned validation shape with task-outcome digest v2",
            artifact_class,
            metadata=metadata,
        )
    ]


def _proposal_validation_round_summary(
    payload: Mapping[str, object],
    *,
    label: str,
) -> list[dict[str, object]]:
    rounds = _object_list(payload, "rounds", label=label)
    summaries: list[dict[str, object]] = []
    for row in rounds:
        round_index = _nonnegative_int(row, "round_index", f"{label} round")
        candidates = _object_list(row, "candidates", label=f"{label} round {round_index}")
        committed = _string_list_set(row, "committed_proposal_ids", label=f"{label} round {round_index}")
        decision_counts: dict[str, int] = {}
        category_counts = {key: 0 for key in _PROPOSAL_VALIDATION_FAILURE_CATEGORY_KEYS}
        changed_surfaces_empty_count = 0
        single_surface_violation_count = 0
        harness_hash_presence_count = sum(
            1
            for key in ("harness_before_sha256", "harness_after_sha256")
            if row.get(key) is not None
        )
        harness_after_merged_sha256 = row.get("harness_after_merged_sha256")
        merged_split_outcomes = row.get("merged_split_outcomes")
        merged_split_outcomes_digest = _optional_split_outcomes_digest(
            merged_split_outcomes,
            label=f"{label} round {round_index} merged_split_outcomes",
        )
        task_outcomes_present_count = 0
        baseline_split_outcomes = _object_field(
            row,
            "baseline_split_outcomes",
            label=f"{label} round {round_index}",
        )
        baseline_task_outcomes_digest = _optional_task_outcomes_digest(
            baseline_split_outcomes,
            label=f"{label} round {round_index} baseline_split_outcomes",
        )
        candidate_task_outcomes_digests: dict[str, str | None] = {}
        candidate_changed_surface_names: dict[str, list[str]] = {}
        accepted_merged_surface_sha256s: dict[str, list[str]] = {}
        for candidate in candidates:
            decision = _non_empty_str(candidate, "audit_decision", f"{label} candidate")
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
            proposal_id = _non_empty_str(candidate, "proposal_id", f"{label} candidate")
            category = candidate.get("validation_failure_category")
            category_key = "none" if category is None else str(category)
            if category_key not in category_counts:
                raise ValueError(
                    f"{label} candidate validation_failure_category must be one of "
                    f"{', '.join(_PROPOSAL_VALIDATION_FAILURE_CATEGORY_KEYS)}"
                )
            category_counts[category_key] += 1
            changed_surfaces = _string_list(candidate, "changed_surfaces", label=f"{label} candidate")
            if not changed_surfaces:
                changed_surfaces_empty_count += 1
            if category_key != "no_editable_surface" and len(changed_surfaces) != 1:
                single_surface_violation_count += 1
            candidate_changed_surface_names[proposal_id] = sorted(changed_surfaces)
            if decision in {"accepted", "merged"}:
                edited_surface_sha256 = _non_empty_str(
                    candidate,
                    "edited_surface_sha256",
                    f"{label} candidate {proposal_id}",
                )
                accepted_merged_surface_sha256s.setdefault(edited_surface_sha256, []).append(proposal_id)
            split_outcomes = _object_field(candidate, "split_outcomes", label=f"{label} candidate")
            task_outcomes = split_outcomes.get("task_outcomes")
            if isinstance(task_outcomes, list) and task_outcomes:
                task_outcomes_present_count += 1
            candidate_task_outcomes_digests[proposal_id] = _optional_task_outcomes_digest(
                split_outcomes,
                label=f"{label} candidate {proposal_id} split_outcomes",
            )
        multi_commit_merged_hash_violation_count = 0
        if len(committed) >= 2 and harness_hash_presence_count > 0 and harness_after_merged_sha256 is None:
            multi_commit_merged_hash_violation_count += 1
        if len(committed) < 2 and harness_after_merged_sha256 is not None:
            multi_commit_merged_hash_violation_count += 1
        summaries.append(
            {
                "round_index": round_index,
                "proposer_round_request_sha256": row.get("proposer_round_request_sha256"),
                "proposer_round_response_sha256": row.get("proposer_round_response_sha256"),
                "candidate_count": len(candidates),
                "committed_count": len(committed),
                "decision_counts": {key: decision_counts[key] for key in sorted(decision_counts)},
                "validation_failure_category_counts": dict(category_counts),
                "changed_surfaces_empty_count": changed_surfaces_empty_count,
                "single_surface_violation_count": single_surface_violation_count,
                "harness_hash_presence_count": harness_hash_presence_count,
                "harness_after_merged_sha256": harness_after_merged_sha256,
                "multi_commit_merged_hash_violation_count": multi_commit_merged_hash_violation_count,
                "merged_split_outcomes_present": merged_split_outcomes is not None,
                "merged_split_outcomes_digest": merged_split_outcomes_digest,
                "task_outcomes_present_count": task_outcomes_present_count,
                "baseline_task_outcomes_digest": baseline_task_outcomes_digest,
                "candidate_task_outcomes_digests": {
                    key: candidate_task_outcomes_digests[key] for key in sorted(candidate_task_outcomes_digests)
                },
                "candidate_changed_surface_names": {
                    key: candidate_changed_surface_names[key] for key in sorted(candidate_changed_surface_names)
                },
                "accepted_merged_surface_sha256s": {
                    key: sorted(accepted_merged_surface_sha256s[key])
                    for key in sorted(accepted_merged_surface_sha256s)
                },
            }
        )
    return summaries


def _candidate_task_outcome_digest_drifts(
    planned: object,
    actual: object,
) -> list[dict[str, object]]:
    if not isinstance(planned, dict) or not isinstance(actual, dict):
        return [{"proposal_id": "*", "expected": planned, "actual": actual}]
    drifts: list[dict[str, object]] = []
    for proposal_id in sorted(set(planned) | set(actual)):
        expected = planned.get(proposal_id)
        observed = actual.get(proposal_id)
        if expected is None or observed is None:
            continue
        if expected != observed:
            drifts.append({"proposal_id": str(proposal_id), "expected": expected, "actual": observed})
    return drifts


def _optional_split_outcomes_digest(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object when present")
    projection = {
        key: value.get(key)
        for key in (
            "held_in_passed",
            "held_in_total",
            "held_out_passed",
            "held_out_total",
            "evaluation_repeats",
        )
    }
    task_outcomes_digest = _optional_task_outcomes_digest(value, label=label)
    return sha256(
        (
            stable_json_dumps(
                {
                    "projection": projection,
                    "task_outcomes_digest": task_outcomes_digest,
                }
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _optional_task_outcomes_digest(split_outcomes: Mapping[str, object], *, label: str) -> str | None:
    task_outcomes = split_outcomes.get("task_outcomes")
    if task_outcomes is None:
        return None
    if not isinstance(task_outcomes, list) or not task_outcomes:
        raise ValueError(f"{label} task_outcomes must be a non-empty list when present")
    if not all(isinstance(row, dict) for row in task_outcomes):
        raise ValueError(f"{label} task_outcomes must be a list of objects")
    return _task_outcomes_digest(tuple(dict(row) for row in task_outcomes), label=label)


def _task_outcomes_digest(rows: Sequence[Mapping[str, object]], *, label: str) -> str:
    normalized: list[tuple[str, str, bool, int | None, int, str | None]] = []
    for index, row in enumerate(rows):
        row_label = f"{label} task_outcomes[{index}]"
        task_id = _non_empty_str(row, "task_id", row_label)
        split = _non_empty_str(row, "split", row_label)
        passed = _required_bool(row, "pass", row_label)
        failure_category_raw = row.get("failure_category")
        if failure_category_raw is None:
            failure_category: str | None = None
        elif isinstance(failure_category_raw, str) and failure_category_raw:
            failure_category = failure_category_raw
        else:
            raise ValueError(f"{row_label} failure_category must be a non-empty string or null")
        attempt_index_raw = row.get("attempt_index")
        if attempt_index_raw is None:
            attempt_index: int | None = None
            attempt_sort = -1
        elif isinstance(attempt_index_raw, int) and attempt_index_raw >= 0:
            attempt_index = attempt_index_raw
            attempt_sort = attempt_index_raw
        else:
            raise ValueError(f"{row_label} attempt_index must be a non-negative integer or null")
        normalized.append((task_id, split, passed, attempt_index, attempt_sort, failure_category))
    normalized_rows = [
        {
            "task_id": task_id,
            "split": split,
            "pass": passed,
            "attempt_index": attempt_index,
            "failure_category": failure_category,
        }
        for task_id, split, passed, attempt_index, _attempt_sort, failure_category in sorted(
            normalized,
            key=lambda item: (item[0], item[1], item[4], item[2]),
        )
    ]
    return sha256((stable_json_dumps({"outcomes": normalized_rows}) + "\n").encode("utf-8")).hexdigest()


def _audit_image_findings(
    manifest: CaptureManifest,
    bundle: ReproductionBundle,
) -> list[CaptureManifestDiffFinding]:
    artifact_class = "live_harbor_audit"
    planned = next((entry for entry in manifest.entries if entry.required_artifact_class == artifact_class), None)
    actual_present = any(entry.required_artifact_class == artifact_class for entry in bundle.entries)
    if planned is None or not actual_present:
        return []
    try:
        planned_digests = _audit_image_digests(
            planned.planned_artifact,
            label="capture manifest live Harbor audit planned artifact",
        )
        actual_digests = _audit_image_digests(
            read_artifact_payload(bundle, artifact_class),
            label="live Harbor audit",
        )
    except (OSError, ReproductionBundleError, ValueError) as exc:
        return [_fail("audit-image-binding", str(exc), artifact_class)]

    if not planned_digests and not actual_digests:
        return []

    metadata: dict[str, object] = {
        "expected": sorted(planned_digests),
        "actual": sorted(actual_digests),
        "trust_image_digests": [],
        "missing_from_trust": [],
        "extra_in_trust": [],
    }
    failures: list[str] = []
    if not planned_digests or not actual_digests:
        failures.append("planned and realized live Harbor audit image_digest sets must both be present or both absent")
    elif planned_digests != actual_digests:
        failures.append("live Harbor audit image_digest values must match manifest planned artifact")

    if actual_digests:
        try:
            trust = read_artifact_payload(bundle, "container_image_trust_report")
            trust_binding = _trust_image_digest_binding(trust)
        except (OSError, ReproductionBundleError, ValueError) as exc:
            return [_fail("audit-image-binding", str(exc), artifact_class, metadata=metadata)]
        metadata["trust_manifest_digests"] = sorted(trust_binding.manifest_digests)
        if trust_binding.mixed_child_digest_declarations:
            metadata["mixed_child_digest_declarations"] = trust_binding.mixed_child_digest_declarations
            failures.append("container image trust report child_digests must be declared for every image or none")
        elif trust_binding.child_digests:
            missing_from_trust = sorted(actual_digests - trust_binding.child_digests)
            extra_in_trust = sorted(trust_binding.child_digests - actual_digests)
            metadata["trust_image_binding_mode"] = "child-digests"
            metadata["trust_child_digests"] = sorted(trust_binding.child_digests)
            metadata["trust_child_digest_map"] = list(trust_binding.child_digest_map)
            metadata["missing_from_trust_children"] = missing_from_trust
            metadata["extra_in_trust_children"] = extra_in_trust
            if missing_from_trust:
                failures.append(
                    "realized live Harbor audit image_digest values must exist in container image trust child_digests"
                )
        else:
            trust_digests = trust_binding.manifest_digests
            missing_from_trust = sorted(actual_digests - trust_digests)
            extra_in_trust = sorted(trust_digests - actual_digests)
            metadata["trust_image_binding_mode"] = "manifest-digests"
            metadata["trust_image_digests"] = sorted(trust_digests)
            metadata["missing_from_trust"] = missing_from_trust
            metadata["extra_in_trust"] = extra_in_trust
            if missing_from_trust:
                failures.append(
                    "realized live Harbor audit image_digest values must exist in container image trust report"
                )
            if extra_in_trust:
                failures.append(
                    "container image trust report digests must match realized live Harbor audit image_digest values"
                )

    if failures:
        return [_fail("audit-image-binding", "; ".join(failures), artifact_class, metadata=metadata)]
    return [
        _pass(
            "audit-image-binding",
            "planned and realized live Harbor audit image digests match trusted container images",
            artifact_class,
            metadata=metadata,
        )
    ]


def _audit_image_digests(payload: Mapping[str, object], *, label: str) -> frozenset[str]:
    rows = payload.get("trial_artifacts")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{label} trial_artifacts must be a list of objects")
    digests: set[str] = set()
    for index, row in enumerate(rows):
        value = row.get("image_digest")
        if value is None:
            continue
        digests.add(_image_digest_value(value, label=f"{label} trial_artifacts[{index}].image_digest"))
    return frozenset(digests)


def _trust_image_digests(payload: Mapping[str, object]) -> frozenset[str]:
    return _trust_image_digest_binding(payload).manifest_digests


def _trust_image_digest_binding(payload: Mapping[str, object]) -> _TrustImageDigestBinding:
    images = payload.get("images")
    if not isinstance(images, list) or not all(isinstance(row, dict) for row in images):
        raise ValueError("container image trust report images must be a list of objects")
    manifest_digests: set[str] = set()
    child_digests: set[str] = set()
    child_digest_map: list[dict[str, object]] = []
    with_children: list[str] = []
    without_children: list[str] = []
    for index, row in enumerate(images):
        name = _non_empty_object_str(row, "name", f"container image trust report images[{index}]")
        manifest_digest = _image_digest_value(
            row.get("digest"),
            label=f"container image trust report images[{index}].digest",
        )
        manifest_digests.add(manifest_digest)
        if "child_digests" not in row:
            without_children.append(name)
            continue
        child_values = row.get("child_digests")
        if not isinstance(child_values, list) or not child_values:
            raise ValueError(f"container image trust report images[{index}].child_digests must be a non-empty list")
        children = tuple(
            _image_digest_value(
                value,
                label=f"container image trust report images[{index}].child_digests[{child_index}]",
            )
            for child_index, value in enumerate(child_values)
        )
        if len(set(children)) != len(children):
            raise ValueError(f"container image trust report images[{index}].child_digests must not contain duplicates")
        with_children.append(name)
        child_digests.update(children)
        child_digest_map.append(
            {
                "name": name,
                "manifest_digest": manifest_digest,
                "child_digests": sorted(children),
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


def _non_empty_object_str(data: Mapping[str, object], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} {key} must be a non-empty string")
    return value


def _image_digest_value(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    digest = value.removeprefix(prefix)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _string_set(value: object) -> set[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        return None
    return set(value)


def _string_list_set(data: Mapping[str, object], key: str, *, label: str) -> set[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} {key} must be a list of non-empty strings")
    result = set(value)
    if len(result) != len(value):
        raise ValueError(f"{label} {key} must not contain duplicates")
    return result


def _string_list(data: Mapping[str, object], key: str, *, label: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} {key} must be a list of strings")
    if any(not item for item in value):
        raise ValueError(f"{label} {key} must not contain empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} {key} must not contain duplicates")
    return tuple(value)


def _object_field(data: Mapping[str, object], key: str, *, label: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{label} {key} must be an object")
    return dict(value)


def _object_list(data: Mapping[str, object], key: str, *, label: str) -> tuple[dict[str, object], ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} {key} must be a list of objects")
    return tuple(dict(item) for item in value)


def _context_task_ids(
    row: Mapping[str, object],
    *,
    block_name: str,
    list_key: str,
    label: str,
) -> set[str]:
    block = row.get(block_name)
    if not isinstance(block, dict):
        raise ValueError(f"{label} must be an object")
    task_ids: set[str] = set()
    for index, item in enumerate(_object_list(block, list_key, label=label)):
        task_ids.update(_string_list_set(item, "task_ids", label=f"{label}.{list_key}[{index}]"))
    return task_ids


def _non_empty_str(data: Mapping[str, object], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} {key} must be a non-empty string")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _required_bool(data: Mapping[str, object], key: str, label: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{label} {key} must be a boolean")
    return value


def _nonnegative_int(data: Mapping[str, object], key: str, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} {key} must be a non-negative integer")
    return value


def _positive_int(data: Mapping[str, object], key: str, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} {key} must be a positive integer")
    return value


def _custody_findings(
    manifest: CaptureManifest,
    bundle_signature_path: Path | None,
) -> list[CaptureManifestDiffFinding]:
    if bundle_signature_path is None:
        return [_fail("custody-drift", "bundle signature sidecar was not supplied")]
    try:
        signature = load_capture_manifest_signature(bundle_signature_path)
    except (OSError, CaptureManifestError) as exc:
        return [_fail("custody-drift", str(exc), path=bundle_signature_path)]
    custody = manifest.signing_custody
    findings: list[CaptureManifestDiffFinding] = []
    if signature.get("provider") == custody["provider"]:
        findings.append(_pass("signing-provider", "bundle signing provider matches", path=bundle_signature_path))
    else:
        findings.append(
            _fail(
                "custody-drift",
                "bundle signing provider drift",
                path=bundle_signature_path,
                metadata={"expected": custody["provider"], "actual": signature.get("provider")},
            )
        )
    expected_key_id = custody.get("key_id")
    if expected_key_id is not None and signature.get("key_id") != expected_key_id:
        findings.append(
            _fail(
                "custody-drift",
                "bundle signing key_id drift",
                path=bundle_signature_path,
                metadata={"expected": expected_key_id, "actual": signature.get("key_id")},
            )
        )
    expected_fingerprint = custody.get("fingerprint")
    if expected_fingerprint is not None and signature.get("fingerprint") != expected_fingerprint:
        findings.append(
            _fail(
                "custody-drift",
                "bundle signing fingerprint drift",
                path=bundle_signature_path,
                metadata={"expected": expected_fingerprint, "actual": signature.get("fingerprint")},
            )
        )
    return findings


def _finding_to_jsonable(finding: CaptureManifestDiffFinding) -> dict[str, object]:
    return {
        "category": finding.category,
        "status": finding.status,
        "detail": finding.detail,
        "artifact_class": finding.artifact_class,
        "metadata": finding.metadata,
    }


def _pass(
    category: str,
    detail: str,
    artifact_class: str | None = None,
    *,
    path: Path | None = None,
    metadata: dict[str, object] | None = None,
) -> CaptureManifestDiffFinding:
    return _finding(category, "pass", detail, artifact_class, path=path, metadata=metadata)


def _fail(
    category: str,
    detail: str,
    artifact_class: str | None = None,
    *,
    path: Path | None = None,
    metadata: dict[str, object] | None = None,
) -> CaptureManifestDiffFinding:
    return _finding(category, "fail", detail, artifact_class, path=path, metadata=metadata)


def _advisory(
    category: str,
    detail: str,
    artifact_class: str | None = None,
    *,
    metadata: dict[str, object] | None = None,
) -> CaptureManifestDiffFinding:
    return _finding(category, "advisory", detail, artifact_class, metadata=metadata)


def _finding(
    category: str,
    status: str,
    detail: str,
    artifact_class: str | None,
    *,
    path: Path | None = None,
    metadata: dict[str, object] | None = None,
) -> CaptureManifestDiffFinding:
    payload = dict(metadata) if metadata is not None else {}
    if path is not None:
        payload["path"] = str(path)
    return CaptureManifestDiffFinding(
        category=category,
        status=status,
        detail=detail,
        artifact_class=artifact_class,
        metadata=payload or None,
    )
