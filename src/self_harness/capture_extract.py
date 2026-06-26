from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from self_harness._artifact_shapes import (
    PAPER_MODEL_BACKENDS,
    PAPER_MODEL_NAMES_BY_BACKEND,
    _normal_model_backends,
    artifact_shape_error,
    artifact_shape_error_from_payload,
)
from self_harness.adapters.terminal_bench.harbor_artifacts import discover_trials
from self_harness.audit import AuditRound, load_audit_run
from self_harness.exceptions import AuditCorruptError
from self_harness.image_policy import ImagePolicy, evaluate_image_policy, load_image_policy
from self_harness.types import FailureCategory, stable_json_dumps

CAPTURE_EXTRACT_BOUNDARY = (
    "offline post-capture live-evidence extraction only; transforms operator-supplied captured "
    "files into required reproduction artifact-class JSON shapes, does not execute tasks, invoke "
    "models, contact Harbor, Docker, registries, scanners, PyPI, Sigstore, model providers, or "
    "cloud providers, and never claims benchmark reproduction"
)
EXTRACTABLE_ARTIFACT_CLASSES = frozenset(
    {
        "live_terminal_bench_split_manifest",
        "live_harbor_preflight_report",
        "container_image_trust_report",
        "fixed_protocol_config",
        "model_backend_preflight_report",
        "proposer_llm_request_log",
        "proposer_context_manifest",
        "proposal_validation_manifest",
        "network_resource_controls_attestation",
        "live_harbor_audit",
        "live_two_repeat_evaluation_report",
    }
)

_HARBOR_DISCOVERY_FIELDS = frozenset(
    {"schema_version", "ok", "mode", "source", "request", "discovered_images", "reason", "reproduction_claimed"}
)
_HARBOR_IMAGE_FIELDS = frozenset({"image", "digest", "reference", "tags", "media_type", "child_digests"})
_MODEL_PREFLIGHT_FIELDS = frozenset(
    {
        "schema_version",
        "ok",
        "mode",
        "backends",
        "checks",
        "report_hash",
        "reproduction_claimed",
        "boundary",
        "evaluated_at",
    }
)
_MODEL_CHECK_FIELDS = frozenset({"name", "backend", "status", "detail", "required", "metadata"})
_NETWORK_CONTROL_FIELDS = frozenset(
    {
        "schema_version",
        "mode",
        "outbound_bandwidth_cap_bps",
        "mirrored_resources",
        "capture_run_id",
        "reproduction_claimed",
    }
)
_SPLIT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "mode",
        "source",
        "total_cases",
        "held_in_count",
        "held_out_count",
        "held_in_task_ids",
        "held_out_task_ids",
        "fixed_across_variants",
        "capture_run_id",
        "operator_label",
        "reproduction_claimed",
    }
)
_FIXED_PROTOCOL_FIELDS = frozenset(
    {
        "schema_version",
        "mode",
        "benchmark_protocol",
        "models",
        "evaluator",
        "tool_set",
        "decoding_budget",
        "self_harness_rounds",
        "proposal_width",
        "fixed_across_variants",
        "capture_run_id",
        "operator_label",
        "reproduction_claimed",
    }
)
_CAPTURE_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "mode", "source", "capture_run_id", "operator_label", "reproduction_claimed"}
)
_ATTEMPT_ROW_FIELDS = frozenset({"task_id", "attempt_index", "pass"})
_PROPOSER_REQUEST_LOG_ROW_FIELDS = frozenset(
    {
        "round_index",
        "proposer_client",
        "request_sha256",
        "response_sha256",
        "prompt_tokens",
        "completion_tokens",
        "attempted_proposals",
        "committed_proposals",
        "reproduction_claimed",
    }
)
_PROPOSER_CONTEXT_LOG_ROW_FIELDS = frozenset(
    {
        "round_index",
        "editable_surfaces",
        "held_in_failure_patterns",
        "passing_behavior_summaries",
        "previous_attempted_edits",
        "reproduction_claimed",
    }
)
_TASK_FAILURE_CATEGORIES = frozenset(
    category.value for category in FailureCategory if category is not FailureCategory.VERIFIER_PASS
)


class CaptureExtractError(ValueError):
    """Raised when post-capture extraction inputs are malformed or unsafe."""


