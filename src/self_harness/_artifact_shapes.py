from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from self_harness.types import FailureCategory

ArtifactValidator = Callable[[Path], str | None]

PAPER_MODEL_BACKENDS = frozenset({"minimax", "qwen", "glm"})
PAPER_MODEL_NAMES_BY_BACKEND = {
    "minimax": "MiniMax-M2.5",
    "qwen": "Qwen3.5-35B-A3B",
    "glm": "GLM-5.2",
}
_LIVE_TWO_REPEAT_EVALUATION_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "ok",
        "mode",
        "attempts_per_task",
        "per_task_attempts",
        "task_count",
        "attempt_count",
        "pass_count",
        "fail_count",
        "fixed_protocol_sha256",
        "capture_run_id",
        "reproduction_claimed",
        "boundary",
    }
)
_PROPOSER_LLM_REQUEST_LOG_FIELDS = frozenset(
    {
        "schema_version",
        "ok",
        "mode",
        "capture_run_id",
        "round_count",
        "rounds",
        "reproduction_claimed",
        "boundary",
    }
)
_PROPOSER_LLM_REQUEST_LOG_ROUND_FIELDS = frozenset(
    {
        "round_index",
        "backend",
        "model",
        "request_sha256",
        "response_sha256",
        "prompt_tokens",
        "completion_tokens",
        "attempted_proposals",
        "committed_proposals",
    }
)
_PROPOSER_CONTEXT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "ok",
        "mode",
        "capture_run_id",
        "round_count",
        "rounds",
        "reproduction_claimed",
        "boundary",
    }
)
_PROPOSER_CONTEXT_ROUND_FIELDS = frozenset(
    {
        "round_index",
        "editable_surfaces",
        "held_in_failure_patterns",
        "passing_behavior_summaries",
        "previous_attempted_edits",
    }
)
_EDITABLE_SURFACES_FIELDS = frozenset({"surface_count", "surfaces"})
_EDITABLE_SURFACE_FIELDS = frozenset({"kind", "name", "sha256"})
_HELD_IN_FAILURE_PATTERNS_FIELDS = frozenset({"pattern_count", "patterns"})
_HELD_IN_FAILURE_PATTERN_FIELDS = frozenset(
    {
        "cluster_id",
        "size",
        "task_ids",
        "mechanism_sha256",
        "failure_category",
        "causal_status_sha256",
        "shared_symptoms_sha256",
        "verifier_evidence_sha256",
        "presentation_order",
        "actionability_hint_sha256",
    }
)
_PASSING_BEHAVIOR_SUMMARIES_FIELDS = frozenset({"summary_count", "summaries"})
_PASSING_BEHAVIOR_SUMMARY_FIELDS = frozenset(
    {"task_ids", "task_id_set_sha256", "preserved_behavior_sha256"}
)
_PREVIOUS_ATTEMPTED_EDITS_FIELDS = frozenset({"edit_count", "edits"})
_PREVIOUS_ATTEMPTED_EDIT_FIELDS = frozenset(
    {
        "round_index",
        "surface",
        "decision",
        "proposal_round_index",
        "targeted_mechanism_sha256",
        "causal_status_sha256",
        "edited_surface_sha256",
        "audit_decision",
        "audit_decision_reason",
    }
)
_PROPOSAL_VALIDATION_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "ok",
        "mode",
        "capture_run_id",
        "round_count",
        "rounds",
        "fixed_protocol_sha256",
        "reproduction_claimed",
        "boundary",
    }
)
_PROPOSAL_VALIDATION_ROUND_FIELDS = frozenset(
    {
        "round_index",
        "harness_before_sha256",
        "harness_after_sha256",
        "harness_after_merged_sha256",
        "baseline_split_outcomes",
        "merged_split_outcomes",
        "candidates",
        "committed_proposal_ids",
        "merge_decision",
        "proposer_round_request_sha256",
        "proposer_round_response_sha256",
    }
)
_PROPOSAL_VALIDATION_SPLIT_OUTCOMES_FIELDS = frozenset(
    {
        "held_in_passed",
        "held_in_total",
        "held_out_passed",
        "held_out_total",
        "evaluation_repeats",
        "task_outcomes",
    }
)
_PROPOSAL_VALIDATION_TASK_OUTCOME_FIELDS = frozenset(
    {"task_id", "split", "pass", "attempt_index", "failure_category"}
)
_PROPOSAL_VALIDATION_CANDIDATE_FIELDS = frozenset(
    {
        "proposal_id",
        "proposal_round_index",
        "pattern_id",
        "changed_surfaces",
        "edited_surface_sha256",
        "targeted_mechanism_sha256",
        "summary_sha256",
        "split_outcomes",
        "audit_decision",
        "validation_failure_category",
        "decision_reason",
        "rejection_reason",
    }
)
_PROPOSAL_VALIDATION_DECISIONS = frozenset({"accepted", "rejected", "superseded", "merged", "invalid"})
_PROPOSAL_VALIDATION_FAILURE_CATEGORIES = frozenset({"no_editable_surface", "execution_failure"})
_PROPOSAL_VALIDATION_MERGE_DECISIONS = frozenset({"accepted", "rejected", "none"})
_TASK_FAILURE_CATEGORIES = frozenset(
    category.value for category in FailureCategory if category is not FailureCategory.VERIFIER_PASS
)


def artifact_shape_error(artifact_class: str, path: Path) -> str | None:
    validator = REPRODUCTION_ARTIFACT_CLASS_VALIDATORS.get(artifact_class)
    if validator is None:
        return f"unsupported required_artifact_class: {artifact_class}"
    return validator(path)


def artifact_shape_error_from_payload(artifact_class: str, payload: Mapping[str, Any]) -> str | None:
    """Validate an already-loaded artifact payload with the file-backed validators."""

    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        return artifact_shape_error(artifact_class, Path(handle.name))


def supported_reproduction_artifact_classes() -> frozenset[str]:
    return frozenset(REPRODUCTION_ARTIFACT_CLASS_VALIDATORS)


