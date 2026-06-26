from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from self_harness._artifact_shapes import artifact_shape_error_from_payload
from self_harness.capture_manifest import (
    CAPTURE_MANIFEST_SCHEMA_VERSION,
    CaptureManifestEntry,
)
from self_harness.reproduction_readiness import ReproductionRequirement
from self_harness.types import stable_json_dumps

PLANNED_SOURCE_FIELDS = frozenset({"provider", "captured_after", "captured_before", "operator_label"})
SIGNING_CUSTODY_FIELDS = frozenset({"provider", "key_id", "fingerprint"})
PAPER_MODEL_BACKENDS = frozenset({"minimax", "qwen", "glm"})


class CaptureManifestBuildError(ValueError):
    """Raised when an operator capture manifest cannot be authored safely."""


@dataclass(frozen=True)
class CaptureManifestDocument:
    schema_version: str
    manifest_id: str
    bundle_id: str
    operator_label: str
    created_at: str
    planned_run: dict[str, object]
    signing_custody: dict[str, str]
    entries: tuple[CaptureManifestEntry, ...]
    reproduction_claimed: bool = False


def build_capture_manifest(
    *,
    requirements: Sequence[ReproductionRequirement],
    manifest_id: str,
    bundle_id: str,
    operator_label: str,
    created_at: str,
    run_id: str,
    mode: str = "live",
    benchmark_protocol: str = "terminal-bench@2.0",
    model_backends: Sequence[str],
    evaluator: str,
    tool_set: str,
    tool_budget: Mapping[str, object],
    outbound_bandwidth_cap_bps: int,
    mirrored_resources: Sequence[str],
    signing_custody: Mapping[str, str],
    source_defaults: Mapping[str, str],
    entry_sources: Mapping[str, Mapping[str, str]] | None = None,
    planned_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
    entry_notes: Mapping[str, str] | None = None,
    strict_shapes: bool = True,
) -> CaptureManifestDocument:
    """Build a deterministic offline capture manifest for a planned live evidence run."""

    manifest_id = _required_string(manifest_id, "manifest_id")
    bundle_id = _required_string(bundle_id, "bundle_id")
    operator_label = _required_string(operator_label, "operator_label")
    created_at = _required_string(created_at, "created_at")
    run_id = _required_string(run_id, "run_id")
    evaluator = _required_string(evaluator, "evaluator")
    tool_set = _required_string(tool_set, "tool_set")
    if mode != "live":
        raise CaptureManifestBuildError("planned run mode must be live")
    if benchmark_protocol != "terminal-bench@2.0":
        raise CaptureManifestBuildError("planned benchmark_protocol must be terminal-bench@2.0")
    model_backends_tuple = _model_backends(model_backends)
    if _normal_model_backends(model_backends_tuple) != PAPER_MODEL_BACKENDS:
        raise CaptureManifestBuildError("model_backends must cover MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5.2")
    tool_budget_payload = _tool_budget(tool_budget)
    mirrored = _mirrored_resources(mirrored_resources)
    cap = _positive_int(outbound_bandwidth_cap_bps, "outbound_bandwidth_cap_bps")
    custody = _signing_custody(signing_custody)
    source = _planned_source(source_defaults, label="source defaults", require_all=True)

    required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
    required_set = frozenset(required_classes)
    entry_sources = entry_sources or {}
    planned_artifacts = planned_artifacts or {}
    entry_notes = entry_notes or {}
    _reject_unknown_classes(entry_sources, required_set, label="entry source")
    _reject_unknown_classes(planned_artifacts, required_set, label="planned artifact")
    _reject_unknown_classes(entry_notes, required_set, label="entry note")

    planned_run: dict[str, object] = {
        "run_id": run_id,
        "mode": "live",
        "benchmark_protocol": "terminal-bench@2.0",
        "model_backends": list(model_backends_tuple),
        "evaluator": evaluator,
        "tool_budget": tool_budget_payload,
        "outbound_bandwidth_cap_bps": cap,
        "mirrored_resources": list(mirrored),
    }

    entries: list[CaptureManifestEntry] = []
    for artifact_class in required_classes:
        entry_source = dict(source)
        entry_source.update(
            _planned_source(
                entry_sources.get(artifact_class, {}),
                label=f"{artifact_class} source",
                require_all=False,
            )
        )
        planned_artifact = dict(
            planned_artifacts.get(
                artifact_class,
                _planned_artifact_stub(
                    artifact_class,
                    capture_run_id=run_id,
                    model_backends=model_backends_tuple,
                    evaluator=evaluator,
                    tool_set=tool_set,
                    tool_budget=tool_budget_payload,
                    outbound_bandwidth_cap_bps=cap,
                    mirrored_resources=mirrored,
                ),
            )
        )
        if planned_artifact.get("reproduction_claimed") is not False:
            raise CaptureManifestBuildError(
                f"planned artifact for class {artifact_class} must set reproduction_claimed=false"
            )
        if strict_shapes:
            shape_error = artifact_shape_error_from_payload(artifact_class, planned_artifact)
            if shape_error is not None:
                raise CaptureManifestBuildError(
                    f"invalid planned artifact for class {artifact_class}: {shape_error}"
                )
        entries.append(
            CaptureManifestEntry(
                required_artifact_class=artifact_class,
                planned_source=entry_source,
                planned_artifact=planned_artifact,
                notes=entry_notes.get(artifact_class),
            )
        )

    return CaptureManifestDocument(
        schema_version=CAPTURE_MANIFEST_SCHEMA_VERSION,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        operator_label=operator_label,
        created_at=created_at,
        planned_run=planned_run,
        signing_custody=custody,
        entries=tuple(entries),
        reproduction_claimed=False,
    )