def extract_artifact_from_paths(
    artifact_class: str,
    *,
    capture_run_id: str | None = None,
    harbor_discovery_result: Path | None = None,
    harbor_version: str | None = None,
    image_policy: Path | None = None,
    model_backend_preflight_result: Path | None = None,
    network_controls: Path | None = None,
    harbor_run_dir: Path | None = None,
    capture_envelope: Path | None = None,
    attempts_jsonl: Path | None = None,
    split_manifest_result: Path | None = None,
    fixed_protocol_declaration: Path | None = None,
    fixed_protocol_result: Path | None = None,
    fixed_protocol_sha256: str | None = None,
    proposer_request_log: Path | None = None,
    proposer_request_log_artifact: Path | None = None,
    proposer_context_log: Path | None = None,
    audit_run_dir: Path | None = None,
    proposer_backend_map: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if artifact_class == "live_terminal_bench_split_manifest":
        return extract_live_terminal_bench_split_manifest(
            _required_json_path(split_manifest_result, "--split-manifest-result"),
            capture_run_id=capture_run_id,
            harbor_version=_required_str_arg(harbor_version, "--harbor-version"),
        )
    if artifact_class == "live_harbor_preflight_report":
        return extract_live_harbor_preflight_report(
            _required_json_path(harbor_discovery_result, "--harbor-discovery-result"),
            capture_run_id=capture_run_id,
            harbor_version=_required_str_arg(harbor_version, "--harbor-version"),
        )
    if artifact_class == "container_image_trust_report":
        return extract_container_image_trust_report(
            _required_json_path(harbor_discovery_result, "--harbor-discovery-result"),
            capture_run_id=capture_run_id,
            image_policy=load_image_policy(_required_path(image_policy, "--image-policy")),
        )
    if artifact_class == "model_backend_preflight_report":
        return extract_model_backend_preflight_report(
            _required_json_path(model_backend_preflight_result, "--model-backend-preflight-result"),
            capture_run_id=capture_run_id,
        )
    if artifact_class == "proposer_llm_request_log":
        return extract_proposer_llm_request_log(
            _required_json_path(capture_envelope, "--capture-envelope"),
            _read_jsonl_objects(
                _required_path(proposer_request_log, "--proposer-request-log"),
                label="proposer request log",
            ),
            capture_run_id=capture_run_id,
            proposer_backend_map=proposer_backend_map or {},
        )
    if artifact_class == "proposer_context_manifest":
        return extract_proposer_context_manifest(
            _required_json_path(capture_envelope, "--capture-envelope"),
            _read_jsonl_objects(
                _required_path(proposer_context_log, "--proposer-context-log"),
                label="proposer context log",
            ),
            capture_run_id=capture_run_id,
            split_manifest=(
                _read_json_object(split_manifest_result, label="--split-manifest-result")
                if split_manifest_result is not None
                else None
            ),
        )
    if artifact_class == "fixed_protocol_config":
        return extract_fixed_protocol_config(
            _required_json_path(fixed_protocol_declaration, "--fixed-protocol-declaration"),
            capture_run_id=capture_run_id,
        )
    if artifact_class == "proposal_validation_manifest":
        return extract_proposal_validation_manifest(
            _required_path(audit_run_dir, "--audit-run-dir"),
            _required_json_path(capture_envelope, "--capture-envelope"),
            capture_run_id=capture_run_id,
            fixed_protocol_sha256=_fixed_protocol_binding_sha256(fixed_protocol_result, fixed_protocol_sha256),
            proposer_request_log_artifact=proposer_request_log_artifact,
        )
    if artifact_class == "network_resource_controls_attestation":
        return extract_network_resource_controls_attestation(
            _required_json_path(network_controls, "--network-controls"),
            capture_run_id=capture_run_id,
        )
    if artifact_class == "live_harbor_audit":
        return extract_live_harbor_audit(
            _required_path(harbor_run_dir, "--harbor-run-dir"),
            capture_run_id=_required_str_arg(capture_run_id, "--capture-run-id"),
            fixed_protocol_sha256=_fixed_protocol_binding_sha256(fixed_protocol_result, fixed_protocol_sha256),
        )
    if artifact_class == "live_two_repeat_evaluation_report":
        return extract_live_two_repeat_evaluation_report(
            _required_json_path(capture_envelope, "--capture-envelope"),
            _read_jsonl_objects(_required_path(attempts_jsonl, "--attempts-jsonl"), label="attempts JSONL"),
            capture_run_id=capture_run_id,
            fixed_protocol_sha256=_fixed_protocol_binding_sha256(fixed_protocol_result, fixed_protocol_sha256),
        )
    supported = ", ".join(sorted(EXTRACTABLE_ARTIFACT_CLASSES))
    raise CaptureExtractError(f"unsupported extractable artifact class: {artifact_class}; supported: {supported}")


def extract_live_terminal_bench_split_manifest(
    split_result: Mapping[str, Any],
    *,
    capture_run_id: str | None,
    harbor_version: str,
) -> dict[str, object]:
    _reject_reproduction_claims(split_result, label="live split manifest")
    _reject_unknown_fields(split_result, _SPLIT_MANIFEST_FIELDS, label="live Terminal-Bench split manifest")
    if split_result.get("schema_version") != "1.0":
        raise CaptureExtractError("live Terminal-Bench split manifest schema_version must be 1.0")
    if split_result.get("mode") != "live":
        raise CaptureExtractError("live Terminal-Bench split manifest mode must be live")
    if split_result.get("source") != "harbor":
        raise CaptureExtractError("live Terminal-Bench split manifest source must be harbor")
    if split_result.get("fixed_across_variants") is not True:
        raise CaptureExtractError("live Terminal-Bench split manifest fixed_across_variants must be true")
    if split_result.get("total_cases") != 64:
        raise CaptureExtractError("live Terminal-Bench split manifest total_cases must be 64")
    if not harbor_version:
        raise CaptureExtractError("harbor_version must be non-empty")
    held_in = _string_list(split_result, "held_in_task_ids", label="live Terminal-Bench split manifest")
    held_out = _string_list(split_result, "held_out_task_ids", label="live Terminal-Bench split manifest")
    _reject_empty_strings(held_in, label="live Terminal-Bench split manifest held_in_task_ids")
    _reject_empty_strings(held_out, label="live Terminal-Bench split manifest held_out_task_ids")
    _reject_duplicates(held_in, label="live Terminal-Bench split manifest held_in_task_ids")
    _reject_duplicates(held_out, label="live Terminal-Bench split manifest held_out_task_ids")
    overlap = sorted(set(held_in) & set(held_out))
    if overlap:
        raise CaptureExtractError(
            "live Terminal-Bench split manifest held-in and held-out tasks must be disjoint"
        )
    if split_result.get("held_in_count") != len(held_in):
        raise CaptureExtractError("live Terminal-Bench split manifest held_in_count must match held_in_task_ids")
    if split_result.get("held_out_count") != len(held_out):
        raise CaptureExtractError("live Terminal-Bench split manifest held_out_count must match held_out_task_ids")
    if len(held_in) + len(held_out) != 64:
        raise CaptureExtractError("live Terminal-Bench split manifest split counts must total 64")
    resolved_capture_run_id = _capture_run_id(
        split_result,
        capture_run_id,
        label="live Terminal-Bench split manifest",
    )
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "mode": "live",
        "source": "harbor",
        "capture_run_id": resolved_capture_run_id,
        "total_cases": 64,
        "held_in_count": len(held_in),
        "held_out_count": len(held_out),
        "held_in_task_ids": held_in,
        "held_out_task_ids": held_out,
        "fixed_across_variants": True,
        "harbor_version": harbor_version,
        "reproduction_claimed": False,
        "boundary": CAPTURE_EXTRACT_BOUNDARY,
    }
    if split_result.get("operator_label") is not None:
        payload["operator_label"] = _required_str(
            split_result, "operator_label", label="live Terminal-Bench split manifest"
        )
    return _validated("live_terminal_bench_split_manifest", payload)


def extract_live_harbor_preflight_report(
    harbor_discovery_result: Mapping[str, Any],
    *,
    capture_run_id: str | None,
    harbor_version: str,
) -> dict[str, object]:
    result = _validated_live_harbor_discovery(harbor_discovery_result)
    if not harbor_version:
        raise CaptureExtractError("harbor_version must be non-empty")
    if not _discovered_images(result):
        raise CaptureExtractError("live Harbor preflight extraction requires at least one discovered image")
    return _validated(
        "live_harbor_preflight_report",
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": _required_str_arg(capture_run_id, "--capture-run-id"),
            "harbor_reachable": True,
            "harbor_version": harbor_version,
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        },
    )


def extract_container_image_trust_report(
    harbor_discovery_result: Mapping[str, Any],
    *,
    capture_run_id: str | None,
    image_policy: ImagePolicy,
) -> dict[str, object]:
    result = _validated_live_harbor_discovery(harbor_discovery_result)
    images = _discovered_images(result)
    if not images:
        raise CaptureExtractError("container image trust extraction requires at least one discovered image")
    rows: list[dict[str, object]] = []
    for index, image in enumerate(images):
        image_name = _required_str(image, "image", label="Harbor discovered image")
        digest = _required_str(image, "digest", label="Harbor discovered image")
        decision = evaluate_image_policy(image_policy, image_name, digest, require_digest=True)
        if not decision.allowed:
            raise CaptureExtractError(decision.message)
        row: dict[str, object] = {"name": image_name, "digest": digest}
        child_digests = _optional_child_digests(image, label=f"Harbor discovered image {index}")
        if child_digests:
            row["child_digests"] = child_digests
        rows.append(row)
    return _validated(
        "container_image_trust_report",
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": _required_str_arg(capture_run_id, "--capture-run-id"),
            "policy": "digest-bound",
            "all_digest_bound": True,
            "images": sorted(rows, key=lambda row: (str(row["name"]), str(row["digest"]))),
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        },
    )


