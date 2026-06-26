import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from self_harness._artifact_shapes import artifact_shape_error_from_payload
from self_harness.capture_extract import (
    EXTRACTABLE_ARTIFACT_CLASSES,
    CaptureExtractError,
    extract_artifact_from_paths,
)
from self_harness.cli import main
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "capture_extract.py"
DIGEST = "sha256:" + "c" * 64
CHILD_DIGEST = "sha256:" + "f" * 64
IMAGE = "harbor.example/terminal-bench/agents/verifier"


def test_capture_extract_outputs_validate_against_artifact_shapes(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    for artifact_class in sorted(EXTRACTABLE_ARTIFACT_CLASSES):
        payload = extract_artifact_from_paths(artifact_class, **paths)

        assert artifact_shape_error_from_payload(artifact_class, payload) is None
        assert payload["reproduction_claimed"] is False
        assert payload["mode"] == "live"
        assert payload["capture_run_id"] == "terminal-bench-2.0-live-fixture"


def test_capture_extract_rejects_empty_capture_run_id(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    paths["capture_run_id"] = ""

    for artifact_class in sorted(EXTRACTABLE_ARTIFACT_CLASSES):
        with pytest.raises(CaptureExtractError, match="capture-run|capture_run"):
            extract_artifact_from_paths(artifact_class, **paths)


def test_split_manifest_shape_requires_harbor_version(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    payload = extract_artifact_from_paths("live_terminal_bench_split_manifest", **paths)
    payload.pop("harbor_version")

    error = artifact_shape_error_from_payload("live_terminal_bench_split_manifest", payload)

    assert error == "live Terminal-Bench split manifest harbor_version must be a non-empty string"


def test_capture_extract_cli_writes_artifact(tmp_path: Path, capsys) -> None:
    paths = _fixture_paths(tmp_path)
    out = tmp_path / "two-repeat.json"

    code = main(
        [
            "capture-extract",
            "--class",
            "live_two_repeat_evaluation_report",
            "--capture-envelope",
            str(paths["capture_envelope"]),
            "--attempts-jsonl",
            str(paths["attempts_jsonl"]),
            "--fixed-protocol-result",
            str(paths["fixed_protocol_result"]),
            "--out",
            str(out),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["attempts_per_task"] == 2
    assert output["task_count"] == 2
    assert output["attempt_count"] == 4
    assert output["pass_count"] == 3
    assert output["fail_count"] == 1
    assert output["fixed_protocol_sha256"] == sha256(paths["fixed_protocol_result"].read_bytes()).hexdigest()
    assert artifact_shape_error_from_payload("live_two_repeat_evaluation_report", output) is None
    assert json.loads(out.read_text(encoding="utf-8")) == output


def test_capture_extract_script_writes_harbor_preflight(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    out = tmp_path / "harbor-preflight.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--class",
            "live_harbor_preflight_report",
            "--capture-run-id",
            str(paths["capture_run_id"]),
            "--harbor-discovery-result",
            str(paths["harbor_discovery_result"]),
            "--harbor-version",
            "2.10.0",
            "--out",
            str(out),
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["harbor_reachable"] is True
    assert artifact_shape_error_from_payload("live_harbor_preflight_report", payload) is None
    assert json.loads(out.read_text(encoding="utf-8")) == payload


def test_capture_extract_cli_writes_fixed_protocol_config(tmp_path: Path, capsys) -> None:
    paths = _fixture_paths(tmp_path)
    out = tmp_path / "fixed-protocol.json"

    code = main(
        [
            "capture-extract",
            "--class",
            "fixed_protocol_config",
            "--fixed-protocol-declaration",
            str(paths["fixed_protocol_declaration"]),
            "--out",
            str(out),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["benchmark_protocol"] == "terminal-bench@2.0"
    assert artifact_shape_error_from_payload("fixed_protocol_config", output) is None
    assert json.loads(out.read_text(encoding="utf-8")) == output


def test_capture_extract_rejects_unknown_raw_fields(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["harbor_discovery_result"])
    data["unexpected"] = "drift"
    paths["harbor_discovery_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="unknown field"):
        extract_artifact_from_paths("live_harbor_preflight_report", **paths)


def test_capture_extract_rejects_missing_digest(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["harbor_discovery_result"])
    data["discovered_images"][0]["digest"] = ""
    paths["harbor_discovery_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="digest"):
        extract_artifact_from_paths("container_image_trust_report", **paths)


def test_capture_extract_threads_harbor_child_digests_into_image_trust_report(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["harbor_discovery_result"])
    data["discovered_images"][0]["child_digests"] = [CHILD_DIGEST]
    paths["harbor_discovery_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    payload = extract_artifact_from_paths("container_image_trust_report", **paths)

    assert artifact_shape_error_from_payload("container_image_trust_report", payload) is None
    assert payload["images"][0]["child_digests"] == [CHILD_DIGEST]


def test_capture_extract_rejects_malformed_harbor_child_digest(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["harbor_discovery_result"])
    data["discovered_images"][0]["child_digests"] = ["sha256:not-a-real-digest"]
    paths["harbor_discovery_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="child_digests"):
        extract_artifact_from_paths("container_image_trust_report", **paths)


def test_capture_extract_rejects_wrong_attempt_count(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    paths["attempts_jsonl"].write_text(
        stable_json_dumps({"task_id": "task-a", "attempt_index": 0, "pass": True}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CaptureExtractError, match="exactly two attempts"):
        extract_artifact_from_paths("live_two_repeat_evaluation_report", **paths)


def test_capture_extract_rejects_missing_fixed_protocol_binding(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    paths.pop("fixed_protocol_result")

    with pytest.raises(CaptureExtractError, match="fixed-protocol-result"):
        extract_artifact_from_paths("live_two_repeat_evaluation_report", **paths)


def test_capture_extract_rejects_fixed_protocol_hash_mismatch(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    with pytest.raises(CaptureExtractError, match="does not match"):
        extract_artifact_from_paths(
            "live_harbor_audit",
            fixed_protocol_sha256="0" * 64,
            **paths,
        )


def test_live_harbor_audit_extracts_metadata_image_digest(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _rewrite_trial_metadata(paths["harbor_run_dir"], image_digest=DIGEST)

    payload = extract_artifact_from_paths("live_harbor_audit", **paths)

    assert artifact_shape_error_from_payload("live_harbor_audit", payload) is None
    assert {row["image_digest"] for row in payload["trial_artifacts"]} == {DIGEST}


def test_live_harbor_audit_rejects_malformed_metadata_image_digest(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _rewrite_trial_metadata(paths["harbor_run_dir"], image_digest="sha256:not-a-real-digest")

    with pytest.raises(CaptureExtractError, match="image_digest"):
        extract_artifact_from_paths("live_harbor_audit", **paths)


def test_live_harbor_audit_rejects_mixed_metadata_image_digest(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _rewrite_trial_metadata(paths["harbor_run_dir"], task_id="task-a", attempt=0, image_digest=DIGEST)

    with pytest.raises(CaptureExtractError, match="every attempt"):
        extract_artifact_from_paths("live_harbor_audit", **paths)


def test_two_repeat_shape_accepts_consistent_aggregate_counts(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    payload = extract_artifact_from_paths("live_two_repeat_evaluation_report", **paths)

    assert artifact_shape_error_from_payload("live_two_repeat_evaluation_report", payload) is None
    assert payload["task_count"] == 2
    assert payload["attempt_count"] == 4
    assert payload["pass_count"] == 3
    assert payload["fail_count"] == 1


def test_two_repeat_shape_rejects_task_count_drift(tmp_path: Path) -> None:
    payload = _two_repeat_payload(tmp_path)
    payload["task_count"] = 3

    error = artifact_shape_error_from_payload("live_two_repeat_evaluation_report", payload)

    assert error is not None
    assert "task_count must match" in error


def test_two_repeat_shape_rejects_attempt_count_drift(tmp_path: Path) -> None:
    payload = _two_repeat_payload(tmp_path)
    payload["attempt_count"] = 5

    error = artifact_shape_error_from_payload("live_two_repeat_evaluation_report", payload)

    assert error is not None
    assert "attempt_count must equal" in error


def test_two_repeat_shape_rejects_pass_count_drift(tmp_path: Path) -> None:
    payload = _two_repeat_payload(tmp_path)
    payload["pass_count"] = 2

    error = artifact_shape_error_from_payload("live_two_repeat_evaluation_report", payload)

    assert error is not None
    assert "pass_count must match" in error


def test_two_repeat_shape_rejects_fail_count_drift(tmp_path: Path) -> None:
    payload = _two_repeat_payload(tmp_path)
    payload["fail_count"] = 2

    error = artifact_shape_error_from_payload("live_two_repeat_evaluation_report", payload)

    assert error is not None
    assert "fail_count must equal" in error


def test_two_repeat_shape_rejects_unknown_aggregate_field(tmp_path: Path) -> None:
    payload = _two_repeat_payload(tmp_path)
    payload["pass_rate"] = 0.75

    error = artifact_shape_error_from_payload("live_two_repeat_evaluation_report", payload)

    assert error is not None
    assert "unknown field" in error


def test_capture_extract_rejects_captured_at_injection(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["capture_envelope"])
    data["captured_at"] = "2026-06-24T00:00:00Z"
    paths["capture_envelope"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="unknown field"):
        extract_artifact_from_paths("live_two_repeat_evaluation_report", **paths)


def test_capture_extract_rejects_reproduction_claim_leakage(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["network_controls"])
    data["reproduction_claimed"] = True
    paths["network_controls"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="reproduction_claimed"):
        extract_artifact_from_paths("network_resource_controls_attestation", **paths)


def test_capture_extract_rejects_mode_drift(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["model_backend_preflight_result"])
    data["mode"] = "replay"
    paths["model_backend_preflight_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="mode must be live"):
        extract_artifact_from_paths("model_backend_preflight_report", **paths)


def test_capture_extract_rejects_split_total_count_drift(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["split_manifest_result"])
    data["total_cases"] = 63
    paths["split_manifest_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="total_cases must be 64"):
        extract_artifact_from_paths("live_terminal_bench_split_manifest", **paths)


def test_capture_extract_rejects_split_overlap(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["split_manifest_result"])
    data["held_out_task_ids"][0] = data["held_in_task_ids"][0]
    paths["split_manifest_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="disjoint"):
        extract_artifact_from_paths("live_terminal_bench_split_manifest", **paths)


def test_capture_extract_rejects_split_timestamp_injection(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["split_manifest_result"])
    data["captured_at"] = "2026-06-24T00:00:00Z"
    paths["split_manifest_result"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="unknown field"):
        extract_artifact_from_paths("live_terminal_bench_split_manifest", **paths)


def test_capture_extract_rejects_fixed_protocol_missing_paper_backend(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["fixed_protocol_declaration"])
    data["models"] = ["minimax", "qwen"]
    paths["fixed_protocol_declaration"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="models must cover"):
        extract_artifact_from_paths("fixed_protocol_config", **paths)


def test_capture_extract_rejects_fixed_protocol_missing_evaluator(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["fixed_protocol_declaration"])
    data["evaluator"] = ""
    paths["fixed_protocol_declaration"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="evaluator"):
        extract_artifact_from_paths("fixed_protocol_config", **paths)


def test_capture_extract_rejects_fixed_protocol_budget_drift(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["fixed_protocol_declaration"])
    data["decoding_budget"] = "8192"
    paths["fixed_protocol_declaration"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="decoding_budget"):
        extract_artifact_from_paths("fixed_protocol_config", **paths)


def test_capture_extract_proposer_llm_request_log_happy_path(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    payload = extract_artifact_from_paths("proposer_llm_request_log", **paths)

    assert payload["round_count"] == 3
    assert [row["backend"] for row in payload["rounds"]] == ["minimax", "qwen", "glm"]
    assert [row["model"] for row in payload["rounds"]] == ["MiniMax-M2.5", "Qwen3.5-35B-A3B", "GLM-5.2"]


def test_capture_extract_proposer_llm_request_log_rejects_unknown_envelope_field(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    data = _read(paths["capture_envelope"])
    data["surprise"] = True
    paths["capture_envelope"].write_text(stable_json_dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="unknown field"):
        extract_artifact_from_paths("proposer_llm_request_log", **paths)


def test_capture_extract_proposer_llm_request_log_rejects_malformed_sha256(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_request_log"])
    rows[0]["request_sha256"] = "not-a-hash"
    _write_jsonl(paths["proposer_request_log"], rows)

    with pytest.raises(CaptureExtractError, match="request_sha256"):
        extract_artifact_from_paths("proposer_llm_request_log", **paths)


def test_capture_extract_proposer_llm_request_log_rejects_unknown_backend_key(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    paths["proposer_backend_map"] = {"primary": "openai"}

    with pytest.raises(CaptureExtractError, match="unknown proposer backend"):
        extract_artifact_from_paths("proposer_llm_request_log", **paths)


def test_capture_extract_proposer_llm_request_log_rejects_reproduction_claim(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_request_log"])
    rows[0]["reproduction_claimed"] = True
    _write_jsonl(paths["proposer_request_log"], rows)

    with pytest.raises(CaptureExtractError, match="reproduction_claimed"):
        extract_artifact_from_paths("proposer_llm_request_log", **paths)


def test_capture_extract_proposer_llm_request_log_rejects_round_index_gap(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_request_log"])
    rows[1]["round_index"] = 3
    _write_jsonl(paths["proposer_request_log"], rows)

    with pytest.raises(CaptureExtractError, match="round_index"):
        extract_artifact_from_paths("proposer_llm_request_log", **paths)


def test_capture_extract_proposer_context_manifest_happy_path(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    payload = extract_artifact_from_paths("proposer_context_manifest", **paths)

    assert payload["round_count"] == 3
    assert [row["round_index"] for row in payload["rounds"]] == [0, 1, 2]
    assert payload["rounds"][0]["editable_surfaces"]["surface_count"] == 2
    assert payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["task_ids"] == _task_ids(0, 1)
    assert (
        payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["failure_category"]
        == "assertion-fail"
    )
    assert (
        payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["causal_status_sha256"]
        == _causal_status_hash("agent-causal")
    )
    assert (
        payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["shared_symptoms_sha256"]
        == _evidence_hash("shared_symptoms", ["assertion mismatch", "same verifier failure"])
    )
    assert (
        payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["verifier_evidence_sha256"]
        == _evidence_hash("verifier_evidence", ["terminal-bench verifier failed"])
    )
    assert payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["presentation_order"] == 0
    assert (
        payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["actionability_hint_sha256"]
        == _evidence_hash("actionability_hint", "high support, high actionability")
    )
    assert "causal_status" not in payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    assert "shared_symptoms" not in payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    assert "verifier_evidence" not in payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    assert "actionability_hint" not in payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    assert payload["rounds"][0]["passing_behavior_summaries"]["summaries"][0]["task_ids"] == _task_ids(3, 32)
    assert payload["rounds"][0]["previous_attempted_edits"]["edit_count"] == 0
    assert payload["rounds"][1]["previous_attempted_edits"]["edit_count"] == 1


def test_capture_extract_proposer_context_manifest_rejects_unknown_row_field(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["surprise"] = True
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="unknown field"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_malformed_ingredient_hash(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["editable_surfaces"]["surfaces"][0]["sha256"] = "not-a-hash"
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="sha256"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_malformed_causal_status(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["causal_status"] = ""
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="causal_status"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_mismatched_causal_status_hash(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["causal_status_sha256"] = "0" * 64
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="causal_status_sha256"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_malformed_failure_evidence(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["shared_symptoms"] = ["ok", ""]
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="shared_symptoms"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_mismatched_failure_evidence_hash(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["verifier_evidence_sha256"] = "0" * 64
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="verifier_evidence_sha256"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_malformed_actionability_hint(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["actionability_hint"] = ""
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="actionability_hint"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_mismatched_actionability_hint_hash(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["actionability_hint_sha256"] = "0" * 64
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="actionability_hint_sha256"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_accepts_absent_failure_evidence_hashes(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    for pattern in rows[0]["held_in_failure_patterns"]["patterns"]:
        pattern.pop("shared_symptoms", None)
        pattern.pop("verifier_evidence", None)
        pattern.pop("shared_symptoms_sha256", None)
        pattern.pop("verifier_evidence_sha256", None)
        pattern.pop("presentation_order", None)
        pattern.pop("actionability_hint", None)
        pattern.pop("actionability_hint_sha256", None)
    _write_jsonl(paths["proposer_context_log"], rows)

    payload = extract_artifact_from_paths("proposer_context_manifest", **paths)

    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    assert "shared_symptoms_sha256" not in pattern
    assert "verifier_evidence_sha256" not in pattern
    assert "presentation_order" not in pattern
    assert "actionability_hint_sha256" not in pattern


def test_capture_extract_proposer_context_manifest_rejects_round_index_gap(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[1]["round_index"] = 4
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="round_index"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposer_context_manifest_rejects_unknown_split_task_id(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    rows = _read_jsonl(paths["proposer_context_log"])
    rows[0]["held_in_failure_patterns"]["patterns"][0]["task_ids"] = ["not-in-split"]
    _write_jsonl(paths["proposer_context_log"], rows)

    with pytest.raises(CaptureExtractError, match="unknown split task ids"):
        extract_artifact_from_paths("proposer_context_manifest", **paths)


def test_capture_extract_proposal_validation_manifest_happy_path(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    payload = extract_artifact_from_paths("proposal_validation_manifest", **paths)

    assert payload["round_count"] == 3
    assert [row["round_index"] for row in payload["rounds"]] == [0, 1, 2]
    assert payload["rounds"][0]["baseline_split_outcomes"]["evaluation_repeats"] == 2
    assert payload["rounds"][0]["harness_before_sha256"] == _audit_harness_state_hash(0)
    assert payload["rounds"][0]["harness_after_sha256"] == _audit_harness_state_hash(1)
    assert payload["rounds"][1]["harness_before_sha256"] == _audit_harness_state_hash(1)
    assert len(payload["rounds"][0]["candidates"]) == 2
    assert payload["rounds"][0]["candidates"][0]["validation_failure_category"] is None
    baseline_task_outcomes = payload["rounds"][0]["baseline_split_outcomes"]["task_outcomes"]
    assert len(baseline_task_outcomes) == 64
    assert sum(1 for row in baseline_task_outcomes if row["split"] == "held_in" and row["pass"] is True) == 29
    assert all(
        row.get("failure_category") == "assertion-fail"
        for row in baseline_task_outcomes
        if row["split"] == "held_in" and row["pass"] is False
    )
    assert all(
        "failure_category" not in row
        for row in baseline_task_outcomes
        if row["pass"] is True
    )
    task_outcomes = payload["rounds"][0]["candidates"][0]["split_outcomes"]["task_outcomes"]
    assert len(task_outcomes) == 64
    assert sum(1 for row in task_outcomes if row["split"] == "held_in" and row["pass"] is True) == 30
    assert sum(1 for row in task_outcomes if row["split"] == "held_out" and row["pass"] is True) == 32
    assert payload["rounds"][1]["candidates"][1]["audit_decision"] == "rejected"
    assert payload["rounds"][1]["candidates"][1]["validation_failure_category"] is None
    assert payload["rounds"][1]["candidates"][1]["rejection_reason"] == "candidate regressed"
    assert "proposer_round_request_sha256" not in payload["rounds"][0]
    assert "proposer_round_response_sha256" not in payload["rounds"][0]
    assert payload["fixed_protocol_sha256"] == sha256(paths["fixed_protocol_result"].read_bytes()).hexdigest()


def test_capture_extract_proposal_validation_manifest_rejects_malformed_lineage_hash(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    lineage_path = paths["audit_run_dir"] / "lineage.json"
    rows = _read(lineage_path)
    rows[1]["harness_before_hash"] = "not-a-hash"
    _write_json(lineage_path, rows)

    with pytest.raises(CaptureExtractError, match="harness_before_hash"):
        extract_artifact_from_paths("proposal_validation_manifest", **paths)


def test_capture_extract_proposal_validation_manifest_stamps_multi_commit_merged_hash(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    proposal_path = paths["audit_run_dir"] / "rounds" / "0" / "proposals.jsonl"
    rows = _read_jsonl(proposal_path)
    rows[1]["status"] = "merged"
    rows[1]["decision_reason"] = "candidate merged with compatible accepted edit"
    rows[1]["rejection_reason"] = None
    _write_jsonl(proposal_path, rows)
    _append_audit_merge_evaluation(paths["audit_run_dir"], round_index=0)

    payload = extract_artifact_from_paths("proposal_validation_manifest", **paths)

    assert payload["rounds"][0]["committed_proposal_ids"] == ["proposal-0-0", "proposal-0-1"]
    assert payload["rounds"][0]["harness_after_merged_sha256"] == _audit_harness_state_hash(1)
    assert payload["rounds"][0]["merged_split_outcomes"]["held_in_passed"] == 30
    assert payload["rounds"][0]["merged_split_outcomes"]["held_out_passed"] == 32
    assert "harness_after_merged_sha256" not in payload["rounds"][1]
    assert "merged_split_outcomes" not in payload["rounds"][1]


def test_capture_extract_proposal_validation_manifest_rejects_missing_merge_evaluation(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    proposal_path = paths["audit_run_dir"] / "rounds" / "0" / "proposals.jsonl"
    rows = _read_jsonl(proposal_path)
    rows[1]["status"] = "merged"
    rows[1]["decision_reason"] = "candidate merged with compatible accepted edit"
    rows[1]["rejection_reason"] = None
    _write_jsonl(proposal_path, rows)

    with pytest.raises(CaptureExtractError, match="proposal_id=__merge__ arm=candidate"):
        extract_artifact_from_paths("proposal_validation_manifest", **paths)


def test_capture_extract_proposal_validation_manifest_binds_proposer_request_log_artifact(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    proposer_artifact = _write_proposer_request_log_artifact(tmp_path, paths)

    payload = extract_artifact_from_paths(
        "proposal_validation_manifest",
        **paths,
        proposer_request_log_artifact=proposer_artifact,
    )

    assert payload["rounds"][0]["proposer_round_request_sha256"] == "1" * 64
    assert payload["rounds"][0]["proposer_round_response_sha256"] == "2" * 64
    assert payload["rounds"][1]["proposer_round_request_sha256"] == "3" * 64
    assert payload["rounds"][2]["proposer_round_response_sha256"] == "6" * 64


def test_capture_extract_proposal_validation_manifest_rejects_missing_proposer_round(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    proposer_artifact = _write_proposer_request_log_artifact(tmp_path, paths)
    payload = _read(proposer_artifact)
    payload["rounds"].pop()
    payload["round_count"] = len(payload["rounds"])
    proposer_artifact.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(CaptureExtractError, match="missing round_index 2"):
        extract_artifact_from_paths(
            "proposal_validation_manifest",
            **paths,
            proposer_request_log_artifact=proposer_artifact,
        )


def test_capture_extract_omits_proposal_validation_task_outcomes_when_audit_has_only_totals(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    for round_index in range(3):
        path = paths["audit_run_dir"] / "rounds" / str(round_index) / "evaluations.jsonl"
        rows = [row for row in _read_jsonl(path) if row["task_id"] == "__split_total__"]
        _write_jsonl(path, rows)

    payload = extract_artifact_from_paths("proposal_validation_manifest", **paths)

    assert "task_outcomes" not in payload["rounds"][0]["baseline_split_outcomes"]
    assert "task_outcomes" not in payload["rounds"][0]["candidates"][0]["split_outcomes"]


def test_capture_extract_invalid_no_surface_proposal_validation_candidate(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    proposal_path = paths["audit_run_dir"] / "rounds" / "1" / "proposals.jsonl"
    rows = _read_jsonl(proposal_path)
    rows[1]["status"] = "invalid"
    rows[1]["decision_reason"] = "proposal did not modify an editable surface"
    rows[1]["rejection_reason"] = "proposal did not modify an editable surface"
    rows[1].pop("changed_surfaces")
    rows[1].pop("surface")
    _write_jsonl(proposal_path, rows)

    payload = extract_artifact_from_paths("proposal_validation_manifest", **paths)

    candidate = payload["rounds"][1]["candidates"][1]
    assert candidate["audit_decision"] == "invalid"
    assert candidate["validation_failure_category"] == "no_editable_surface"
    assert candidate["changed_surfaces"] == []


def test_capture_extract_invalid_surface_candidate_as_execution_failure(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    proposal_path = paths["audit_run_dir"] / "rounds" / "1" / "proposals.jsonl"
    rows = _read_jsonl(proposal_path)
    rows[1]["status"] = "invalid"
    rows[1]["decision_reason"] = "candidate evaluation failed before valid result"
    rows[1]["rejection_reason"] = "candidate evaluation failed before valid result"
    _write_jsonl(proposal_path, rows)

    payload = extract_artifact_from_paths("proposal_validation_manifest", **paths)

    candidate = payload["rounds"][1]["candidates"][1]
    assert candidate["audit_decision"] == "invalid"
    assert candidate["validation_failure_category"] == "execution_failure"
    assert candidate["changed_surfaces"] == ["tool_manifest"]


def test_capture_extract_non_invalid_candidate_still_requires_changed_surface(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    proposal_path = paths["audit_run_dir"] / "rounds" / "0" / "proposals.jsonl"
    rows = _read_jsonl(proposal_path)
    rows[0].pop("changed_surfaces")
    rows[0].pop("surface")
    _write_jsonl(proposal_path, rows)

    with pytest.raises(CaptureExtractError, match="changed surface"):
        extract_artifact_from_paths("proposal_validation_manifest", **paths)


def _fixture_paths(tmp_path: Path) -> dict[str, Path]:
    harbor_discovery = tmp_path / "harbor-discovery-live.json"
    image_policy = tmp_path / "image-policy.json"
    model_backend = tmp_path / "model-backend-live.json"
    network_controls = tmp_path / "network-controls.json"
    capture_envelope = tmp_path / "capture-envelope.json"
    attempts_jsonl = tmp_path / "attempts.jsonl"
    harbor_run_dir = tmp_path / "harbor-run"
    split_manifest = tmp_path / "split-manifest-live.json"
    fixed_protocol = tmp_path / "fixed-protocol-live.json"
    fixed_protocol_result = tmp_path / "fixed-protocol-result.json"
    proposer_request_log = tmp_path / "proposer-request-log.jsonl"
    proposer_context_log = tmp_path / "proposer-context-log.jsonl"
    audit_run_dir = tmp_path / "audit-run"

    _write_json(
        harbor_discovery,
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "source": "https://harbor.example/api/v2.0/artifacts/stable",
            "request": {"method": "GET"},
            "discovered_images": [
                {
                    "image": IMAGE,
                    "digest": DIGEST,
                    "reference": "stable",
                    "tags": ["stable"],
                    "media_type": "application/vnd.oci.image.manifest.v1+json",
                    "child_digests": [],
                }
            ],
            "reason": None,
            "reproduction_claimed": False,
        },
    )
    _write_json(
        image_policy,
        {
            "policy_version": "1",
            "entries": [{"image": IMAGE, "digest": DIGEST, "status": "active", "labels": {}}],
        },
    )
    _write_json(
        model_backend,
        {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "backends": ["minimax", "qwen", "glm"],
            "checks": [
                {
                    "name": "minimax_backend_reachable",
                    "backend": "minimax",
                    "status": "pass",
                    "detail": "live chat completion parsed successfully",
                    "required": True,
                    "metadata": {},
                },
                {
                    "name": "qwen_backend_reachable",
                    "backend": "qwen",
                    "status": "pass",
                    "detail": "live chat completion parsed successfully",
                    "required": True,
                    "metadata": {},
                },
                {
                    "name": "glm_backend_reachable",
                    "backend": "glm",
                    "status": "pass",
                    "detail": "live chat completion parsed successfully",
                    "required": True,
                    "metadata": {},
                },
            ],
            "report_hash": "d" * 64,
            "reproduction_claimed": False,
            "boundary": "fixture live model backend preflight",
            "evaluated_at": "2026-06-24",
        },
    )
    _write_json(
        network_controls,
        {
            "schema_version": "1.0",
            "mode": "live",
            "outbound_bandwidth_cap_bps": 2_000_000,
            "mirrored_resources": ["https://resources.example/terminal-bench"],
            "capture_run_id": "terminal-bench-2.0-live-fixture",
            "reproduction_claimed": False,
        },
    )
    held_in = _task_ids(0, 32)
    held_out = _task_ids(32, 64)
    _write_json(
        split_manifest,
        {
            "schema_version": "1.0",
            "mode": "live",
            "source": "harbor",
            "total_cases": 64,
            "held_in_count": len(held_in),
            "held_out_count": len(held_out),
            "held_in_task_ids": held_in,
            "held_out_task_ids": held_out,
            "fixed_across_variants": True,
            "capture_run_id": "terminal-bench-2.0-live-fixture",
            "operator_label": "self-harness-tests",
            "reproduction_claimed": False,
        },
    )
    _write_json(
        fixed_protocol,
        {
            "schema_version": "1.0",
            "mode": "live",
            "benchmark_protocol": "terminal-bench@2.0",
            "models": ["minimax", "qwen", "glm"],
            "evaluator": "terminal-bench-verifier",
            "tool_set": "minimal-terminal-tools",
            "decoding_budget": {"max_tokens": 8192, "max_tool_calls": 100},
            "self_harness_rounds": 3,
            "proposal_width": 2,
            "fixed_across_variants": True,
            "capture_run_id": "terminal-bench-2.0-live-fixture",
            "operator_label": "self-harness-tests",
            "reproduction_claimed": False,
        },
    )
    _write_json(
        fixed_protocol_result,
        extract_artifact_from_paths(
            "fixed_protocol_config",
            capture_run_id="terminal-bench-2.0-live-fixture",
            fixed_protocol_declaration=fixed_protocol,
        ),
    )
    _write_json(
        capture_envelope,
        {
            "schema_version": "1.0",
            "mode": "live",
            "source": "harbor",
            "capture_run_id": "terminal-bench-2.0-live-fixture",
            "operator_label": "self-harness-tests",
            "reproduction_claimed": False,
        },
    )
    attempts_jsonl.write_text(
        "\n".join(
            [
                stable_json_dumps({"task_id": "task-a", "attempt_index": 0, "pass": True}),
                stable_json_dumps({"task_id": "task-a", "attempt_index": 1, "pass": False}),
                stable_json_dumps({"task_id": "task-b", "attempt_index": 0, "pass": True}),
                stable_json_dumps({"task_id": "task-b", "attempt_index": 1, "pass": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_jsonl(
        proposer_request_log,
        [
            {
                "round_index": 0,
                "proposer_client": "primary",
                "request_sha256": "1" * 64,
                "response_sha256": "2" * 64,
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "attempted_proposals": 2,
                "committed_proposals": 1,
            },
            {
                "round_index": 1,
                "proposer_client": "secondary",
                "request_sha256": "3" * 64,
                "response_sha256": "4" * 64,
                "prompt_tokens": 13,
                "completion_tokens": 5,
                "attempted_proposals": 2,
                "committed_proposals": 1,
            },
            {
                "round_index": 2,
                "proposer_client": "tertiary",
                "request_sha256": "5" * 64,
                "response_sha256": "6" * 64,
                "prompt_tokens": 17,
                "completion_tokens": 9,
                "attempted_proposals": 2,
                "committed_proposals": 1,
            },
        ],
    )
    _write_jsonl(proposer_context_log, _proposer_context_rows())
    _write_audit_run(audit_run_dir)
    _write_trial(harbor_run_dir, "task-a", 0, reward=1.0)
    _write_trial(harbor_run_dir, "task-a", 1, reward=0.0)
    _write_trial(harbor_run_dir, "task-b", 0, reward=1.0)
    _write_trial(harbor_run_dir, "task-b", 1, reward=1.0)
    return {
        "harbor_discovery_result": harbor_discovery,
        "image_policy": image_policy,
        "model_backend_preflight_result": model_backend,
        "network_controls": network_controls,
        "capture_envelope": capture_envelope,
        "attempts_jsonl": attempts_jsonl,
        "harbor_run_dir": harbor_run_dir,
        "split_manifest_result": split_manifest,
        "fixed_protocol_declaration": fixed_protocol,
        "fixed_protocol_result": fixed_protocol_result,
        "proposer_request_log": proposer_request_log,
        "proposer_context_log": proposer_context_log,
        "audit_run_dir": audit_run_dir,
        "proposer_backend_map": {"primary": "minimax", "secondary": "qwen", "tertiary": "glm"},
        "capture_run_id": "terminal-bench-2.0-live-fixture",
        "harbor_version": "2.10.0",
    }


def _write_proposer_request_log_artifact(tmp_path: Path, paths: dict[str, Path]) -> Path:
    proposer_artifact = tmp_path / "proposer-request-log-artifact.json"
    _write_json(proposer_artifact, extract_artifact_from_paths("proposer_llm_request_log", **paths))
    return proposer_artifact


def _proposer_context_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(3):
        failing_task_ids = _round_baseline_held_in_failing_task_ids(index)
        passing_task_ids = [
            task_id
            for task_id in _task_ids(0, 32)
            if task_id not in set(failing_task_ids)
        ]
        patterns: list[dict[str, object]] = []
        pattern_ids = [
            f"cluster-{index}",
            f"cluster-{index}-alt",
            f"cluster-{index}-extra",
        ]
        for pattern_index, task_id in enumerate(failing_task_ids):
            pattern_id = pattern_ids[pattern_index]
            shared_symptoms: object = "assertion mismatch"
            verifier_evidence: object = "terminal-bench verifier failed"
            actionability_hint = "lower support, medium actionability"
            if pattern_index == 0:
                shared_symptoms = ["assertion mismatch", "same verifier failure"]
                verifier_evidence = ["terminal-bench verifier failed"]
                actionability_hint = "high support, high actionability"
            patterns.append(
                {
                    "cluster_id": pattern_id,
                    "size": 1,
                    "task_ids": [task_id],
                    "mechanism_sha256": _mechanism_hash(pattern_id),
                    "failure_category": "assertion-fail",
                    "causal_status": "agent-causal",
                    "shared_symptoms": shared_symptoms,
                    "verifier_evidence": verifier_evidence,
                    "presentation_order": pattern_index,
                    "actionability_hint": actionability_hint,
                }
            )
        rows.append(
            {
                "round_index": index,
                "editable_surfaces": {
                    "surface_count": 2,
                    "surfaces": [
                        {
                            "kind": "prompt",
                            "name": "system_prompt",
                            "sha256": _surface_hash("system_prompt"),
                        },
                        {
                            "kind": "tool",
                            "name": "tool_manifest",
                            "sha256": _surface_hash("tool_manifest"),
                        }
                    ],
                },
                "held_in_failure_patterns": {
                    "pattern_count": len(patterns),
                    "patterns": patterns,
                },
                "passing_behavior_summaries": {
                    "summary_count": 1,
                    "summaries": [
                        {
                            "task_ids": passing_task_ids,
                            "task_id_set_sha256": _task_id_set_hash(passing_task_ids),
                            "preserved_behavior_sha256": "a" * 64,
                        }
                    ],
                },
                "previous_attempted_edits": _previous_attempted_edits(index),
            }
        )
    return rows


def _task_id_set_hash(task_ids: list[str]) -> str:
    return sha256((stable_json_dumps({"task_ids": sorted(task_ids)}) + "\n").encode("utf-8")).hexdigest()


def _surface_hash(surface: str) -> str:
    return sha256(
        (stable_json_dumps({"changed_surfaces": [surface]}) + "\n").encode("utf-8")
    ).hexdigest()


def _mechanism_hash(pattern_id: str) -> str:
    return sha256((stable_json_dumps({"pattern_id": pattern_id}) + "\n").encode("utf-8")).hexdigest()


def _causal_status_hash(causal_status: str) -> str:
    return sha256((stable_json_dumps({"causal_status": causal_status}) + "\n").encode("utf-8")).hexdigest()


def _evidence_hash(key: str, values: object) -> str:
    return sha256((stable_json_dumps({key: values}) + "\n").encode("utf-8")).hexdigest()


def _previous_attempted_edits(round_index: int) -> dict[str, object]:
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
                "targeted_mechanism_sha256": _mechanism_hash(f"cluster-{round_index - 1}"),
                "causal_status": "agent-causal",
                "edited_surface_sha256": _surface_hash("system_prompt"),
                "audit_decision": "accepted",
                "audit_decision_reason": "",
            }
        ],
    }


def _write_audit_run(root: Path) -> None:
    _write_json(
        root / "manifest.json",
        {
            "schema_version": "1.4",
            "protocol_version": "fixture-protocol",
            "benchmark_protocol": "terminal-bench@2.0",
            "reproduction_claimed": False,
        },
    )
    _write_json(
        root / "lineage.json",
        [
            {
                "round": index,
                "accepted_proposal_ids": [f"proposal-{index}-0"],
                "harness_before_hash": _audit_harness_state_hash(index),
                "harness_after_hash": _audit_harness_state_hash(index + 1),
                "ops_applied": [{"surface": "system_prompt"}],
                "reverse_ops": [{"surface": "system_prompt"}],
            }
            for index in range(3)
        ],
    )
    for index in range(3):
        round_dir = root / "rounds" / str(index)
        _write_json(round_dir / "harness_before.json", {"system_prompt": "before"})
        _write_json(round_dir / "harness_after.json", {"system_prompt": "after"})
        _write_jsonl(
            round_dir / "proposals.jsonl",
            [
                _audit_proposal(index, 0, "accepted"),
                _audit_proposal(index, 1, "rejected"),
            ],
        )
        rows = [
            *_audit_split_total_rows(
                "__baseline__",
                "baseline",
                held_in_failing_task_ids=_round_baseline_held_in_failing_task_ids(index),
                held_out_failing_task_ids=_round_baseline_held_out_failing_task_ids(index),
            ),
            *_audit_split_total_rows(
                f"proposal-{index}-0",
                "candidate",
                held_in_failing_task_ids=_round_accepted_held_in_failing_task_ids(index),
                held_out_failing_task_ids=[],
            ),
            *_audit_split_total_rows(
                f"proposal-{index}-1",
                "candidate",
                held_in_failing_task_ids=_round_rejected_held_in_failing_task_ids(index),
                held_out_failing_task_ids=_round_rejected_held_out_failing_task_ids(index),
            ),
        ]
        _write_jsonl(round_dir / "evaluations.jsonl", rows)


def _append_audit_merge_evaluation(root: Path, *, round_index: int) -> None:
    path = root / "rounds" / str(round_index) / "evaluations.jsonl"
    rows = _read_jsonl(path)
    rows.extend(
        _audit_split_total_rows(
            "__merge__",
            "candidate",
            held_in_failing_task_ids=_round_accepted_held_in_failing_task_ids(round_index),
            held_out_failing_task_ids=[],
        )
    )
    _write_jsonl(path, rows)


def _audit_proposal(round_index: int, candidate_index: int, status: str) -> dict[str, object]:
    pattern_id = _proposal_pattern_id(round_index, candidate_index)
    surface = "system_prompt" if candidate_index == 0 else "tool_manifest"
    baseline_held_in_failing = _round_baseline_held_in_failing_task_ids(round_index)
    baseline_held_out_failing = _round_baseline_held_out_failing_task_ids(round_index)
    if status == "accepted":
        held_in_failing = _round_accepted_held_in_failing_task_ids(round_index)
        held_out_failing: list[str] = []
    else:
        held_in_failing = _round_rejected_held_in_failing_task_ids(round_index)
        held_out_failing = _round_rejected_held_out_failing_task_ids(round_index)
    passed_held_in = 32 - len(held_in_failing)
    passed_held_out = 32 - len(held_out_failing)
    baseline_passed_held_in = 32 - len(baseline_held_in_failing)
    baseline_passed_held_out = 32 - len(baseline_held_out_failing)
    return {
        "id": f"proposal-{round_index}-{candidate_index}",
        "schema_version": "1.4",
        "round": round_index,
        "pattern_id": pattern_id,
        "op": "replace",
        "surface": surface,
        "changed_surfaces": [surface],
        "payload": "updated prompt",
        "status": status,
        "priority": candidate_index,
        "score_held_in": passed_held_in / 32,
        "score_held_out": passed_held_out / 32,
        "passed_held_in": passed_held_in,
        "passed_held_out": passed_held_out,
        "baseline_held_in": baseline_passed_held_in / 32,
        "baseline_held_out": baseline_passed_held_out / 32,
        "baseline_passed_held_in": baseline_passed_held_in,
        "baseline_passed_held_out": baseline_passed_held_out,
        "evaluation_repeats": 2,
        "decision_reason": "candidate passed validation" if status == "accepted" else "candidate regressed",
        "rejection_reason": None if status == "accepted" else "candidate regressed",
        "rationale": "improve held-in failures",
        "expected_effect": "more robust command execution",
        "regression_risks": [],
    }


def _audit_split_total_rows(
    proposal_id: str,
    arm: str,
    *,
    held_in_failing_task_ids: list[str],
    held_out_failing_task_ids: list[str],
) -> list[dict[str, object]]:
    held_in_task_ids = _task_ids(0, 32)
    held_out_task_ids = _task_ids(32, 64)
    held_in_passed = len(held_in_task_ids) - len(held_in_failing_task_ids)
    held_out_passed = len(held_out_task_ids) - len(held_out_failing_task_ids)
    return [
        _audit_split_total_row(proposal_id, arm, split="held_in", passed=held_in_passed, total=32),
        _audit_split_total_row(proposal_id, arm, split="held_out", passed=held_out_passed, total=32),
        *_audit_task_outcome_rows(
            proposal_id,
            arm,
            split="held_in",
            task_ids=held_in_task_ids,
            failing_task_ids=held_in_failing_task_ids,
        ),
        *_audit_task_outcome_rows(
            proposal_id,
            arm,
            split="held_out",
            task_ids=held_out_task_ids,
            failing_task_ids=held_out_failing_task_ids,
        ),
    ]


def _audit_split_total_row(
    proposal_id: str,
    arm: str,
    *,
    split: str,
    passed: int,
    total: int,
) -> dict[str, object]:
    return {
        "proposal_id": proposal_id,
        "schema_version": "1.4",
        "split": split,
        "task_id": "__split_total__",
        "attempt_index": None,
        "arm": arm,
        "verifier_pass": passed,
        "verifier_fail": total - passed,
        "score": passed / total,
        "terminal_cause": None,
        "failure_category": None,
        "mechanism": None,
        "evaluation_repeats": 2,
    }


def _audit_task_outcome_rows(
    proposal_id: str,
    arm: str,
    *,
    split: str,
    task_ids: list[str],
    failing_task_ids: list[str],
) -> list[dict[str, object]]:
    failing = set(failing_task_ids)
    return [
        {
            "proposal_id": proposal_id,
            "schema_version": "1.4",
            "split": split,
            "task_id": task_id,
            "attempt_index": None,
            "arm": arm,
            "verifier_pass": 0 if task_id in failing else 1,
            "verifier_fail": 1 if task_id in failing else 0,
            "terminal_cause": "assertion-fail" if task_id in failing else None,
            "failure_category": "assertion-fail" if task_id in failing else "verifier-pass",
            "mechanism": "fixture failure" if task_id in failing else None,
            "evaluation_repeats": 2,
        }
        for task_id in task_ids
    ]


def _round_baseline_held_in_failing_task_ids(round_index: int) -> list[str]:
    return _task_ids(round_index, 3)


def _round_accepted_held_in_failing_task_ids(round_index: int) -> list[str]:
    return _task_ids(round_index + 1, 3)


def _round_rejected_held_in_failing_task_ids(round_index: int) -> list[str]:
    return sorted(
        {
            *_round_baseline_held_in_failing_task_ids(round_index),
            "terminal-bench-task-00",
        }
    )


def _round_baseline_held_out_failing_task_ids(round_index: int) -> list[str]:
    return _task_ids(32, 33) if round_index == 0 else []


def _round_rejected_held_out_failing_task_ids(_round_index: int) -> list[str]:
    return _task_ids(32, 33)


def _proposal_pattern_id(round_index: int, candidate_index: int) -> str:
    if candidate_index == 0:
        return f"cluster-{round_index}"
    if len(_round_baseline_held_in_failing_task_ids(round_index)) > 1:
        return f"cluster-{round_index}-alt"
    return f"cluster-{round_index}"


def _audit_harness_state_hash(state_index: int) -> str:
    values = ("0", "1", "2", "3")
    return values[state_index] * 64


def _write_trial(root: Path, task_id: str, attempt: int, *, reward: float) -> None:
    trial = root / task_id / str(attempt)
    trial.mkdir(parents=True)
    _write_json(trial / "metadata.json", {"task_id": task_id})
    _write_json(trial / "reward.json", {"reward": reward})
    trial.joinpath("trajectory.jsonl").write_text(
        stable_json_dumps({"kind": "assistant", "message": "finished"}) + "\n",
        encoding="utf-8",
    )


def _rewrite_trial_metadata(
    root: Path,
    *,
    image_digest: str,
    task_id: str | None = None,
    attempt: int | None = None,
) -> None:
    for metadata_path in sorted(root.glob("*/*/metadata.json")):
        payload = _read(metadata_path)
        if task_id is not None and payload["task_id"] != task_id:
            continue
        if attempt is not None and metadata_path.parent.name != str(attempt):
            continue
        payload["image_digest"] = image_digest
        _write_json(metadata_path, payload)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(stable_json_dumps(row) + "\n" for row in rows), encoding="utf-8")


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _task_ids(start: int, stop: int) -> list[str]:
    return [f"terminal-bench-task-{index:02d}" for index in range(start, stop)]


def _two_repeat_payload(tmp_path: Path) -> dict[str, object]:
    return extract_artifact_from_paths("live_two_repeat_evaluation_report", **_fixture_paths(tmp_path))