def capture_manifest_document_to_jsonable(document: CaptureManifestDocument) -> dict[str, object]:
    return {
        "schema_version": document.schema_version,
        "manifest_id": document.manifest_id,
        "bundle_id": document.bundle_id,
        "operator_label": document.operator_label,
        "created_at": document.created_at,
        "planned_run": dict(document.planned_run),
        "signing_custody": dict(document.signing_custody),
        "entries": [_entry_to_jsonable(entry) for entry in document.entries],
        "reproduction_claimed": document.reproduction_claimed,
    }


def write_capture_manifest_document(document: CaptureManifestDocument, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(capture_manifest_document_to_jsonable(document)) + "\n", encoding="utf-8")


def load_planned_artifact(path: Path, *, artifact_class: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaptureManifestBuildError(f"missing planned artifact for class {artifact_class}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CaptureManifestBuildError(f"invalid planned artifact JSON for class {artifact_class}: {path}") from exc
    if not isinstance(data, dict):
        raise CaptureManifestBuildError(f"planned artifact for class {artifact_class} must be a JSON object")
    return cast(dict[str, Any], data)


def _entry_to_jsonable(entry: CaptureManifestEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "required_artifact_class": entry.required_artifact_class,
        "planned_source": dict(entry.planned_source),
        "planned_artifact": dict(entry.planned_artifact),
    }
    if entry.notes is not None:
        payload["notes"] = entry.notes
    return payload


def _planned_artifact_stub(
    artifact_class: str,
    *,
    capture_run_id: str,
    model_backends: tuple[str, ...],
    evaluator: str,
    tool_set: str,
    tool_budget: Mapping[str, object],
    outbound_bandwidth_cap_bps: int,
    mirrored_resources: tuple[str, ...],
) -> dict[str, Any]:
    held_in_ids = [f"planned-held-in-{index:02d}" for index in range(32)]
    held_out_ids = [f"planned-held-out-{index:02d}" for index in range(32)]
    round_count = len(model_backends)
    held_in_failing_ids: list[str] = []
    round_failing_ids = {
        index: held_in_ids[index:round_count]
        for index in range(round_count)
    }
    round_accepted_failing_ids = {
        index: round_failing_ids[index][1:]
        for index in range(round_count)
    }
    round_passing_ids = {
        index: [
            task_id
            for task_id in held_in_ids
            if task_id not in set(round_failing_ids[index])
        ]
        for index in range(round_count)
    }
    round_baseline_task_outcomes = {
        index: _planned_task_outcomes(
            held_in_ids=held_in_ids,
            held_out_ids=held_out_ids,
            held_in_failing_ids=round_failing_ids[index],
        )
        for index in range(round_count)
    }
    round_accepted_task_outcomes = {
        index: _planned_task_outcomes(
            held_in_ids=held_in_ids,
            held_out_ids=held_out_ids,
            held_in_failing_ids=round_accepted_failing_ids[index],
        )
        for index in range(round_count)
    }
    harness_state_hashes = [
        _stable_artifact_sha256(
            {
                "capture_run_id": capture_run_id,
                "planned_harness_state_index": index,
            }
        )
        for index in range(round_count + 1)
    ]
    planned_eval_ids = [*held_in_ids, *held_out_ids]
    planned_eval_rows: list[dict[str, Any]] = [
        {
            "task_id": task_id,
            "attempts": [
                {"pass": task_id not in held_in_failing_ids},
                {"pass": task_id not in held_in_failing_ids},
            ],
        }
        for task_id in planned_eval_ids
    ]
    planned_pass_count = sum(
        1
        for row in planned_eval_rows
        for attempt in row["attempts"]
        if attempt["pass"] is True
    )
    common: dict[str, Any] = {"schema_version": "1.0", "reproduction_claimed": False}
    captured_common: dict[str, Any] = {**common, "capture_run_id": capture_run_id}
    fixed_protocol_hash = _stable_artifact_sha256(
        _planned_fixed_protocol_stub(
            capture_run_id=capture_run_id,
            model_backends=model_backends,
            evaluator=evaluator,
            tool_set=tool_set,
            tool_budget=tool_budget,
        )
    )
    if artifact_class == "live_terminal_bench_split_manifest":
        return {
            **captured_common,
            "mode": "live",
            "source": "harbor",
            "total_cases": 64,
            "held_in_count": len(held_in_ids),
            "held_out_count": len(held_out_ids),
            "held_in_task_ids": held_in_ids,
            "held_out_task_ids": held_out_ids,
            "fixed_across_variants": True,
            "harbor_version": "planned-live-harbor-version",
        }
    if artifact_class == "live_two_repeat_evaluation_report":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "attempts_per_task": 2,
            "per_task_attempts": planned_eval_rows,
            "task_count": len(planned_eval_ids),
            "attempt_count": len(planned_eval_ids) * 2,
            "pass_count": planned_pass_count,
            "fail_count": (len(planned_eval_ids) * 2) - planned_pass_count,
            "fixed_protocol_sha256": fixed_protocol_hash,
        }
    if artifact_class == "fixed_protocol_config":
        return _planned_fixed_protocol_stub(
            capture_run_id=capture_run_id,
            model_backends=model_backends,
            evaluator=evaluator,
            tool_set=tool_set,
            tool_budget=tool_budget,
        )
    if artifact_class == "live_harbor_preflight_report":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "harbor_reachable": True,
            "harbor_version": "planned-live-harbor-version",
        }
    if artifact_class == "container_image_trust_report":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "policy": "digest-bound",
            "all_digest_bound": True,
            "images": [{"name": "registry.example/terminal-bench/agent", "digest": "sha256:" + "0" * 64}],
        }
    if artifact_class == "model_backend_preflight_report":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "backends": list(model_backends),
            "checks": [
                {
                    "name": f"{backend}_backend_reachable",
                    "backend": backend,
                    "status": "pass",
                    "required": True,
                }
                for backend in model_backends
            ],
            "report_hash": "0" * 64,
        }
    if artifact_class == "proposer_llm_request_log":
        model_names = {
            "minimax": "MiniMax-M2.5",
            "qwen": "Qwen3.5-35B-A3B",
            "glm": "GLM-5.2",
        }
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "round_count": len(model_backends),
            "rounds": [
                {
                    "round_index": index,
                    "backend": backend,
                    "model": model_names.get(backend, backend),
                    "request_sha256": "0" * 64,
                    "response_sha256": "1" * 64,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "attempted_proposals": 2,
                    "committed_proposals": 1,
                }
                for index, backend in enumerate(model_backends)
            ],
        }
    if artifact_class == "proposer_context_manifest":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "round_count": len(model_backends),
            "rounds": [
                {
                    "round_index": index,
                    "editable_surfaces": {
                        "surface_count": 1,
                        "surfaces": [
                            {
                                "kind": "prompt",
                                "name": "system_prompt",
                                "sha256": "0" * 64,
                            }
                        ],
                    },
                    "held_in_failure_patterns": {
                        "pattern_count": 1,
                        "patterns": [
                            {
                                "cluster_id": f"planned-pattern-{index:02d}",
                                "size": len(round_failing_ids[index]),
                                "task_ids": round_failing_ids[index],
                                "mechanism_sha256": "1" * 64,
                                "causal_status_sha256": _planned_causal_status_sha256("agent-causal"),
                                "shared_symptoms_sha256": _planned_evidence_sha256(
                                    "shared_symptoms",
                                    ["assertion mismatch", "same verifier failure"],
                                ),
                                "verifier_evidence_sha256": _planned_evidence_sha256(
                                    "verifier_evidence",
                                    ["terminal-bench verifier failed"],
                                ),
                                "presentation_order": 0,
                                "actionability_hint_sha256": _planned_evidence_sha256(
                                    "actionability_hint",
                                    "high support, high actionability",
                                ),
                            }
                        ],
                    },
                    "passing_behavior_summaries": {
                        "summary_count": 1,
                        "summaries": [
                            {
                                "task_ids": round_passing_ids[index],
                                "task_id_set_sha256": _planned_task_id_set_sha256(round_passing_ids[index]),
                                "preserved_behavior_sha256": "3" * 64,
                            }
                        ],
                    },
                    "previous_attempted_edits": _planned_previous_attempted_edits(index),
                }
                for index, _backend in enumerate(model_backends)
            ],
        }
    if artifact_class == "proposal_validation_manifest":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "round_count": len(model_backends),
            "fixed_protocol_sha256": fixed_protocol_hash,
            "rounds": [
                {
                    "round_index": index,
                    "harness_before_sha256": harness_state_hashes[index],
                    "harness_after_sha256": harness_state_hashes[index + 1],
                    "proposer_round_request_sha256": "0" * 64,
                    "proposer_round_response_sha256": "1" * 64,
                    "baseline_split_outcomes": _planned_split_outcomes(
                        held_in_passed=len(round_passing_ids[index]),
                        held_in_total=len(held_in_ids),
                        held_out_passed=len(held_out_ids),
                        held_out_total=len(held_out_ids),
                        task_outcomes=round_baseline_task_outcomes[index],
                    ),
                    "candidates": [
                        {
                            "proposal_id": f"planned-proposal-{index:02d}-0",
                            "proposal_round_index": index,
                            "pattern_id": f"planned-pattern-{index:02d}",
                            "changed_surfaces": ["system_prompt"],
                            "edited_surface_sha256": "0" * 64,
                            "targeted_mechanism_sha256": "1" * 64,
                            "summary_sha256": _stable_artifact_sha256(
                                {
                                    "rationale": "planned validation candidate",
                                    "expected_effect": "operator live proposal validation",
                                    "regression_risks": [],
                                }
                            ),
                            "split_outcomes": _planned_split_outcomes(
                                held_in_passed=len(held_in_ids) - len(round_accepted_failing_ids[index]),
                                held_in_total=len(held_in_ids),
                                held_out_passed=len(held_out_ids),
                                held_out_total=len(held_out_ids),
                                task_outcomes=round_accepted_task_outcomes[index],
                            ),
                            "audit_decision": "accepted",
                            "validation_failure_category": None,
                            "decision_reason": "planned candidate accepted by live validation",
                            "rejection_reason": None,
                        },
                        {
                            "proposal_id": f"planned-proposal-{index:02d}-1",
                            "proposal_round_index": index,
                            "pattern_id": f"planned-pattern-{index:02d}",
                            "changed_surfaces": [],
                            "edited_surface_sha256": _stable_artifact_sha256({"changed_surfaces": []}),
                            "targeted_mechanism_sha256": "1" * 64,
                            "summary_sha256": _stable_artifact_sha256(
                                {
                                    "rationale": "planned invalid no-surface validation candidate",
                                    "expected_effect": "operator live proposal validation rejects no-op edits",
                                    "regression_risks": [],
                                }
                            ),
                            "split_outcomes": _planned_split_outcomes(
                                held_in_passed=len(round_passing_ids[index]),
                                held_in_total=len(held_in_ids),
                                held_out_passed=len(held_out_ids),
                                held_out_total=len(held_out_ids),
                                task_outcomes=round_baseline_task_outcomes[index],
                            ),
                            "audit_decision": "invalid",
                            "validation_failure_category": "no_editable_surface",
                            "decision_reason": "planned candidate did not modify an editable surface",
                            "rejection_reason": "planned candidate did not modify an editable surface",
                        }
                    ],
                    "committed_proposal_ids": [f"planned-proposal-{index:02d}-0"],
                    "merge_decision": "none",
                }
                for index, _backend in enumerate(model_backends)
            ],
        }
    if artifact_class == "network_resource_controls_attestation":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "outbound_bandwidth_cap_bps": outbound_bandwidth_cap_bps,
            "mirrored_resources": list(mirrored_resources),
        }
    if artifact_class == "live_harbor_audit":
        return {
            **captured_common,
            "ok": True,
            "mode": "live",
            "trial_artifacts": [
                {
                    "task_id": task_id,
                    "captured": True,
                    "verifier_outcome": "pass" if task_id not in held_in_failing_ids else "fail",
                    "attempts": [
                        {
                            "attempt_index": 0,
                            "pass": task_id not in held_in_failing_ids,
                            "terminal_cause": None,
                        },
                        {
                            "attempt_index": 1,
                            "pass": task_id not in held_in_failing_ids,
                            "terminal_cause": None,
                        },
                    ],
                }
                for task_id in planned_eval_ids
            ],
            "fixed_protocol_sha256": fixed_protocol_hash,
        }
    if artifact_class == "audit_verify_report":
        return {
            **common,
            "ok": True,
            "mode": "live",
            "held_out_leakage": False,
            "proposer_evidence_inspected": True,
            "changed_surfaces_recorded": True,
            "evaluation_repeats_recorded": True,
            "rejected_reasons_recorded": True,
            "report_hash": "0" * 64,
        }
    if artifact_class == "release_candidate_evidence":
        return {
            **common,
            "ok": True,
            "decision": "ready",
            "evidence_sha256": "0" * 64,
            "gates": [
                {"name": "audit_integrity", "status": "pass", "metadata": {"report_hash": "0" * 64}},
                {"name": "provenance_manifest", "status": "pass", "metadata": {"artifact_count": 1}},
                {"name": "attestation", "status": "pass", "metadata": {"report_hash": "0" * 64}},
                {
                    "name": "reproduction_readiness",
                    "status": "pass",
                    "metadata": {"reproduction_ready": True, "report_hash": "0" * 64},
                },
            ],
        }
    raise CaptureManifestBuildError(f"unsupported required_artifact_class: {artifact_class}")