def extract_model_backend_preflight_report(
    report: Mapping[str, Any],
    *,
    capture_run_id: str | None,
) -> dict[str, object]:
    _reject_reproduction_claims(report, label="model backend preflight")
    _reject_unknown_fields(report, _MODEL_PREFLIGHT_FIELDS, label="model backend preflight")
    if report.get("schema_version") != "1.0":
        raise CaptureExtractError("model backend preflight schema_version must be 1.0")
    if report.get("ok") is not True:
        raise CaptureExtractError("model backend preflight ok must be true")
    if report.get("mode") != "live":
        raise CaptureExtractError("model backend preflight mode must be live")
    backends = _string_list(report, "backends", label="model backend preflight")
    checks = _object_list(report, "checks", label="model backend preflight")
    for index, check in enumerate(checks):
        _reject_unknown_fields(check, _MODEL_CHECK_FIELDS, label=f"model backend preflight check {index}")
        if check.get("required") is True and check.get("status") != "pass":
            raise CaptureExtractError(f"model backend preflight required check failed: {check.get('name', index)}")
    payload = {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": _required_str_arg(capture_run_id, "--capture-run-id"),
            "backends": backends,
        "checks": checks,
        "report_hash": _required_str(report, "report_hash", label="model backend preflight"),
        "reproduction_claimed": False,
        "boundary": str(report.get("boundary", CAPTURE_EXTRACT_BOUNDARY)),
    }
    if report.get("evaluated_at") is not None:
        payload["evaluated_at"] = _required_str(report, "evaluated_at", label="model backend preflight")
    return _validated("model_backend_preflight_report", payload)


def extract_proposer_llm_request_log(
    capture_envelope: Mapping[str, Any],
    request_log_rows: Sequence[Mapping[str, Any]],
    *,
    capture_run_id: str | None,
    proposer_backend_map: Mapping[str, str],
) -> dict[str, object]:
    _reject_reproduction_claims(capture_envelope, label="capture envelope")
    _reject_unknown_fields(capture_envelope, _CAPTURE_ENVELOPE_FIELDS, label="capture envelope")
    if capture_envelope.get("schema_version") != "1.0":
        raise CaptureExtractError("capture envelope schema_version must be 1.0")
    if capture_envelope.get("mode") != "live":
        raise CaptureExtractError("capture envelope mode must be live")
    resolved_capture_run_id = _capture_run_id(capture_envelope, capture_run_id, label="capture envelope")
    backend_map = _validated_proposer_backend_map(proposer_backend_map)
    rounds: list[dict[str, object]] = []
    for index, row in enumerate(request_log_rows):
        _reject_reproduction_claims(row, label=f"proposer request log row {index}")
        _reject_unknown_fields(row, _PROPOSER_REQUEST_LOG_ROW_FIELDS, label=f"proposer request log row {index}")
        proposer_client = str(row.get("proposer_client", "primary"))
        if not proposer_client:
            raise CaptureExtractError(f"proposer request log row {index} proposer_client must be non-empty")
        backend = backend_map.get(proposer_client)
        if backend is None:
            raise CaptureExtractError(f"proposer backend map missing client: {proposer_client}")
        rounds.append(
            {
                "round_index": _nonnegative_int(row, "round_index", label=f"proposer request log row {index}"),
                "backend": backend,
                "model": PAPER_MODEL_NAMES_BY_BACKEND[backend],
                "request_sha256": _sha256_field(row, "request_sha256", label=f"proposer request log row {index}"),
                "response_sha256": _sha256_field(row, "response_sha256", label=f"proposer request log row {index}"),
                "prompt_tokens": _nonnegative_int(row, "prompt_tokens", label=f"proposer request log row {index}"),
                "completion_tokens": _nonnegative_int(
                    row,
                    "completion_tokens",
                    label=f"proposer request log row {index}",
                ),
                "attempted_proposals": _nonnegative_int(
                    row,
                    "attempted_proposals",
                    label=f"proposer request log row {index}",
                ),
                "committed_proposals": _nonnegative_int(
                    row,
                    "committed_proposals",
                    label=f"proposer request log row {index}",
                ),
            }
        )
    if not rounds:
        raise CaptureExtractError("proposer request log must contain at least one row")
    rounds = sorted(rounds, key=_round_index_sort_key)
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "capture_run_id": resolved_capture_run_id,
        "round_count": len(rounds),
        "rounds": rounds,
        "reproduction_claimed": False,
        "boundary": CAPTURE_EXTRACT_BOUNDARY,
    }
    return _validated("proposer_llm_request_log", payload)


def extract_proposer_context_manifest(
    capture_envelope: Mapping[str, Any],
    context_rows: Sequence[Mapping[str, Any]],
    *,
    capture_run_id: str | None,
    split_manifest: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    _reject_reproduction_claims(capture_envelope, label="capture envelope")
    _reject_unknown_fields(capture_envelope, _CAPTURE_ENVELOPE_FIELDS, label="capture envelope")
    if capture_envelope.get("schema_version") != "1.0":
        raise CaptureExtractError("capture envelope schema_version must be 1.0")
    if capture_envelope.get("mode") != "live":
        raise CaptureExtractError("capture envelope mode must be live")
    resolved_capture_run_id = _capture_run_id(capture_envelope, capture_run_id, label="capture envelope")
    allowed_task_ids = _split_task_ids(split_manifest) if split_manifest is not None else None
    rounds: list[dict[str, object]] = []
    for index, row in enumerate(context_rows):
        _reject_reproduction_claims(row, label=f"proposer context log row {index}")
        _reject_unknown_fields(row, _PROPOSER_CONTEXT_LOG_ROW_FIELDS, label=f"proposer context log row {index}")
        held_in_failure_patterns = _normalize_failure_pattern_evidence(
            _context_block(
                row,
                "held_in_failure_patterns",
                label=f"proposer context log row {index}",
            ),
            label=f"proposer context log row {index} held_in_failure_patterns",
        )
        previous_attempted_edits = _normalize_previous_edit_causal_statuses(
            _context_block(
                row,
                "previous_attempted_edits",
                label=f"proposer context log row {index}",
            ),
            label=f"proposer context log row {index} previous_attempted_edits",
        )
        passing_behavior_summaries = _context_block(
            row,
            "passing_behavior_summaries",
            label=f"proposer context log row {index}",
        )
        if allowed_task_ids is not None:
            _validate_context_task_ids(
                held_in_failure_patterns,
                list_key="patterns",
                label=f"proposer context log row {index} held_in_failure_patterns",
                allowed_task_ids=allowed_task_ids,
            )
            _validate_context_task_ids(
                passing_behavior_summaries,
                list_key="summaries",
                label=f"proposer context log row {index} passing_behavior_summaries",
                allowed_task_ids=allowed_task_ids,
            )
        rounds.append(
            {
                "round_index": _nonnegative_int(row, "round_index", label=f"proposer context log row {index}"),
                "editable_surfaces": _context_block(
                    row,
                    "editable_surfaces",
                    label=f"proposer context log row {index}",
                ),
                "held_in_failure_patterns": held_in_failure_patterns,
                "passing_behavior_summaries": passing_behavior_summaries,
                "previous_attempted_edits": previous_attempted_edits,
            }
        )
    if not rounds:
        raise CaptureExtractError("proposer context log must contain at least one row")
    rounds = sorted(rounds, key=_round_index_sort_key)
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "capture_run_id": resolved_capture_run_id,
        "round_count": len(rounds),
        "rounds": rounds,
        "reproduction_claimed": False,
        "boundary": CAPTURE_EXTRACT_BOUNDARY,
    }
    return _validated("proposer_context_manifest", payload)