def _live_terminal_bench_split_manifest(path: Path) -> str | None:
    data, error = _base_json(path, "live Terminal-Bench split manifest")
    if error is not None:
        return error
    if (error := _require_live(data, "live Terminal-Bench split manifest")) is not None:
        return error
    if data.get("source") != "harbor":
        return "live Terminal-Bench split manifest source must be harbor"
    if not _non_empty_str(data.get("capture_run_id")):
        return "live Terminal-Bench split manifest capture_run_id must be a non-empty string"
    if not _non_empty_str(data.get("harbor_version")):
        return "live Terminal-Bench split manifest harbor_version must be a non-empty string"
    if data.get("fixed_across_variants") is not True:
        return "live Terminal-Bench split manifest fixed_across_variants must be true"
    if data.get("total_cases") != 64:
        return "live Terminal-Bench split manifest total_cases must be 64"

    held_in_ids, error = _string_list(data, "held_in_task_ids", "live Terminal-Bench split manifest")
    if error is not None:
        return error
    held_out_ids, error = _string_list(data, "held_out_task_ids", "live Terminal-Bench split manifest")
    if error is not None:
        return error
    if not held_in_ids or not held_out_ids:
        return "live Terminal-Bench split manifest must include held-in and held-out tasks"
    if data.get("held_in_count") != len(held_in_ids):
        return "live Terminal-Bench split manifest held_in_count must match held_in_task_ids"
    if data.get("held_out_count") != len(held_out_ids):
        return "live Terminal-Bench split manifest held_out_count must match held_out_task_ids"
    if len(held_in_ids) + len(held_out_ids) != 64:
        return "live Terminal-Bench split manifest split counts must total 64"
    overlap = sorted(set(held_in_ids) & set(held_out_ids))
    if overlap:
        return "live Terminal-Bench split manifest held-in and held-out tasks must be disjoint"
    return None


def _live_two_repeat_evaluation_report(path: Path) -> str | None:
    data, error = _base_json(path, "live two-repeat evaluation report")
    if error is not None:
        return error
    if (
        error := _reject_unknown_fields(
            data,
            _LIVE_TWO_REPEAT_EVALUATION_REPORT_FIELDS,
            "live two-repeat evaluation report",
        )
    ) is not None:
        return error
    if (error := _require_live(data, "live two-repeat evaluation report")) is not None:
        return error
    attempts_per_task = data.get("attempts_per_task")
    if attempts_per_task != 2:
        return "live two-repeat evaluation report attempts_per_task must be 2"
    rows, error = _object_list(data, "per_task_attempts", "live two-repeat evaluation report")
    if error is not None:
        return error
    if not rows:
        return "live two-repeat evaluation report per_task_attempts must be non-empty"
    seen: set[str] = set()
    observed_pass_count = 0
    for index, row in enumerate(rows):
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return f"live two-repeat evaluation report per_task_attempts[{index}].task_id must be non-empty"
        if task_id in seen:
            return f"live two-repeat evaluation report duplicate task_id: {task_id}"
        seen.add(task_id)
        attempts = row.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != 2:
            return f"live two-repeat evaluation report {task_id} must record exactly 2 attempts"
        for attempt_index, attempt in enumerate(attempts):
            if not isinstance(attempt, dict) or not isinstance(attempt.get("pass"), bool):
                return (
                    "live two-repeat evaluation report "
                    f"{task_id} attempts[{attempt_index}].pass must be boolean"
                )
            if attempt["pass"] is True:
                observed_pass_count += 1
    task_count = data.get("task_count")
    if not isinstance(task_count, int) or task_count < 1:
        return "live two-repeat evaluation report task_count must be a positive integer"
    if task_count != len(rows):
        return "live two-repeat evaluation report task_count must match per_task_attempts"
    attempt_count = data.get("attempt_count")
    if not isinstance(attempt_count, int) or attempt_count < 1:
        return "live two-repeat evaluation report attempt_count must be a positive integer"
    if attempt_count != attempts_per_task * task_count:
        return "live two-repeat evaluation report attempt_count must equal attempts_per_task * task_count"
    pass_count = data.get("pass_count")
    if not isinstance(pass_count, int) or pass_count < 0:
        return "live two-repeat evaluation report pass_count must be a non-negative integer"
    if pass_count != observed_pass_count:
        return "live two-repeat evaluation report pass_count must match per_task_attempts"
    fail_count = data.get("fail_count")
    if not isinstance(fail_count, int) or fail_count < 0:
        return "live two-repeat evaluation report fail_count must be a non-negative integer"
    if fail_count != attempt_count - pass_count:
        return "live two-repeat evaluation report fail_count must equal attempt_count - pass_count"
    if not _sha256(data.get("fixed_protocol_sha256")):
        return "live two-repeat evaluation report fixed_protocol_sha256 must be 64 lowercase hex"
    if not _non_empty_str(data.get("capture_run_id")):
        return "live two-repeat evaluation report capture_run_id must be a non-empty string"
    return None


def _fixed_protocol_config(path: Path) -> str | None:
    data, error = _base_json(path, "fixed protocol config")
    if error is not None:
        return error
    if (error := _require_live(data, "fixed protocol config")) is not None:
        return error
    if data.get("benchmark_protocol") != "terminal-bench@2.0":
        return "fixed protocol config benchmark_protocol must be terminal-bench@2.0"
    if not _non_empty_str(data.get("capture_run_id")):
        return "fixed protocol config capture_run_id must be a non-empty string"
    models, error = _string_list(data, "models", "fixed protocol config")
    if error is not None:
        return error
    if _normal_model_backends(models) != PAPER_MODEL_BACKENDS:
        return "fixed protocol config models must cover MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5.2"
    if not _non_empty_str(data.get("evaluator")):
        return "fixed protocol config evaluator must be a non-empty string"
    if not _non_empty_str(data.get("tool_set")):
        return "fixed protocol config tool_set must be a non-empty string"
    if not isinstance(data.get("decoding_budget"), dict):
        return "fixed protocol config decoding_budget must be an object"
    rounds = data.get("self_harness_rounds")
    if not isinstance(rounds, int) or rounds < 1:
        return "fixed protocol config self_harness_rounds must be a positive integer"
    proposal_width = data.get("proposal_width")
    if not isinstance(proposal_width, int) or proposal_width < 1:
        return "fixed protocol config proposal_width must be a positive integer"
    if data.get("fixed_across_variants") is not True:
        return "fixed protocol config fixed_across_variants must be true"
    return None


def _live_harbor_preflight_report(path: Path) -> str | None:
    data, error = _base_json(path, "live Harbor preflight report")
    if error is not None:
        return error
    if (error := _require_ok_live(data, "live Harbor preflight report")) is not None:
        return error
    if data.get("harbor_reachable") is not True:
        return "live Harbor preflight report harbor_reachable must be true"
    if not _non_empty_str(data.get("capture_run_id")):
        return "live Harbor preflight report capture_run_id must be a non-empty string"
    if not _non_empty_str(data.get("harbor_version")):
        return "live Harbor preflight report harbor_version must be a non-empty string"
    return None