def _planned_fixed_protocol_stub(
    *,
    capture_run_id: str,
    model_backends: tuple[str, ...],
    evaluator: str,
    tool_set: str,
    tool_budget: Mapping[str, object],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "reproduction_claimed": False,
        "mode": "live",
        "benchmark_protocol": "terminal-bench@2.0",
        "capture_run_id": capture_run_id,
        "models": list(model_backends),
        "evaluator": evaluator,
        "tool_set": tool_set,
        "decoding_budget": dict(tool_budget),
        "self_harness_rounds": len(model_backends),
        "proposal_width": 2,
        "fixed_across_variants": True,
    }


def _planned_previous_attempted_edits(round_index: int) -> dict[str, object]:
    if round_index == 0:
        return {"edit_count": 0, "edits": []}
    return {
        "edit_count": 1,
        "edits": [
            {
                "round_index": round_index - 1,
                "surface": "system_prompt",
                "decision": "accepted",
                "proposal_round_index": round_index - 1,
                "targeted_mechanism_sha256": "1" * 64,
                "causal_status_sha256": _planned_causal_status_sha256("agent-causal"),
                "edited_surface_sha256": "0" * 64,
                "audit_decision": "accepted",
                "audit_decision_reason": "",
            }
        ],
    }


def _planned_split_outcomes(
    *,
    held_in_passed: int,
    held_in_total: int,
    held_out_passed: int,
    held_out_total: int,
    task_outcomes: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "held_in_passed": held_in_passed,
        "held_in_total": held_in_total,
        "held_out_passed": held_out_passed,
        "held_out_total": held_out_total,
        "evaluation_repeats": 2,
    }
    if task_outcomes is not None:
        payload["task_outcomes"] = [dict(row) for row in task_outcomes]
    return payload