def extract_fixed_protocol_config(
    declaration: Mapping[str, Any],
    *,
    capture_run_id: str | None,
) -> dict[str, object]:
    _reject_reproduction_claims(declaration, label="fixed protocol declaration")
    _reject_unknown_fields(declaration, _FIXED_PROTOCOL_FIELDS, label="fixed protocol declaration")
    if declaration.get("schema_version") != "1.0":
        raise CaptureExtractError("fixed protocol declaration schema_version must be 1.0")
    if declaration.get("mode") != "live":
        raise CaptureExtractError("fixed protocol declaration mode must be live")
    if declaration.get("benchmark_protocol") != "terminal-bench@2.0":
        raise CaptureExtractError("fixed protocol declaration benchmark_protocol must be terminal-bench@2.0")
    models = _string_list(declaration, "models", label="fixed protocol declaration")
    _reject_empty_strings(models, label="fixed protocol declaration models")
    evaluator = _required_str(declaration, "evaluator", label="fixed protocol declaration")
    tool_set = _required_str(declaration, "tool_set", label="fixed protocol declaration")
    decoding_budget = declaration.get("decoding_budget")
    if not isinstance(decoding_budget, dict):
        raise CaptureExtractError("fixed protocol declaration decoding_budget must be an object")
    self_harness_rounds = _positive_int(
        declaration,
        "self_harness_rounds",
        label="fixed protocol declaration",
    )
    proposal_width = _positive_int(
        declaration,
        "proposal_width",
        label="fixed protocol declaration",
    )
    if declaration.get("fixed_across_variants") is not True:
        raise CaptureExtractError("fixed protocol declaration fixed_across_variants must be true")
    resolved_capture_run_id = _capture_run_id(declaration, capture_run_id, label="fixed protocol declaration")
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "mode": "live",
        "benchmark_protocol": "terminal-bench@2.0",
        "capture_run_id": resolved_capture_run_id,
        "models": models,
        "evaluator": evaluator,
        "tool_set": tool_set,
        "decoding_budget": dict(decoding_budget),
        "self_harness_rounds": self_harness_rounds,
        "proposal_width": proposal_width,
        "fixed_across_variants": True,
        "reproduction_claimed": False,
        "boundary": CAPTURE_EXTRACT_BOUNDARY,
    }
    if declaration.get("operator_label") is not None:
        payload["operator_label"] = _required_str(declaration, "operator_label", label="fixed protocol declaration")
    return _validated("fixed_protocol_config", payload)


def extract_proposal_validation_manifest(
    audit_run_dir: Path,
    capture_envelope: Mapping[str, Any],
    *,
    capture_run_id: str | None,
    fixed_protocol_sha256: str,
    proposer_request_log_artifact: Path | None = None,
) -> dict[str, object]:
    _reject_reproduction_claims(capture_envelope, label="capture envelope")
    _reject_unknown_fields(capture_envelope, _CAPTURE_ENVELOPE_FIELDS, label="capture envelope")
    if capture_envelope.get("schema_version") != "1.0":
        raise CaptureExtractError("capture envelope schema_version must be 1.0")
    if capture_envelope.get("mode") != "live":
        raise CaptureExtractError("capture envelope mode must be live")
    resolved_capture_run_id = _capture_run_id(capture_envelope, capture_run_id, label="capture envelope")
    try:
        audit = load_audit_run(audit_run_dir)
    except AuditCorruptError as exc:
        raise CaptureExtractError(f"invalid audit run: {exc}") from exc
    proposer_traffic = _optional_proposer_round_traffic(proposer_request_log_artifact)
    lineage_by_round = _audit_lineage_by_round(audit.lineage)
    rounds = [
        _proposal_validation_round(
            round_,
            lineage_by_round=lineage_by_round,
            proposer_traffic=proposer_traffic,
        )
        for round_ in sorted(audit.rounds, key=lambda row: row.index)
    ]
    if not rounds:
        raise CaptureExtractError("proposal validation manifest extraction requires at least one audit round")
    return _validated(
        "proposal_validation_manifest",
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": resolved_capture_run_id,
            "round_count": len(rounds),
            "rounds": rounds,
            "fixed_protocol_sha256": fixed_protocol_sha256,
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        },
    )


def extract_network_resource_controls_attestation(
    controls: Mapping[str, Any],
    *,
    capture_run_id: str | None,
) -> dict[str, object]:
    _reject_reproduction_claims(controls, label="network resource controls")
    _reject_unknown_fields(controls, _NETWORK_CONTROL_FIELDS, label="network resource controls")
    if controls.get("schema_version") != "1.0":
        raise CaptureExtractError("network resource controls schema_version must be 1.0")
    if controls.get("mode") != "live":
        raise CaptureExtractError("network resource controls mode must be live")
    cap = controls.get("outbound_bandwidth_cap_bps")
    if not isinstance(cap, int) or cap <= 0:
        raise CaptureExtractError("network resource controls outbound_bandwidth_cap_bps must be positive")
    mirrored = _string_list(controls, "mirrored_resources", label="network resource controls")
    if not mirrored or any(not item for item in mirrored):
        raise CaptureExtractError("network resource controls mirrored_resources must be non-empty")
    resolved_capture_run_id = _capture_run_id(controls, capture_run_id, label="network resource controls")
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "capture_run_id": resolved_capture_run_id,
        "outbound_bandwidth_cap_bps": cap,
        "mirrored_resources": mirrored,
        "reproduction_claimed": False,
        "boundary": CAPTURE_EXTRACT_BOUNDARY,
    }
    return _validated("network_resource_controls_attestation", payload)


def extract_live_harbor_audit(
    run_dir: Path,
    *,
    capture_run_id: str,
    fixed_protocol_sha256: str,
) -> dict[str, object]:
    records = discover_trials(run_dir)
    if not records:
        raise CaptureExtractError("live Harbor audit extraction requires at least one captured trial")
    by_task: dict[str, list[dict[str, object]]] = defaultdict(list)
    image_digests_by_task: dict[str, list[str | None]] = defaultdict(list)
    for record in records:
        if record.provenance.validation_status != "candidate":
            missing = ", ".join(record.provenance.missing_required)
            raise CaptureExtractError(f"trial artifact is incomplete for task {record.task_id}: {missing}")
        image_digest = _optional_image_digest(record.image_digest, label=f"trial artifact {record.task_id}")
        image_digests_by_task[record.task_id].append(image_digest)
        by_task[record.task_id].append(
            {
                "attempt_index": record.attempt_index,
                "pass": record.passed,
                "terminal_cause": record.terminal_cause,
            }
        )
    artifacts = []
    for task_id in sorted(by_task):
        attempts = sorted(by_task[task_id], key=_attempt_sort_key)
        if len(attempts) != 2:
            raise CaptureExtractError(f"live Harbor audit task {task_id} must record exactly two attempts")
        attempt_indexes = {row["attempt_index"] for row in attempts}
        if attempt_indexes != {0, 1}:
            raise CaptureExtractError(f"live Harbor audit task {task_id} attempt indexes must be 0 and 1")
        image_digests = image_digests_by_task[task_id]
        present_image_digests = [digest for digest in image_digests if digest is not None]
        if present_image_digests and len(present_image_digests) != len(image_digests):
            raise CaptureExtractError(
                f"live Harbor audit task {task_id} image_digest must be recorded on every attempt when present"
            )
        unique_image_digests = sorted(set(present_image_digests))
        if len(unique_image_digests) > 1:
            raise CaptureExtractError(f"live Harbor audit task {task_id} must use one image_digest")
        artifact: dict[str, object] = {
            "task_id": task_id,
            "captured": True,
            "verifier_outcome": "pass" if all(bool(row["pass"]) for row in attempts) else "fail",
            "attempts": attempts,
        }
        if unique_image_digests:
            artifact["image_digest"] = unique_image_digests[0]
        artifacts.append(artifact)
    return _validated(
        "live_harbor_audit",
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "trial_artifacts": artifacts,
            "fixed_protocol_sha256": fixed_protocol_sha256,
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        },
    )