def _container_image_trust_report(path: Path) -> str | None:
    data, error = _base_json(path, "container image trust report")
    if error is not None:
        return error
    if (error := _require_ok_live(data, "container image trust report")) is not None:
        return error
    if data.get("policy") != "digest-bound":
        return "container image trust report policy must be digest-bound"
    if data.get("all_digest_bound") is not True:
        return "container image trust report all_digest_bound must be true"
    if not _non_empty_str(data.get("capture_run_id")):
        return "container image trust report capture_run_id must be a non-empty string"
    images, error = _object_list(data, "images", "container image trust report")
    if error is not None:
        return error
    if not images:
        return "container image trust report images must be non-empty"
    for index, image in enumerate(images):
        if not _non_empty_str(image.get("name")):
            return f"container image trust report images[{index}].name must be non-empty"
        if not _sha256_image_digest(image.get("digest")):
            return f"container image trust report images[{index}].digest must be sha256:<64 lowercase hex>"
        if "child_digests" in image:
            child_digests = image.get("child_digests")
            if not isinstance(child_digests, list) or not child_digests:
                return f"container image trust report images[{index}].child_digests must be a non-empty list"
            if not all(_sha256_image_digest(item) for item in child_digests):
                return (
                    "container image trust report "
                    f"images[{index}].child_digests must contain sha256:<64 lowercase hex> values"
                )
            if len(set(child_digests)) != len(child_digests):
                return f"container image trust report images[{index}].child_digests must not contain duplicates"
    return None


def _model_backend_preflight_report(path: Path) -> str | None:
    data, error = _base_json(path, "model backend preflight report")
    if error is not None:
        return error
    if (error := _require_ok_live(data, "model backend preflight report")) is not None:
        return error
    if not _non_empty_str(data.get("capture_run_id")):
        return "model backend preflight report capture_run_id must be a non-empty string"
    backends, error = _string_list(data, "backends", "model backend preflight report")
    if error is not None:
        return error
    if _normal_model_backends(backends) != PAPER_MODEL_BACKENDS:
        return "model backend preflight report backends must cover MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5.2"
    if (error := _required_checks_pass(data, "model backend preflight report")) is not None:
        return error
    return None


def _proposer_llm_request_log(path: Path) -> str | None:
    data, error = _base_json(path, "proposer LLM request log")
    if error is not None:
        return error
    if (
        error := _reject_unknown_fields(
            data,
            _PROPOSER_LLM_REQUEST_LOG_FIELDS,
            "proposer LLM request log",
        )
    ) is not None:
        return error
    if (error := _require_ok_live(data, "proposer LLM request log")) is not None:
        return error
    if not _non_empty_str(data.get("capture_run_id")):
        return "proposer LLM request log capture_run_id must be a non-empty string"
    rounds, error = _object_list(data, "rounds", "proposer LLM request log")
    if error is not None:
        return error
    if not rounds:
        return "proposer LLM request log rounds must be non-empty"
    if data.get("round_count") != len(rounds):
        return "proposer LLM request log round_count must match rounds"
    for expected_index, row in enumerate(rounds):
        if (
            error := _reject_unknown_fields(
                row,
                _PROPOSER_LLM_REQUEST_LOG_ROUND_FIELDS,
                f"proposer LLM request log rounds[{expected_index}]",
            )
        ) is not None:
            return error
        if row.get("round_index") != expected_index:
            return "proposer LLM request log round_index values must be contiguous from zero"
        backend = row.get("backend")
        if not isinstance(backend, str) or _normal_model_backends((backend,)) not in (
            frozenset({"minimax"}),
            frozenset({"qwen"}),
            frozenset({"glm"}),
        ):
            return f"proposer LLM request log rounds[{expected_index}].backend must be a paper backend"
        normalized_backend = next(iter(_normal_model_backends((backend,))))
        if row.get("model") != PAPER_MODEL_NAMES_BY_BACKEND[normalized_backend]:
            return f"proposer LLM request log rounds[{expected_index}].model must match backend"
        for key in ("request_sha256", "response_sha256"):
            if not _sha256(row.get(key)):
                return f"proposer LLM request log rounds[{expected_index}].{key} must be 64 lowercase hex"
        for key in ("prompt_tokens", "completion_tokens", "attempted_proposals", "committed_proposals"):
            if not isinstance(row.get(key), int) or row[key] < 0:
                return f"proposer LLM request log rounds[{expected_index}].{key} must be a non-negative integer"
        if row["committed_proposals"] > row["attempted_proposals"]:
            return (
                "proposer LLM request log "
                f"rounds[{expected_index}].committed_proposals must not exceed attempted_proposals"
            )
    return None


def _proposer_context_manifest(path: Path) -> str | None:
    data, error = _base_json(path, "proposer context manifest")
    if error is not None:
        return error
    if (
        error := _reject_unknown_fields(
            data,
            _PROPOSER_CONTEXT_MANIFEST_FIELDS,
            "proposer context manifest",
        )
    ) is not None:
        return error
    if (error := _require_ok_live(data, "proposer context manifest")) is not None:
        return error
    if not _non_empty_str(data.get("capture_run_id")):
        return "proposer context manifest capture_run_id must be a non-empty string"
    rounds, error = _object_list(data, "rounds", "proposer context manifest")
    if error is not None:
        return error
    if not rounds:
        return "proposer context manifest rounds must be non-empty"
    round_count = data.get("round_count")
    if not isinstance(round_count, int) or round_count < 1:
        return "proposer context manifest round_count must be a positive integer"
    if round_count != len(rounds):
        return "proposer context manifest round_count must match rounds"
    for expected_index, row in enumerate(rounds):
        if (
            error := _reject_unknown_fields(
                row,
                _PROPOSER_CONTEXT_ROUND_FIELDS,
                f"proposer context manifest rounds[{expected_index}]",
            )
        ) is not None:
            return error
        if row.get("round_index") != expected_index:
            return "proposer context manifest round_index values must be contiguous from zero"
        if (
            error := _editable_surfaces_block(
                row.get("editable_surfaces"),
                f"proposer context manifest rounds[{expected_index}].editable_surfaces",
            )
        ) is not None:
            return error
        if (
            error := _held_in_failure_patterns_block(
                row.get("held_in_failure_patterns"),
                f"proposer context manifest rounds[{expected_index}].held_in_failure_patterns",
            )
        ) is not None:
            return error
        if (
            error := _passing_behavior_summaries_block(
                row.get("passing_behavior_summaries"),
                f"proposer context manifest rounds[{expected_index}].passing_behavior_summaries",
            )
        ) is not None:
            return error
        if (
            error := _previous_attempted_edits_block(
                row.get("previous_attempted_edits"),
                f"proposer context manifest rounds[{expected_index}].previous_attempted_edits",
            )
        ) is not None:
            return error
    return None