def _planned_task_outcomes(
    *,
    held_in_ids: Sequence[str],
    held_out_ids: Sequence[str],
    held_in_failing_ids: Sequence[str],
) -> list[dict[str, object]]:
    failing = set(held_in_failing_ids)
    return [
        {"task_id": task_id, "split": "held_in", "pass": task_id not in failing}
        for task_id in held_in_ids
    ] + [{"task_id": task_id, "split": "held_out", "pass": True} for task_id in held_out_ids]


def _planned_task_id_set_sha256(task_ids: Sequence[str]) -> str:
    return sha256((stable_json_dumps({"task_ids": sorted(task_ids)}) + "\n").encode("utf-8")).hexdigest()


def _planned_causal_status_sha256(causal_status: str) -> str:
    return sha256((stable_json_dumps({"causal_status": causal_status}) + "\n").encode("utf-8")).hexdigest()


def _planned_evidence_sha256(key: str, values: Sequence[str] | str) -> str:
    payload: object = values if isinstance(values, str) else list(values)
    return sha256((stable_json_dumps({key: payload}) + "\n").encode("utf-8")).hexdigest()


def _stable_artifact_sha256(payload: Mapping[str, object]) -> str:
    return sha256((stable_json_dumps(payload) + "\n").encode("utf-8")).hexdigest()