def extract_live_two_repeat_evaluation_report(
    capture_envelope: Mapping[str, Any],
    attempt_rows: Sequence[Mapping[str, Any]],
    *,
    capture_run_id: str | None,
    fixed_protocol_sha256: str,
) -> dict[str, object]:
    _reject_reproduction_claims(capture_envelope, label="capture envelope")
    _reject_unknown_fields(capture_envelope, _CAPTURE_ENVELOPE_FIELDS, label="capture envelope")
    if capture_envelope.get("schema_version") != "1.0":
        raise CaptureExtractError("capture envelope schema_version must be 1.0")
    if capture_envelope.get("mode") != "live":
        raise CaptureExtractError("capture envelope mode must be live")
    if capture_envelope.get("source") != "harbor":
        raise CaptureExtractError("capture envelope source must be harbor")
    resolved_capture_run_id = _capture_run_id(capture_envelope, capture_run_id, label="capture envelope")
    by_task: dict[str, list[dict[str, bool]]] = defaultdict(list)
    seen_attempts: set[tuple[str, int]] = set()
    for index, row in enumerate(attempt_rows):
        _reject_reproduction_claims(row, label=f"attempt row {index}")
        _reject_unknown_fields(row, _ATTEMPT_ROW_FIELDS, label=f"attempt row {index}")
        task_id = _required_str(row, "task_id", label=f"attempt row {index}")
        attempt_index = row.get("attempt_index")
        if not isinstance(attempt_index, int) or attempt_index < 0:
            raise CaptureExtractError(f"attempt row {index} attempt_index must be a non-negative integer")
        passed = row.get("pass")
        if not isinstance(passed, bool):
            raise CaptureExtractError(f"attempt row {index} pass must be boolean")
        key = (task_id, attempt_index)
        if key in seen_attempts:
            raise CaptureExtractError(f"duplicate attempt row for task {task_id} attempt {attempt_index}")
        seen_attempts.add(key)
        by_task[task_id].append({"pass": passed})
    if not by_task:
        raise CaptureExtractError("two-repeat extraction requires at least one task")
    per_task = []
    for task_id in sorted(by_task):
        attempts = by_task[task_id]
        if len(attempts) != 2:
            raise CaptureExtractError(f"task {task_id} must record exactly two attempts")
        per_task.append({"task_id": task_id, "attempts": attempts})
    task_count = len(per_task)
    attempt_count = task_count * 2
    pass_count = sum(1 for attempts in by_task.values() for attempt in attempts if attempt["pass"] is True)
    return _validated(
        "live_two_repeat_evaluation_report",
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "attempts_per_task": 2,
            "per_task_attempts": per_task,
            "task_count": task_count,
            "attempt_count": attempt_count,
            "pass_count": pass_count,
            "fail_count": attempt_count - pass_count,
            "fixed_protocol_sha256": fixed_protocol_sha256,
            "capture_run_id": resolved_capture_run_id,
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        },
    )


def _validated(artifact_class: str, payload: dict[str, object]) -> dict[str, object]:
    error = artifact_shape_error_from_payload(artifact_class, payload)
    if error is not None:
        raise CaptureExtractError(error)
    return payload


def _optional_proposer_round_traffic(path: Path | None) -> dict[int, tuple[str, str]]:
    if path is None:
        return {}
    data = _read_json_object(path, label="--proposer-request-log-artifact")
    error = artifact_shape_error_from_payload("proposer_llm_request_log", data)
    if error is not None:
        raise CaptureExtractError(error)
    traffic: dict[int, tuple[str, str]] = {}
    for row in _object_list(data, "rounds", label="proposer LLM request log artifact"):
        round_index = _nonnegative_int(row, "round_index", label="proposer LLM request log artifact")
        traffic[round_index] = (
            _sha256_field(row, "request_sha256", label="proposer LLM request log artifact"),
            _sha256_field(row, "response_sha256", label="proposer LLM request log artifact"),
        )
    return traffic


def _proposal_validation_round(
    round_: AuditRound,
    *,
    lineage_by_round: Mapping[int, Mapping[str, Any]],
    proposer_traffic: Mapping[int, tuple[str, str]],
) -> dict[str, object]:
    candidates = [
        _proposal_validation_candidate(round_, row)
        for row in sorted(round_.proposals, key=_proposal_sort_key)
    ]
    if not candidates:
        raise CaptureExtractError(f"audit round {round_.index} must contain at least one proposal")
    committed_proposal_ids = [
        str(candidate["proposal_id"])
        for candidate in candidates
        if candidate["audit_decision"] in {"accepted", "merged"}
    ]
    payload: dict[str, object] = {
        "round_index": round_.index,
        "baseline_split_outcomes": _split_outcomes(round_, "__baseline__", "baseline"),
        "candidates": candidates,
        "committed_proposal_ids": committed_proposal_ids,
        "merge_decision": _merge_decision(candidates),
    }
    lineage_hashes = _optional_lineage_harness_hashes(lineage_by_round, round_.index)
    if lineage_hashes is not None:
        payload["harness_before_sha256"] = lineage_hashes[0]
        payload["harness_after_sha256"] = lineage_hashes[1]
        if len(committed_proposal_ids) >= 2:
            payload["harness_after_merged_sha256"] = lineage_hashes[1]
            payload["merged_split_outcomes"] = _split_outcomes(round_, "__merge__", "candidate")
    if proposer_traffic:
        traffic = proposer_traffic.get(round_.index)
        if traffic is None:
            raise CaptureExtractError(
                f"proposer request log artifact missing round_index {round_.index} for proposal validation"
            )
        payload["proposer_round_request_sha256"] = traffic[0]
        payload["proposer_round_response_sha256"] = traffic[1]
    return payload