def _proposal_validation_manifest(path: Path) -> str | None:
    data, error = _base_json(path, "proposal validation manifest")
    if error is not None:
        return error
    if (
        error := _reject_unknown_fields(
            data,
            _PROPOSAL_VALIDATION_MANIFEST_FIELDS,
            "proposal validation manifest",
        )
    ) is not None:
        return error
    if (error := _require_ok_live(data, "proposal validation manifest")) is not None:
        return error
    if not _non_empty_str(data.get("capture_run_id")):
        return "proposal validation manifest capture_run_id must be a non-empty string"
        if not _sha256(data.get("fixed_protocol_sha256")):
            return "proposal validation manifest fixed_protocol_sha256 must be 64 lowercase hex"
    rounds, error = _object_list(data, "rounds", "proposal validation manifest")
    if error is not None:
        return error
    if not rounds:
        return "proposal validation manifest rounds must be non-empty"
    round_count = data.get("round_count")
    if not isinstance(round_count, int) or round_count < 1:
        return "proposal validation manifest round_count must be a positive integer"
    if round_count != len(rounds):
        return "proposal validation manifest round_count must match rounds"
    for expected_index, row in enumerate(rounds):
        row_label = f"proposal validation manifest rounds[{expected_index}]"
        if (error := _reject_unknown_fields(row, _PROPOSAL_VALIDATION_ROUND_FIELDS, row_label)) is not None:
            return error
        if row.get("round_index") != expected_index:
            return "proposal validation manifest round_index values must be contiguous from zero"
        harness_before_hash = row.get("harness_before_sha256")
        harness_after_hash = row.get("harness_after_sha256")
        harness_after_merged_hash = row.get("harness_after_merged_sha256")
        if (harness_before_hash is None) != (harness_after_hash is None):
            return f"{row_label}.harness_before_sha256 and harness_after_sha256 must be present together"
        if harness_before_hash is not None and not _sha256(harness_before_hash):
            return f"{row_label}.harness_before_sha256 must be 64 lowercase hex"
        if harness_after_hash is not None and not _sha256(harness_after_hash):
            return f"{row_label}.harness_after_sha256 must be 64 lowercase hex"
        if harness_after_merged_hash is not None and not _sha256(harness_after_merged_hash):
            return f"{row_label}.harness_after_merged_sha256 must be 64 lowercase hex"
        request_hash = row.get("proposer_round_request_sha256")
        response_hash = row.get("proposer_round_response_sha256")
        if (request_hash is None) != (response_hash is None):
            return (
                f"{row_label}.proposer_round_request_sha256 and "
                "proposer_round_response_sha256 must be present together"
            )
        if request_hash is not None and not _sha256(request_hash):
            return f"{row_label}.proposer_round_request_sha256 must be 64 lowercase hex"
        if response_hash is not None and not _sha256(response_hash):
            return f"{row_label}.proposer_round_response_sha256 must be 64 lowercase hex"
        baseline_split_outcomes = row.get("baseline_split_outcomes")
        if (
            error := _proposal_validation_split_outcomes(
                baseline_split_outcomes,
                f"{row_label}.baseline_split_outcomes",
            )
        ) is not None:
            return error
        assert isinstance(baseline_split_outcomes, dict)
        baseline_evaluation_repeats = baseline_split_outcomes.get("evaluation_repeats")
        assert isinstance(baseline_evaluation_repeats, int)
        committed, error = _string_list(row, "committed_proposal_ids", row_label)
        if error is not None:
            return error
        if len(set(committed)) != len(committed):
            return f"{row_label}.committed_proposal_ids must not contain duplicates"
        merged_split_outcomes = row.get("merged_split_outcomes")
        if merged_split_outcomes is not None:
            if (
                error := _proposal_validation_split_outcomes(
                    merged_split_outcomes,
                    f"{row_label}.merged_split_outcomes",
                )
            ) is not None:
                return error
            assert isinstance(merged_split_outcomes, dict)
            if merged_split_outcomes.get("evaluation_repeats") != baseline_evaluation_repeats:
                return (
                    f"{row_label}.merged_split_outcomes.evaluation_repeats must match "
                    "baseline_split_outcomes.evaluation_repeats"
                )
            if harness_before_hash is None or harness_after_hash is None:
                return (
                    f"{row_label}.merged_split_outcomes requires "
                    "harness_before_sha256 and harness_after_sha256"
                )
            if len(committed) < 2:
                return f"{row_label}.merged_split_outcomes is only valid for multi-commit rounds"
        if len(committed) >= 2 and harness_before_hash is not None and merged_split_outcomes is None:
            return f"{row_label}.merged_split_outcomes is required for multi-commit rounds with harness hashes"
        if harness_after_merged_hash is not None:
            if harness_before_hash is None or harness_after_hash is None:
                return (
                    f"{row_label}.harness_after_merged_sha256 requires "
                    "harness_before_sha256 and harness_after_sha256"
                )
            if len(committed) < 2:
                return f"{row_label}.harness_after_merged_sha256 is only valid for multi-commit rounds"
            if harness_after_merged_hash != harness_after_hash:
                return f"{row_label}.harness_after_merged_sha256 must match harness_after_sha256"
        if len(committed) >= 2 and harness_before_hash is not None and harness_after_merged_hash is None:
            return f"{row_label}.harness_after_merged_sha256 is required for multi-commit rounds with harness hashes"
        merge_decision = row.get("merge_decision")
        if merge_decision not in _PROPOSAL_VALIDATION_MERGE_DECISIONS:
            return f"{row_label}.merge_decision must be accepted, rejected, or none"
        candidates, error = _object_list(row, "candidates", row_label)
        if error is not None:
            return error
        if not candidates:
            return f"{row_label}.candidates must be non-empty"
        seen_ids: set[str] = set()
        accepted_ids: set[str] = set()
        for candidate_index, candidate in enumerate(candidates):
            candidate_label = f"{row_label}.candidates[{candidate_index}]"
            if (
                error := _proposal_validation_candidate(
                    candidate,
                    candidate_label,
                    expected_round_index=expected_index,
                )
            ) is not None:
                return error
            candidate_split_outcomes = candidate.get("split_outcomes")
            assert isinstance(candidate_split_outcomes, dict)
            if candidate_split_outcomes.get("evaluation_repeats") != baseline_evaluation_repeats:
                return (
                    f"{candidate_label}.split_outcomes.evaluation_repeats must match "
                    "baseline_split_outcomes.evaluation_repeats"
                )
            proposal_id = candidate.get("proposal_id")
            assert isinstance(proposal_id, str)
            if proposal_id in seen_ids:
                return f"{row_label}.candidates proposal_id values must be unique"
            seen_ids.add(proposal_id)
            if candidate.get("audit_decision") in {"accepted", "merged"}:
                accepted_ids.add(proposal_id)
        if set(committed) != accepted_ids:
            return f"{row_label}.committed_proposal_ids must match accepted or merged candidates"
    return None