def _reject_unknown_classes(
    values: Mapping[str, object],
    required_set: frozenset[str],
    *,
    label: str,
) -> None:
    unknown = sorted(set(values) - required_set)
    if unknown:
        raise CaptureManifestBuildError(f"unknown {label} class(es): {', '.join(unknown)}")


def _planned_source(source: Mapping[str, str], *, label: str, require_all: bool) -> dict[str, str]:
    unknown = sorted(set(source) - PLANNED_SOURCE_FIELDS)
    if unknown:
        raise CaptureManifestBuildError(f"{label} has unknown field(s): {', '.join(unknown)}")
    result: dict[str, str] = {}
    for key, value in source.items():
        result[key] = _required_string(value, f"{label}.{key}")
    if require_all:
        missing = sorted(PLANNED_SOURCE_FIELDS - set(result))
        if missing:
            raise CaptureManifestBuildError(f"{label} missing field(s): {', '.join(missing)}")
    if "captured_after" in result and "captured_before" in result:
        if result["captured_after"] > result["captured_before"]:
            raise CaptureManifestBuildError(f"{label} captured_after must not exceed captured_before")
    return result


def _signing_custody(custody: Mapping[str, str]) -> dict[str, str]:
    unknown = sorted(set(custody) - SIGNING_CUSTODY_FIELDS)
    if unknown:
        raise CaptureManifestBuildError(f"signing_custody has unknown field(s): {', '.join(unknown)}")
    result = {"provider": _required_string(custody.get("provider", ""), "signing_custody.provider")}
    key_id = custody.get("key_id")
    if key_id is not None:
        result["key_id"] = key_id
    fingerprint = custody.get("fingerprint")
    if fingerprint is not None:
        if not _is_sha256(fingerprint):
            raise CaptureManifestBuildError("signing_custody.fingerprint must be a lowercase sha256 digest")
        result["fingerprint"] = fingerprint
    return result


def _tool_budget(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise CaptureManifestBuildError("tool_budget must be a non-empty object")
    return dict(value)


def _model_backends(values: Sequence[str]) -> tuple[str, ...]:
    if not values or not all(isinstance(value, str) and value for value in values):
        raise CaptureManifestBuildError("model_backends must be non-empty strings")
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise CaptureManifestBuildError("duplicate model_backends: " + ", ".join(duplicates))
    return tuple(values)


def _mirrored_resources(values: Sequence[str]) -> tuple[str, ...]:
    if not values or not all(isinstance(value, str) and value for value in values):
        raise CaptureManifestBuildError("mirrored_resources must contain non-empty strings")
    return tuple(values)


def _positive_int(value: int, label: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise CaptureManifestBuildError(f"{label} must be a positive integer")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureManifestBuildError(f"{label} must be a non-empty string")
    return value


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


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