def _audit_lineage_by_round(lineage: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    by_round: dict[int, Mapping[str, Any]] = {}
    for row in lineage:
        round_index = row.get("round")
        if isinstance(round_index, int):
            by_round[round_index] = row
    return by_round


def _optional_lineage_harness_hashes(
    lineage_by_round: Mapping[int, Mapping[str, Any]],
    round_index: int,
) -> tuple[str, str] | None:
    row = lineage_by_round.get(round_index)
    if row is None:
        return None
    before_hash = row.get("harness_before_hash")
    after_hash = row.get("harness_after_hash")
    if before_hash is None and after_hash is None:
        return None
    if before_hash is None or after_hash is None:
        raise CaptureExtractError(
            f"audit lineage round {round_index} harness_before_hash and harness_after_hash must be present together"
        )
    if not _is_sha256(before_hash):
        raise CaptureExtractError(f"audit lineage round {round_index} harness_before_hash must be 64 lowercase hex")
    if not _is_sha256(after_hash):
        raise CaptureExtractError(f"audit lineage round {round_index} harness_after_hash must be 64 lowercase hex")
    assert isinstance(before_hash, str)
    assert isinstance(after_hash, str)
    return before_hash, after_hash


def _proposal_validation_candidate(round_: AuditRound, row: Mapping[str, Any]) -> dict[str, object]:
    proposal_id = _required_str(row, "id", label=f"audit round {round_.index} proposal")
    status = _required_str(row, "status", label=f"audit round {round_.index} proposal {proposal_id}")
    changed_surfaces = _proposal_changed_surfaces(
        row,
        proposal_id=proposal_id,
        allow_empty=status == "invalid",
    )
    validation_failure_category = _validation_failure_category(status, changed_surfaces)
    decision_reason = str(row.get("decision_reason") or status)
    rejection_reason = row.get("rejection_reason")
    if status in {"rejected", "superseded", "invalid"} and not rejection_reason:
        rejection_reason = decision_reason
    return {
        "proposal_id": proposal_id,
        "proposal_round_index": _proposal_round_index(row, fallback=round_.index, proposal_id=proposal_id),
        "pattern_id": _required_str(row, "pattern_id", label=f"audit round {round_.index} proposal {proposal_id}"),
        "changed_surfaces": changed_surfaces,
        "edited_surface_sha256": _stable_payload_sha256({"changed_surfaces": changed_surfaces}),
        "targeted_mechanism_sha256": _stable_payload_sha256({"pattern_id": row["pattern_id"]}),
        "summary_sha256": _stable_payload_sha256(
            {
                "rationale": str(row.get("rationale", "")),
                "expected_effect": str(row.get("expected_effect", "")),
                "regression_risks": row.get("regression_risks", []),
            }
        ),
        "split_outcomes": _split_outcomes(round_, proposal_id, "candidate", proposal_row=row),
        "audit_decision": status,
        "validation_failure_category": validation_failure_category,
        "decision_reason": decision_reason,
        "rejection_reason": rejection_reason if isinstance(rejection_reason, str) else None,
    }


def _proposal_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    raw_priority = row.get("priority")
    priority = raw_priority if isinstance(raw_priority, int) else 0
    return priority, str(row.get("id", ""))


def _proposal_round_index(row: Mapping[str, Any], *, fallback: int, proposal_id: str) -> int:
    raw = row.get("round", fallback)
    if not isinstance(raw, int) or raw < 0:
        raise CaptureExtractError(f"audit proposal {proposal_id} round must be a non-negative integer")
    return raw


def _proposal_changed_surfaces(
    row: Mapping[str, Any],
    *,
    proposal_id: str,
    allow_empty: bool = False,
) -> list[str]:
    raw = row.get("changed_surfaces")
    if isinstance(raw, list):
        values = sorted({item for item in raw if isinstance(item, str) and item})
        if values:
            return values
    surface = row.get("surface")
    if isinstance(surface, str) and surface:
        return [surface]
    if allow_empty:
        return []
    raise CaptureExtractError(f"audit proposal {proposal_id} must record at least one changed surface")


def _validation_failure_category(status: str, changed_surfaces: Sequence[str]) -> str | None:
    if status != "invalid":
        return None
    if changed_surfaces:
        return "execution_failure"
    return "no_editable_surface"


def _split_outcomes(
    round_: AuditRound,
    proposal_id: str,
    arm: str,
    *,
    proposal_row: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    totals = [
        row
        for row in round_.evaluations
        if row.get("task_id") == "__split_total__"
        and row.get("proposal_id") == proposal_id
        and row.get("arm") == arm
    ]
    if not totals and proposal_row is not None:
        return _split_outcomes_from_proposal_row(proposal_row)
    by_split = {str(row.get("split")): row for row in totals}
    if "held_in" not in by_split or "held_out" not in by_split:
        raise CaptureExtractError(
            f"audit round {round_.index} missing split totals for proposal_id={proposal_id} arm={arm}"
        )
    held_in = by_split["held_in"]
    held_out = by_split["held_out"]
    payload: dict[str, object] = {
        "held_in_passed": _nonnegative_int(held_in, "verifier_pass", label="audit evaluation split total"),
        "held_in_total": _split_total_count(held_in, label="audit evaluation held-in total"),
        "held_out_passed": _nonnegative_int(held_out, "verifier_pass", label="audit evaluation split total"),
        "held_out_total": _split_total_count(held_out, label="audit evaluation held-out total"),
        "evaluation_repeats": _positive_int(held_in, "evaluation_repeats", label="audit evaluation split total"),
    }
    task_outcomes = _split_task_outcomes(round_, proposal_id, arm)
    if task_outcomes:
        payload["task_outcomes"] = task_outcomes
    return payload


def _split_outcomes_from_proposal_row(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        "held_in_passed": _nonnegative_int(row, "passed_held_in", label="audit proposal"),
        "held_in_total": _nonnegative_int(row, "passed_held_in", label="audit proposal"),
        "held_out_passed": _nonnegative_int(row, "passed_held_out", label="audit proposal"),
        "held_out_total": _nonnegative_int(row, "passed_held_out", label="audit proposal"),
        "evaluation_repeats": _positive_int(row, "evaluation_repeats", label="audit proposal"),
    }


def _split_total_count(row: Mapping[str, Any], *, label: str) -> int:
    passed = _nonnegative_int(row, "verifier_pass", label=label)
    failed = _nonnegative_int(row, "verifier_fail", label=label)
    return passed + failed


def _split_task_outcomes(round_: AuditRound, proposal_id: str, arm: str) -> list[dict[str, object]]:
    rows = [
        row
        for row in round_.evaluations
        if row.get("task_id") != "__split_total__"
        and row.get("proposal_id") == proposal_id
        and row.get("arm") == arm
    ]
    outcomes: list[dict[str, object]] = []
    for row in rows:
        task_id = _required_str(row, "task_id", label="audit evaluation task outcome")
        split = _required_str(row, "split", label=f"audit evaluation task outcome {task_id}")
        if split not in {"held_in", "held_out"}:
            raise CaptureExtractError(f"audit evaluation task outcome {task_id} split must be held_in or held_out")
        passed = _task_outcome_pass(row, label=f"audit evaluation task outcome {task_id}")
        outcome: dict[str, object] = {
            "task_id": task_id,
            "split": split,
            "pass": passed,
        }
        failure_category = _task_outcome_failure_category(
            row,
            passed=passed,
            label=f"audit evaluation task outcome {task_id}",
        )
        if failure_category is not None:
            outcome["failure_category"] = failure_category
        attempt_index = row.get("attempt_index")
        if isinstance(attempt_index, int) and attempt_index >= 0:
            outcome["attempt_index"] = attempt_index
        elif attempt_index is not None:
            raise CaptureExtractError(
                f"audit evaluation task outcome {task_id} attempt_index must be a non-negative integer or null"
            )
        outcomes.append(outcome)
    return sorted(outcomes, key=_task_outcome_sort_key)


def _task_outcome_pass(row: Mapping[str, Any], *, label: str) -> bool:
    raw = row.get("verifier_pass")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in {0, 1}:
        return raw == 1
    raise CaptureExtractError(f"{label} verifier_pass must be boolean or 0/1")


def _task_outcome_failure_category(
    row: Mapping[str, Any],
    *,
    passed: bool,
    label: str,
) -> str | None:
    raw = row.get("failure_category")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise CaptureExtractError(f"{label} failure_category must be a string or null")
    if passed:
        if raw != FailureCategory.VERIFIER_PASS.value:
            raise CaptureExtractError(f"{label} failure_category must be verifier-pass or null when passed")
        return None
    if raw not in _TASK_FAILURE_CATEGORIES:
        categories = ", ".join(sorted(_TASK_FAILURE_CATEGORIES))
        raise CaptureExtractError(f"{label} failure_category must be one of {categories} when failed")
    return raw


def _task_outcome_sort_key(row: Mapping[str, object]) -> tuple[str, str, int]:
    raw_attempt = row.get("attempt_index")
    attempt_index = raw_attempt if isinstance(raw_attempt, int) else -1
    return str(row.get("split", "")), str(row.get("task_id", "")), attempt_index


def _merge_decision(candidates: Sequence[Mapping[str, object]]) -> str:
    if any(candidate.get("audit_decision") == "merged" for candidate in candidates):
        return "accepted"
    if all(candidate.get("audit_decision") in {"rejected", "invalid", "superseded"} for candidate in candidates):
        return "rejected"
    return "none"


def _stable_payload_sha256(payload: Mapping[str, object]) -> str:
    return sha256((stable_json_dumps(payload) + "\n").encode("utf-8")).hexdigest()


def _capture_run_id(data: Mapping[str, Any], supplied: str | None, *, label: str) -> str:
    recorded: str | None = None
    if data.get("capture_run_id") is not None:
        recorded = _required_str(data, "capture_run_id", label=label)
    if supplied is not None:
        supplied_value = _required_str({"capture_run_id": supplied}, "capture_run_id", label="supplied capture run")
        if recorded is not None and recorded != supplied_value:
            raise CaptureExtractError(f"{label} capture_run_id must match supplied --capture-run-id")
        return supplied_value
    if recorded is not None:
        return recorded
    raise CaptureExtractError(f"{label} capture_run_id must be a non-empty string")


def _attempt_sort_key(row: Mapping[str, object]) -> int:
    value = row.get("attempt_index")
    return value if isinstance(value, int) else 0


def _round_index_sort_key(row: Mapping[str, object]) -> int:
    value = row.get("round_index")
    return value if isinstance(value, int) else 0


def _validated_live_harbor_discovery(data: Mapping[str, Any]) -> Mapping[str, Any]:
    _reject_reproduction_claims(data, label="Harbor discovery result")
    _reject_unknown_fields(data, _HARBOR_DISCOVERY_FIELDS, label="Harbor discovery result")
    if data.get("schema_version") != "1.0":
        raise CaptureExtractError("Harbor discovery result schema_version must be 1.0")
    if data.get("ok") is not True:
        raise CaptureExtractError("Harbor discovery result ok must be true")
    if data.get("mode") != "live":
        raise CaptureExtractError("Harbor discovery result mode must be live")
    images = _discovered_images(data)
    for index, image in enumerate(images):
        _reject_unknown_fields(image, _HARBOR_IMAGE_FIELDS, label=f"Harbor discovered image {index}")
        _required_str(image, "image", label=f"Harbor discovered image {index}")
        _required_str(image, "digest", label=f"Harbor discovered image {index}")
        _string_list(image, "tags", label=f"Harbor discovered image {index}")
        _optional_child_digests(image, label=f"Harbor discovered image {index}")
    return data


def _discovered_images(data: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_object_list(data, "discovered_images", label="Harbor discovery result"))


def _required_json_path(path: Path | None, flag: str) -> Mapping[str, Any]:
    return _read_json_object(_required_path(path, flag), label=flag)


def _required_path(path: Path | None, flag: str) -> Path:
    if path is None:
        raise CaptureExtractError(f"{flag} is required")
    return path


def _required_str_arg(value: str | None, flag: str) -> str:
    if value is None or not value:
        raise CaptureExtractError(f"{flag} is required")
    return value


def _read_json_object(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaptureExtractError(f"{label} must be valid JSON") from exc
    if not isinstance(data, dict):
        raise CaptureExtractError(f"{label} must be a JSON object")
    return cast(Mapping[str, Any], data)


def _fixed_protocol_binding_sha256(path: Path | None, provided_sha256: str | None) -> str:
    computed_sha256: str | None = None
    if path is not None:
        shape_error = artifact_shape_error("fixed_protocol_config", path)
        if shape_error is not None:
            raise CaptureExtractError(f"--fixed-protocol-result is invalid: {shape_error}")
        computed_sha256 = sha256(path.read_bytes()).hexdigest()
    if provided_sha256 is not None:
        if not _is_sha256(provided_sha256):
            raise CaptureExtractError("--fixed-protocol-sha256 must be 64 lowercase hex")
        if computed_sha256 is not None and provided_sha256 != computed_sha256:
            raise CaptureExtractError("--fixed-protocol-sha256 does not match --fixed-protocol-result")
        return provided_sha256
    if computed_sha256 is not None:
        return computed_sha256
    raise CaptureExtractError("--fixed-protocol-result or --fixed-protocol-sha256 is required")


def _read_jsonl_objects(path: Path, *, label: str) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureExtractError(f"{label} line {line_number} must be valid JSON") from exc
        if not isinstance(row, dict):
            raise CaptureExtractError(f"{label} line {line_number} must be a JSON object")
        rows.append(cast(Mapping[str, Any], row))
    return tuple(rows)


def _reject_reproduction_claims(value: object, *, label: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "reproduction_claimed" and item is not False:
                raise CaptureExtractError(f"{label} reproduction_claimed must be false")
            _reject_reproduction_claims(item, label=label)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_reproduction_claims(item, label=label)


def _reject_unknown_fields(data: Mapping[str, Any], allowed: frozenset[str], *, label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise CaptureExtractError(f"{label} has unknown field(s): {', '.join(unknown)}")


def _required_str(data: Mapping[str, Any], key: str, *, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise CaptureExtractError(f"{label} {key} must be a non-empty string")
    return value


def _string_list(data: Mapping[str, Any], key: str, *, label: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CaptureExtractError(f"{label} {key} must be a list of strings")
    return list(value)


def _object_list(data: Mapping[str, Any], key: str, *, label: str) -> list[Mapping[str, Any]]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CaptureExtractError(f"{label} {key} must be a list of objects")
    return [cast(Mapping[str, Any], item) for item in value]


def _context_block(data: Mapping[str, Any], key: str, *, label: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise CaptureExtractError(f"{label} {key} must be an object")
    return dict(value)


def _normalize_failure_pattern_evidence(block: Mapping[str, object], *, label: str) -> dict[str, object]:
    normalized = dict(block)
    patterns = _object_list(normalized, "patterns", label=label)
    normalized["patterns"] = [
        _normalize_failure_pattern_evidence_row(pattern, label=f"{label}.patterns[{index}]")
        for index, pattern in enumerate(patterns)
    ]
    return normalized


def _normalize_previous_edit_causal_statuses(block: Mapping[str, object], *, label: str) -> dict[str, object]:
    normalized = dict(block)
    edits = _object_list(normalized, "edits", label=label)
    normalized["edits"] = [
        _normalize_causal_status_row(edit, label=f"{label}.edits[{index}]")
        for index, edit in enumerate(edits)
    ]
    return normalized


def _normalize_causal_status_row(row: Mapping[str, Any], *, label: str) -> dict[str, object]:
    normalized = dict(row)
    raw = normalized.pop("causal_status", None)
    if raw is None:
        return normalized
    if not isinstance(raw, str) or not raw:
        raise CaptureExtractError(f"{label}.causal_status must be a non-empty string")
    expected = _stable_payload_sha256({"causal_status": raw})
    declared = normalized.get("causal_status_sha256")
    if declared is not None and declared != expected:
        raise CaptureExtractError(f"{label}.causal_status_sha256 must match causal_status")
    normalized["causal_status_sha256"] = expected
    return normalized


def _normalize_failure_pattern_evidence_row(row: Mapping[str, Any], *, label: str) -> dict[str, object]:
    normalized = _normalize_causal_status_row(row, label=label)
    for evidence_key in ("shared_symptoms", "verifier_evidence"):
        _normalize_evidence_hash_list(normalized, evidence_key, label=label)
    _normalize_evidence_hash_string(normalized, "actionability_hint", label=label)
    return normalized


def _normalize_evidence_hash_list(row: dict[str, object], evidence_key: str, *, label: str) -> None:
    raw = row.pop(evidence_key, None)
    hash_key = f"{evidence_key}_sha256"
    if raw is None:
        return
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        values = list(raw)
    else:
        raise CaptureExtractError(f"{label}.{evidence_key} must be a non-empty string or list of strings")
    if not values or any(not item for item in values):
        raise CaptureExtractError(f"{label}.{evidence_key} must contain non-empty strings")
    expected = _stable_payload_sha256({evidence_key: values})
    declared = row.get(hash_key)
    if declared is not None and declared != expected:
        raise CaptureExtractError(f"{label}.{hash_key} must match {evidence_key}")
    row[hash_key] = expected


def _normalize_evidence_hash_string(row: dict[str, object], evidence_key: str, *, label: str) -> None:
    raw = row.pop(evidence_key, None)
    hash_key = f"{evidence_key}_sha256"
    if raw is None:
        return
    if not isinstance(raw, str) or not raw:
        raise CaptureExtractError(f"{label}.{evidence_key} must be a non-empty string")
    expected = _stable_payload_sha256({evidence_key: raw})
    declared = row.get(hash_key)
    if declared is not None and declared != expected:
        raise CaptureExtractError(f"{label}.{hash_key} must match {evidence_key}")
    row[hash_key] = expected


def _split_task_ids(split_manifest: Mapping[str, Any]) -> set[str]:
    _reject_reproduction_claims(split_manifest, label="live split manifest")
    held_in = _string_list(split_manifest, "held_in_task_ids", label="live Terminal-Bench split manifest")
    held_out = _string_list(split_manifest, "held_out_task_ids", label="live Terminal-Bench split manifest")
    _reject_empty_strings(held_in, label="live Terminal-Bench split manifest held_in_task_ids")
    _reject_empty_strings(held_out, label="live Terminal-Bench split manifest held_out_task_ids")
    _reject_duplicates(held_in, label="live Terminal-Bench split manifest held_in_task_ids")
    _reject_duplicates(held_out, label="live Terminal-Bench split manifest held_out_task_ids")
    return set(held_in) | set(held_out)


def _validate_context_task_ids(
    block: Mapping[str, object],
    *,
    list_key: str,
    label: str,
    allowed_task_ids: set[str],
) -> None:
    rows = _object_list(block, list_key, label=label)
    for index, row in enumerate(rows):
        task_ids = _string_list(row, "task_ids", label=f"{label} {list_key}[{index}]")
        _reject_empty_strings(task_ids, label=f"{label} {list_key}[{index}].task_ids")
        _reject_duplicates(task_ids, label=f"{label} {list_key}[{index}].task_ids")
        unknown = sorted(set(task_ids) - allowed_task_ids)
        if unknown:
            raise CaptureExtractError(
                f"{label} {list_key}[{index}].task_ids reference unknown split task ids: "
                + ", ".join(unknown)
            )


def _reject_empty_strings(values: Sequence[str], *, label: str) -> None:
    if any(not value for value in values):
        raise CaptureExtractError(f"{label} must contain non-empty strings")


def _reject_duplicates(values: Sequence[str], *, label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise CaptureExtractError(f"{label} must not contain duplicate values: {', '.join(sorted(set(duplicates)))}")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_field(data: Mapping[str, Any], key: str, *, label: str) -> str:
    value = data.get(key)
    if not _is_sha256(value):
        raise CaptureExtractError(f"{label} {key} must be 64 lowercase hex")
    assert isinstance(value, str)
    return value


def _nonnegative_int(data: Mapping[str, Any], key: str, *, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value < 0:
        raise CaptureExtractError(f"{label} {key} must be a non-negative integer")
    return value


def _positive_int(data: Mapping[str, Any], key: str, *, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value < 1:
        raise CaptureExtractError(f"{label} {key} must be a positive integer")
    return value


def _validated_proposer_backend_map(values: Mapping[str, str]) -> dict[str, str]:
    if not values:
        raise CaptureExtractError("--proposer-backend-map is required")
    result: dict[str, str] = {}
    for client, backend in values.items():
        if not isinstance(client, str) or not client:
            raise CaptureExtractError("proposer backend map client labels must be non-empty strings")
        if not isinstance(backend, str) or not backend:
            raise CaptureExtractError("proposer backend map backends must be non-empty strings")
        normalized = _normal_model_backends((backend,))
        if len(normalized) != 1 or not normalized <= PAPER_MODEL_BACKENDS:
            raise CaptureExtractError(f"unknown proposer backend for {client}: {backend}")
        result[client] = next(iter(normalized))
    return result


def parse_proposer_backend_map(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise CaptureExtractError("--proposer-backend-map entries must use client=backend")
        client, backend = value.split("=", 1)
        result[client] = backend
    return _validated_proposer_backend_map(result)


def _optional_child_digests(data: Mapping[str, Any], *, label: str) -> list[str]:
    if data.get("child_digests") is None:
        return []
    values = _string_list(data, "child_digests", label=label)
    for index, value in enumerate(values):
        _optional_image_digest(value, label=f"{label} child_digests[{index}]")
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise CaptureExtractError(f"{label} child_digests must not contain duplicates: {', '.join(duplicates)}")
    return sorted(values)


def _optional_image_digest(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CaptureExtractError(f"{label} image_digest must be sha256:<64 lowercase hex>")
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise CaptureExtractError(f"{label} image_digest must be sha256:<64 lowercase hex>")
    digest = value.removeprefix(prefix)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CaptureExtractError(f"{label} image_digest must be sha256:<64 lowercase hex>")
    return value