def _proposal_validation_candidate(
    value: object,
    label: str,
    *,
    expected_round_index: int,
) -> str | None:
    if not isinstance(value, dict):
        return f"{label} must be an object"
    if (error := _reject_unknown_fields(value, _PROPOSAL_VALIDATION_CANDIDATE_FIELDS, label)) is not None:
        return error
    if not _non_empty_str(value.get("proposal_id")):
        return f"{label}.proposal_id must be a non-empty string"
    if value.get("proposal_round_index") != expected_round_index:
        return f"{label}.proposal_round_index must match the parent round_index"
    if not _non_empty_str(value.get("pattern_id")):
        return f"{label}.pattern_id must be a non-empty string"
    audit_decision = value.get("audit_decision")
    if audit_decision not in _PROPOSAL_VALIDATION_DECISIONS:
        return f"{label}.audit_decision must be accepted, rejected, superseded, merged, or invalid"
    failure_category = value.get("validation_failure_category")
    if failure_category is not None and failure_category not in _PROPOSAL_VALIDATION_FAILURE_CATEGORIES:
        return (
            f"{label}.validation_failure_category must be no_editable_surface, "
            "execution_failure, or null"
        )
    if audit_decision == "invalid":
        if failure_category not in _PROPOSAL_VALIDATION_FAILURE_CATEGORIES:
            return f"{label}.validation_failure_category must be non-null for invalid candidates"
    elif failure_category is not None:
        return f"{label}.validation_failure_category must be null unless audit_decision is invalid"
    changed_surfaces, error = _string_list(value, "changed_surfaces", label)
    if error is not None:
        return error
    if any(not surface for surface in changed_surfaces):
        return f"{label}.changed_surfaces must contain non-empty strings"
    if len(set(changed_surfaces)) != len(changed_surfaces):
        return f"{label}.changed_surfaces must not contain duplicates"
    # Paper Section 3.3 scopes each proposal to one edited surface; only
    # no_editable_surface invalid candidates may disclose none.
    if failure_category == "no_editable_surface":
        if changed_surfaces:
            return f"{label}.changed_surfaces must be empty for no_editable_surface invalid candidates"
    elif not changed_surfaces:
        return f"{label}.changed_surfaces must contain non-empty strings"
    elif len(changed_surfaces) != 1:
        return (
            f"{label}.changed_surfaces must contain exactly one surface "
            "for non-no_editable_surface candidates"
        )
    if not _sha256(value.get("edited_surface_sha256")):
        return f"{label}.edited_surface_sha256 must be 64 lowercase hex"
    if not _sha256(value.get("targeted_mechanism_sha256")):
        return f"{label}.targeted_mechanism_sha256 must be 64 lowercase hex"
    if not _sha256(value.get("summary_sha256")):
        return f"{label}.summary_sha256 must be 64 lowercase hex"
    if (
        error := _proposal_validation_split_outcomes(
            value.get("split_outcomes"),
            f"{label}.split_outcomes",
        )
    ) is not None:
        return error
    if not _non_empty_str(value.get("decision_reason")):
        return f"{label}.decision_reason must be a non-empty string"
    rejection_reason = value.get("rejection_reason")
    if rejection_reason is not None and not isinstance(rejection_reason, str):
        return f"{label}.rejection_reason must be a string or null"
    if audit_decision in {"rejected", "superseded", "invalid"} and not rejection_reason:
        return f"{label}.rejection_reason must be non-empty for rejected, superseded, or invalid candidates"
    return None


def _proposal_validation_split_outcomes(value: object, label: str) -> str | None:
    if not isinstance(value, dict):
        return f"{label} must be an object"
    if (error := _reject_unknown_fields(value, _PROPOSAL_VALIDATION_SPLIT_OUTCOMES_FIELDS, label)) is not None:
        return error
    for key in ("held_in_passed", "held_in_total", "held_out_passed", "held_out_total", "evaluation_repeats"):
        raw = value.get(key)
        if not isinstance(raw, int) or raw < 0:
            return f"{label}.{key} must be a non-negative integer"
    if value["held_in_total"] < value["held_in_passed"]:
        return f"{label}.held_in_passed must not exceed held_in_total"
    if value["held_out_total"] < value["held_out_passed"]:
        return f"{label}.held_out_passed must not exceed held_out_total"
    if value["evaluation_repeats"] < 1:
        return f"{label}.evaluation_repeats must be a positive integer"
    if "task_outcomes" in value:
        if (error := _proposal_validation_task_outcomes(value, label)) is not None:
            return error
    return None


def _proposal_validation_task_outcomes(value: Mapping[str, Any], label: str) -> str | None:
    outcomes, error = _object_list(value, "task_outcomes", label)
    if error is not None:
        return error
    if not outcomes:
        return f"{label}.task_outcomes must be non-empty when present"
    seen: set[tuple[str, str, int | None]] = set()
    by_split = {
        "held_in": {"passed": 0, "total": 0},
        "held_out": {"passed": 0, "total": 0},
    }
    for index, outcome in enumerate(outcomes):
        row_label = f"{label}.task_outcomes[{index}]"
        if (error := _reject_unknown_fields(outcome, _PROPOSAL_VALIDATION_TASK_OUTCOME_FIELDS, row_label)) is not None:
            return error
        task_id = outcome.get("task_id")
        if not _non_empty_str(task_id):
            return f"{row_label}.task_id must be a non-empty string"
        split = outcome.get("split")
        if split not in by_split:
            return f"{row_label}.split must be held_in or held_out"
        passed = outcome.get("pass")
        if not isinstance(passed, bool):
            return f"{row_label}.pass must be boolean"
        failure_category = outcome.get("failure_category")
        if failure_category is not None:
            if not isinstance(failure_category, str) or failure_category not in _TASK_FAILURE_CATEGORIES:
                categories = ", ".join(sorted(_TASK_FAILURE_CATEGORIES))
                return f"{row_label}.failure_category must be one of {categories}, or null"
            if passed:
                return f"{row_label}.failure_category must be null or omitted when pass is true"
        attempt_index = outcome.get("attempt_index")
        if attempt_index is not None and (not isinstance(attempt_index, int) or attempt_index < 0):
            return f"{row_label}.attempt_index must be a non-negative integer or null"
        assert isinstance(task_id, str)
        assert isinstance(split, str)
        key = (task_id, split, attempt_index)
        if key in seen:
            return f"{label}.task_outcomes must not contain duplicate task/split/attempt rows"
        seen.add(key)
        by_split[split]["total"] += 1
        if passed:
            by_split[split]["passed"] += 1
    expected = {
        "held_in": {
            "passed": value["held_in_passed"],
            "total": value["held_in_total"],
        },
        "held_out": {
            "passed": value["held_out_passed"],
            "total": value["held_out_total"],
        },
    }
    for split, observed in by_split.items():
        if observed != expected[split]:
            return (
                f"{label}.task_outcomes {split} pass/total counts must reconcile "
                "with aggregate split outcomes"
            )
    return None


def _network_resource_controls_attestation(path: Path) -> str | None:
    data, error = _base_json(path, "network resource controls attestation")
    if error is not None:
        return error
    if (error := _require_ok_live(data, "network resource controls attestation")) is not None:
        return error
    cap = data.get("outbound_bandwidth_cap_bps")
    if not isinstance(cap, int) or cap <= 0:
        return "network resource controls attestation outbound_bandwidth_cap_bps must be a positive integer"
    if not _non_empty_str(data.get("capture_run_id")):
        return "network resource controls attestation capture_run_id must be a non-empty string"
    mirrored, error = _string_list(data, "mirrored_resources", "network resource controls attestation")
    if error is not None:
        return error
    if any(not item for item in mirrored):
        return "network resource controls attestation mirrored_resources must contain non-empty strings"
    return None


def _live_harbor_audit(path: Path) -> str | None:
    data, error = _base_json(path, "live Harbor audit")
    if error is not None:
        return error
    if (error := _require_ok_live(data, "live Harbor audit")) is not None:
        return error
    if not _sha256(data.get("fixed_protocol_sha256")):
        return "live Harbor audit fixed_protocol_sha256 must be 64 lowercase hex"
    if not _non_empty_str(data.get("capture_run_id")):
        return "live Harbor audit capture_run_id must be a non-empty string"
    artifacts, error = _object_list(data, "trial_artifacts", "live Harbor audit")
    if error is not None:
        return error
    if not artifacts:
        return "live Harbor audit trial_artifacts must be non-empty"
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        task_id = artifact.get("task_id")
        if not _non_empty_str(task_id):
            return f"live Harbor audit trial_artifacts[{index}].task_id must be non-empty"
        assert isinstance(task_id, str)
        if task_id in seen:
            return f"live Harbor audit duplicate task_id: {task_id}"
        seen.add(task_id)
        if artifact.get("captured") is not True:
            return f"live Harbor audit trial_artifacts[{index}].captured must be true"
        image_digest = artifact.get("image_digest")
        if image_digest is not None and not _sha256_image_digest(image_digest):
            return f"live Harbor audit trial_artifacts[{index}].image_digest must be sha256:<64 lowercase hex>"
        verifier_outcome = artifact.get("verifier_outcome")
        if not _non_empty_str(verifier_outcome):
            return f"live Harbor audit trial_artifacts[{index}].verifier_outcome must be non-empty"
        attempts = artifact.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != 2:
            return f"live Harbor audit trial_artifacts[{index}].attempts must contain exactly 2 attempts"
        attempt_indexes: set[int] = set()
        pass_values: list[bool] = []
        for attempt_index, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                return f"live Harbor audit trial_artifacts[{index}].attempts[{attempt_index}] must be an object"
            raw_attempt_index = attempt.get("attempt_index")
            if raw_attempt_index not in {0, 1}:
                return (
                    f"live Harbor audit trial_artifacts[{index}].attempts[{attempt_index}]."
                    "attempt_index must be 0 or 1"
                )
            if raw_attempt_index in attempt_indexes:
                return f"live Harbor audit trial_artifacts[{index}].attempt indexes must be distinct"
            attempt_indexes.add(raw_attempt_index)
            passed = attempt.get("pass")
            if not isinstance(passed, bool):
                return f"live Harbor audit trial_artifacts[{index}].attempts[{attempt_index}].pass must be boolean"
            pass_values.append(passed)
        expected_outcome = "pass" if all(pass_values) else "fail"
        if verifier_outcome != expected_outcome:
            return (
                "live Harbor audit "
                f"trial_artifacts[{index}].verifier_outcome must be {expected_outcome}"
            )
    return None


def _audit_verify_report(path: Path) -> str | None:
    data, error = _base_json(path, "audit verify report")
    if error is not None:
        return error
    if (error := _require_ok_live(data, "audit verify report")) is not None:
        return error
    expected = {
        "held_out_leakage": False,
        "proposer_evidence_inspected": True,
        "changed_surfaces_recorded": True,
        "evaluation_repeats_recorded": True,
        "rejected_reasons_recorded": True,
    }
    for key, expected_value in expected.items():
        if data.get(key) is not expected_value:
            return f"audit verify report {key} must be {str(expected_value).lower()}"
    if not _sha256(data.get("report_hash")):
        return "audit verify report report_hash must be 64 lowercase hex characters"
    return None


def _release_candidate_evidence(path: Path) -> str | None:
    data, error = _base_json(path, "release candidate evidence")
    if error is not None:
        return error
    if data.get("schema_version") != "1.0":
        return "release candidate evidence schema_version must be 1.0"
    if data.get("ok") is not True:
        return "release candidate evidence ok field must be true"
    if data.get("decision") != "ready":
        return "release candidate evidence decision must be ready"
    if not _sha256(data.get("evidence_sha256")):
        return "release candidate evidence evidence_sha256 must be 64 lowercase hex characters"
    gates, error = _object_list(data, "gates", "release candidate evidence")
    if error is not None:
        return error
    gates_by_name = {str(gate.get("name")): gate for gate in gates if isinstance(gate.get("name"), str)}
    for name in ("audit_integrity", "provenance_manifest", "attestation", "reproduction_readiness"):
        gate = gates_by_name.get(name)
        if gate is None:
            return f"release candidate evidence missing gate: {name}"
        if gate.get("status") != "pass":
            return f"release candidate evidence gate {name} must pass"
    metadata = gates_by_name["reproduction_readiness"].get("metadata")
    if not isinstance(metadata, dict):
        return "release candidate evidence reproduction_readiness gate metadata must be an object"
    if metadata.get("reproduction_ready") is not True:
        return "release candidate evidence reproduction_readiness gate must be ready"
    if not _sha256(metadata.get("report_hash")):
        return "release candidate evidence reproduction_readiness report_hash must be 64 lowercase hex"
    return None


def _editable_surfaces_block(value: object, label: str) -> str | None:
    if not isinstance(value, dict):
        return f"{label} must be an object"
    if (error := _reject_unknown_fields(value, _EDITABLE_SURFACES_FIELDS, label)) is not None:
        return error
    surfaces, error = _object_list(value, "surfaces", label)
    if error is not None:
        return error
    if value.get("surface_count") != len(surfaces):
        return f"{label} surface_count must match surfaces"
    surface_sha256s: dict[str, int] = {}
    for index, surface in enumerate(surfaces):
        row_label = f"{label}.surfaces[{index}]"
        if (error := _reject_unknown_fields(surface, _EDITABLE_SURFACE_FIELDS, row_label)) is not None:
            return error
        if not _non_empty_str(surface.get("kind")):
            return f"{row_label}.kind must be a non-empty string"
        if not _non_empty_str(surface.get("name")):
            return f"{row_label}.name must be a non-empty string"
        sha256_value = surface.get("sha256")
        if not _sha256(sha256_value):
            return f"{row_label}.sha256 must be 64 lowercase hex"
        # Section 3.3 exposes editable surfaces as distinct harness configuration points.
        first_index = surface_sha256s.get(str(sha256_value))
        if first_index is not None:
            return (
                f"{label}.surfaces duplicate editable surface: surface {index} "
                f"repeats surface {first_index} sha256 {sha256_value} "
                f"(name={surface.get('name')})"
            )
        surface_sha256s[str(sha256_value)] = index
    return None


def _held_in_failure_patterns_block(value: object, label: str) -> str | None:
    if not isinstance(value, dict):
        return f"{label} must be an object"
    if (error := _reject_unknown_fields(value, _HELD_IN_FAILURE_PATTERNS_FIELDS, label)) is not None:
        return error
    patterns, error = _object_list(value, "patterns", label)
    if error is not None:
        return error
    if value.get("pattern_count") != len(patterns):
        return f"{label} pattern_count must match patterns"
    presentation_orders: list[int] = []
    presentation_rank_rows: list[tuple[str, int, int]] = []
    presentation_order_seen = False
    failure_signatures: set[tuple[str | None, str | None, str]] = set()
    task_id_clusters: dict[str, str] = {}
    for index, pattern in enumerate(patterns):
        row_label = f"{label}.patterns[{index}]"
        if (error := _reject_unknown_fields(pattern, _HELD_IN_FAILURE_PATTERN_FIELDS, row_label)) is not None:
            return error
        if not _non_empty_str(pattern.get("cluster_id")):
            return f"{row_label}.cluster_id must be a non-empty string"
        cluster_id = str(pattern["cluster_id"])
        size = pattern.get("size")
        if not isinstance(size, int) or size < 1:
            return f"{row_label}.size must be a positive integer"
        if (error := _non_empty_unique_string_list(pattern, "task_ids", row_label)) is not None:
            return error
        # Section 3.2 exact-match clustering assigns one failing task to one signature cluster.
        for task_id in cast(list[str], pattern["task_ids"]):
            previous_cluster_id = task_id_clusters.get(task_id)
            if previous_cluster_id is not None:
                return (
                    f"{label}.patterns task-id overlap violation: task {task_id} "
                    f"appears in clusters {previous_cluster_id} and {cluster_id}"
                )
            task_id_clusters[task_id] = cluster_id
        failure_category = pattern.get("failure_category")
        if failure_category is not None:
            if not isinstance(failure_category, str) or failure_category not in _TASK_FAILURE_CATEGORIES:
                categories = ", ".join(sorted(_TASK_FAILURE_CATEGORIES))
                return f"{row_label}.failure_category must be one of {categories}, or null"
        if not _sha256(pattern.get("mechanism_sha256")):
            return f"{row_label}.mechanism_sha256 must be 64 lowercase hex"
        causal_status_sha256 = pattern.get("causal_status_sha256")
        if causal_status_sha256 is not None and not _sha256(causal_status_sha256):
            return f"{row_label}.causal_status_sha256 must be 64 lowercase hex or null"
        mechanism_sha256 = str(pattern["mechanism_sha256"])
        signature = (
            failure_category,
            cast(str | None, causal_status_sha256),
            mechanism_sha256,
        )
        if signature in failure_signatures:
            return f"{row_label} duplicate failure signature (category, causal_status, mechanism)"
        failure_signatures.add(signature)
        shared_symptoms_sha256 = pattern.get("shared_symptoms_sha256")
        if shared_symptoms_sha256 is not None and not _sha256(shared_symptoms_sha256):
            return f"{row_label}.shared_symptoms_sha256 must be 64 lowercase hex or null"
        verifier_evidence_sha256 = pattern.get("verifier_evidence_sha256")
        if verifier_evidence_sha256 is not None and not _sha256(verifier_evidence_sha256):
            return f"{row_label}.verifier_evidence_sha256 must be 64 lowercase hex or null"
        presentation_order = pattern.get("presentation_order")
        if presentation_order is not None:
            presentation_order_seen = True
            if (
                not isinstance(presentation_order, int)
                or isinstance(presentation_order, bool)
                or presentation_order < 0
            ):
                return f"{row_label}.presentation_order must be a non-negative integer or null"
            presentation_orders.append(presentation_order)
            presentation_rank_rows.append((cluster_id, size, presentation_order))
        actionability_hint_sha256 = pattern.get("actionability_hint_sha256")
        if actionability_hint_sha256 is not None and not _sha256(actionability_hint_sha256):
            return f"{row_label}.actionability_hint_sha256 must be 64 lowercase hex or null"
    if presentation_order_seen:
        if len(presentation_orders) != len(patterns):
            return f"{label}.patterns must all declare presentation_order when any pattern declares it"
        if sorted(presentation_orders) != list(range(len(patterns))):
            return f"{label}.patterns presentation_order must be a contiguous permutation from 0"
        # Section 3.2 orders clusters by support and estimated actionability:
        # support wins when sizes differ; equal-size ties remain actionability-led.
        for left_cluster_id, left_size, left_order in presentation_rank_rows:
            for right_cluster_id, right_size, right_order in presentation_rank_rows:
                if left_size > right_size and left_order > right_order:
                    return (
                        f"{label}.patterns support-rank ordering violation: cluster "
                        f"{left_cluster_id} (size={left_size}) must precede cluster "
                        f"{right_cluster_id} (size={right_size})"
                    )
    return None


def _passing_behavior_summaries_block(value: object, label: str) -> str | None:
    if not isinstance(value, dict):
        return f"{label} must be an object"
    if (error := _reject_unknown_fields(value, _PASSING_BEHAVIOR_SUMMARIES_FIELDS, label)) is not None:
        return error
    summaries, error = _object_list(value, "summaries", label)
    if error is not None:
        return error
    if value.get("summary_count") != len(summaries):
        return f"{label} summary_count must match summaries"
    for index, summary in enumerate(summaries):
        row_label = f"{label}.summaries[{index}]"
        if (error := _reject_unknown_fields(summary, _PASSING_BEHAVIOR_SUMMARY_FIELDS, row_label)) is not None:
            return error
        if (error := _non_empty_unique_string_list(summary, "task_ids", row_label)) is not None:
            return error
        if not _sha256(summary.get("task_id_set_sha256")):
            return f"{row_label}.task_id_set_sha256 must be 64 lowercase hex"
        if not _sha256(summary.get("preserved_behavior_sha256")):
            return f"{row_label}.preserved_behavior_sha256 must be 64 lowercase hex"
    return None


def _previous_attempted_edits_block(value: object, label: str) -> str | None:
    if not isinstance(value, dict):
        return f"{label} must be an object"
    if (error := _reject_unknown_fields(value, _PREVIOUS_ATTEMPTED_EDITS_FIELDS, label)) is not None:
        return error
    edits, error = _object_list(value, "edits", label)
    if error is not None:
        return error
    if value.get("edit_count") != len(edits):
        return f"{label} edit_count must match edits"
    previous_edit_signatures: dict[tuple[int, str, str], int] = {}
    for index, edit in enumerate(edits):
        row_label = f"{label}.edits[{index}]"
        if (error := _reject_unknown_fields(edit, _PREVIOUS_ATTEMPTED_EDIT_FIELDS, row_label)) is not None:
            return error
        round_index = edit.get("round_index")
        if not isinstance(round_index, int) or round_index < 0:
            return f"{row_label}.round_index must be a non-negative integer"
        proposal_round_index = edit.get("proposal_round_index")
        if not isinstance(proposal_round_index, int) or proposal_round_index < 0:
            return f"{row_label}.proposal_round_index must be a non-negative integer"
        if not _non_empty_str(edit.get("surface")):
            return f"{row_label}.surface must be a non-empty string"
        if not _non_empty_str(edit.get("decision")):
            return f"{row_label}.decision must be a non-empty string"
        targeted_mechanism_sha256 = edit.get("targeted_mechanism_sha256")
        if not _sha256(targeted_mechanism_sha256):
            return f"{row_label}.targeted_mechanism_sha256 must be 64 lowercase hex"
        causal_status_sha256 = edit.get("causal_status_sha256")
        if causal_status_sha256 is not None and not _sha256(causal_status_sha256):
            return f"{row_label}.causal_status_sha256 must be 64 lowercase hex or null"
        edited_surface_sha256 = edit.get("edited_surface_sha256")
        if not _sha256(edited_surface_sha256):
            return f"{row_label}.edited_surface_sha256 must be 64 lowercase hex"
        signature = (
            proposal_round_index,
            str(targeted_mechanism_sha256),
            str(edited_surface_sha256),
        )
        first_index = previous_edit_signatures.get(signature)
        if first_index is not None:
            return (
                f"{label}.edits duplicate previous-attempted-edit signature: "
                f"edit {index} repeats edit {first_index} "
                f"({proposal_round_index}, {targeted_mechanism_sha256}, {edited_surface_sha256})"
            )
        previous_edit_signatures[signature] = index
        audit_decision = edit.get("audit_decision")
        if audit_decision not in {"accepted", "rejected", "invalid"}:
            return f"{row_label}.audit_decision must be accepted, rejected, or invalid"
        audit_decision_reason = edit.get("audit_decision_reason")
        if not isinstance(audit_decision_reason, str):
            return f"{row_label}.audit_decision_reason must be a string"
        if audit_decision != "accepted" and not audit_decision_reason:
            return f"{row_label}.audit_decision_reason must be non-empty unless audit_decision is accepted"
    return None


def _base_json(path: Path, label: str) -> tuple[dict[str, Any], str | None]:
    if path.suffix != ".json":
        return {}, f"{label} must be JSON"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, f"{label} must be valid JSON"
    if not isinstance(data, dict):
        return {}, f"{label} must be a JSON object"
    if data.get("reproduction_claimed") is not False:
        return {}, f"{label} reproduction_claimed must be false"
    return data, None


def _require_ok_live(data: Mapping[str, Any], label: str) -> str | None:
    if data.get("ok") is not True:
        return f"{label} ok field must be true"
    return _require_live(data, label)


def _require_live(data: Mapping[str, Any], label: str) -> str | None:
    if data.get("mode") != "live":
        return f"{label} mode must be live"
    return None


def _string_list(
    data: Mapping[str, Any],
    key: str,
    label: str,
) -> tuple[tuple[str, ...], str | None]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return (), f"{label} {key} must be a list of strings"
    return tuple(value), None


def _non_empty_unique_string_list(data: Mapping[str, Any], key: str, label: str) -> str | None:
    values, error = _string_list(data, key, label)
    if error is not None:
        return error
    if not values or any(not value for value in values):
        return f"{label}.{key} must contain non-empty strings"
    if len(set(values)) != len(values):
        return f"{label}.{key} must not contain duplicates"
    return None


def _object_list(
    data: Mapping[str, Any],
    key: str,
    label: str,
) -> tuple[tuple[dict[str, Any], ...], str | None]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return (), f"{label} {key} must be a list of objects"
    return tuple(value), None


def _required_checks_pass(data: Mapping[str, Any], label: str) -> str | None:
    checks = data.get("checks")
    if checks is None:
        return None
    if not isinstance(checks, list):
        return f"{label} checks must be a list when present"
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            return f"{label} checks[{index}] must be an object"
        if check.get("required") is True and check.get("status") != "pass":
            return f"{label} required check {check.get('name', index)} must pass"
    return None


def _reject_unknown_fields(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> str | None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        return f"{label} has unknown field(s): {', '.join(unknown)}"
    return None


def _normal_model_backends(values: tuple[str, ...]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in values:
        key = value.lower().replace("_", "-").replace(" ", "-")
        if key in {"minimax", "minimax-m2.5", "minimax-m25", "minimax-m2-5"}:
            normalized.add("minimax")
        elif key in {"qwen", "qwen3.5-35b-a3b", "qwen3-5-35b-a3b", "qwen35-35b-a3b"}:
            normalized.add("qwen")
        elif key in {"glm", "glm-5", "glm5", "glm-5.2", "glm-52", "glm52"}:
            normalized.add("glm")
        else:
            normalized.add(key)
    return frozenset(normalized)


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_image_digest(value: object) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    if not value.startswith(prefix):
        return False
    digest = value.removeprefix(prefix)
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


REPRODUCTION_ARTIFACT_CLASS_VALIDATORS: Mapping[str, ArtifactValidator] = {
    "live_terminal_bench_split_manifest": _live_terminal_bench_split_manifest,
    "live_two_repeat_evaluation_report": _live_two_repeat_evaluation_report,
    "fixed_protocol_config": _fixed_protocol_config,
    "live_harbor_preflight_report": _live_harbor_preflight_report,
    "container_image_trust_report": _container_image_trust_report,
    "model_backend_preflight_report": _model_backend_preflight_report,
    "proposer_llm_request_log": _proposer_llm_request_log,
    "proposer_context_manifest": _proposer_context_manifest,
    "proposal_validation_manifest": _proposal_validation_manifest,
    "network_resource_controls_attestation": _network_resource_controls_attestation,
    "live_harbor_audit": _live_harbor_audit,
    "audit_verify_report": _audit_verify_report,
    "release_candidate_evidence": _release_candidate_evidence,
}
