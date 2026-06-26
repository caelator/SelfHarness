import json
import shutil
import subprocess
import sys
from pathlib import Path

import self_harness.capture_admit as capture_admit_module
import self_harness.reproduction_bundle as reproduction_bundle_module
import self_harness.reproduction_bundle_build as reproduction_bundle_build_module
from self_harness.cli import main
from self_harness.reproduction_readiness import ReproductionRequirement
from self_harness.types import stable_json_dumps
from test_capture_extract import _fixture_paths
from test_reproduction_readiness import _class_shaped_payloads, _provisioned_readiness_matrix

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "capture_admit.py"


def test_capture_admit_script_builds_verified_ready_report(tmp_path: Path) -> None:
    completed, report_path = _run_admission(tmp_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 0, completed.stderr
    assert payload["ok"] is True
    assert payload["reproduction_claimed"] is False
    assert payload["bundle_verification"]["ok"] is True
    assert payload["readiness"]["reproduction_ready"] is True
    assert {row["status"] for row in payload["extractions"]} == {"pass"}
    assert {row["source"] for row in payload["extractions"]} == {"extracted", "supplied"}
    capture_check = next(
        check
        for check in payload["bundle_verification"]["checks"]
        if check["name"] == "cross_artifact_capture_run_id_binding"
    )
    assert capture_check["status"] == "pass"
    assert capture_check["metadata"]["unique_capture_run_ids"] == ["terminal-bench-2.0-live-fixture"]
    assert (tmp_path / "admission" / "bundle.json").exists()


def test_capture_admit_installed_cli_matches_script_shape(tmp_path: Path, capsys) -> None:
    args, report_path = _admission_args(tmp_path, report_name="cli-report.json")

    code = main(["capture-admit", *args])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload == json.loads(report_path.read_text(encoding="utf-8"))


def test_capture_admit_skip_readiness_has_distinct_hash(tmp_path: Path) -> None:
    full, full_report = _run_admission(tmp_path / "full")
    skipped, skipped_report = _run_admission(tmp_path / "skipped", skip_readiness=True)
    full_payload = json.loads(full_report.read_text(encoding="utf-8"))
    skipped_payload = json.loads(skipped_report.read_text(encoding="utf-8"))

    assert full.returncode == 0
    assert skipped.returncode == 0
    assert full_payload["readiness"]["skipped"] is False
    assert skipped_payload["readiness"]["skipped"] is True
    assert full_payload["report_hash"] != skipped_payload["report_hash"]


def test_capture_admit_rejects_missing_required_artifact(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    audit_artifact = f"audit_verify_report={tmp_path / 'supplied' / 'audit_verify_report.json'}"
    index = args.index("--artifact", args.index(audit_artifact))
    del args[index : index + 2]

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert any(row["artifact_class"] == "release_candidate_evidence" for row in payload["extractions"])


def test_capture_admit_rejects_required_signature_absence(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    args.append("--require-bundle-signature")

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["bundle_verification"]["ok"] is False
    assert any("signature is required" in check["detail"] for check in payload["bundle_verification"]["checks"])


def test_capture_admit_rejects_extractor_unknown_field(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    raw = json.loads((tmp_path / "raw" / "harbor-discovery-live.json").read_text(encoding="utf-8"))
    raw["unexpected"] = "drift"
    (tmp_path / "raw" / "harbor-discovery-live.json").write_text(
        stable_json_dumps(raw) + "\n",
        encoding="utf-8",
    )

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert any("unknown field" in row.get("detail", "") for row in payload["extractions"])


def test_capture_admit_rejects_reproduction_claim_leakage(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    raw = json.loads((tmp_path / "raw" / "network-controls.json").read_text(encoding="utf-8"))
    raw["reproduction_claimed"] = True
    (tmp_path / "raw" / "network-controls.json").write_text(
        stable_json_dumps(raw) + "\n",
        encoding="utf-8",
    )

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert any("reproduction_claimed" in row.get("detail", "") for row in payload["extractions"])


def test_capture_admit_rejects_two_repeat_split_coverage_drift(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    split = json.loads((tmp_path / "raw" / "split-manifest-live.json").read_text(encoding="utf-8"))
    task_ids = [*split["held_in_task_ids"], *split["held_out_task_ids"]]
    _write_attempt_rows(tmp_path / "raw" / "attempts.jsonl", task_ids[:-1])

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["bundle_verification"]["ok"] is False
    assert any(
        check["name"] == "cross_artifact_split_evaluation_coverage" and check["status"] == "fail"
        for check in payload["bundle_verification"]["checks"]
    )


def test_capture_admit_rejects_live_audit_split_coverage_drift(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    split = json.loads((tmp_path / "raw" / "split-manifest-live.json").read_text(encoding="utf-8"))
    task_ids = [*split["held_in_task_ids"], *split["held_out_task_ids"]]
    _write_harbor_trials(tmp_path / "raw" / "harbor-run", task_ids[:-1])

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["bundle_verification"]["ok"] is False
    assert any(
        check["name"] == "cross_artifact_audit_split_coverage" and check["status"] == "fail"
        for check in payload["bundle_verification"]["checks"]
    )


def test_capture_admit_rejects_evaluation_audit_outcome_drift(tmp_path: Path) -> None:
    args, report_path = _admission_args(tmp_path)
    split = json.loads((tmp_path / "raw" / "split-manifest-live.json").read_text(encoding="utf-8"))
    task_ids = [*split["held_in_task_ids"], *split["held_out_task_ids"]]
    _write_attempt_rows(tmp_path / "raw" / "attempts.jsonl", task_ids, failing_attempts={(task_ids[0], 0)})

    completed = _run_script_args(args)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["bundle_verification"]["ok"] is False
    assert any(
        check["name"] == "cross_artifact_evaluation_audit_outcomes" and check["status"] == "fail"
        for check in payload["bundle_verification"]["checks"]
    )


def test_capture_admit_surfaces_model_protocol_binding_drift(tmp_path: Path, monkeypatch) -> None:
    supplied = tmp_path / "supplied"
    supplied.mkdir()
    payloads = json.loads(stable_json_dumps(_class_shaped_payloads()))
    payloads["model_backend_preflight_report"]["backends"] = ["minimax", "qwen"]
    for artifact_class in ("fixed_protocol_config", "model_backend_preflight_report"):
        (supplied / f"{artifact_class}.json").write_text(
            stable_json_dumps(payloads[artifact_class]) + "\n",
            encoding="utf-8",
        )

    def accept_shape(_artifact_class: str, _path: Path) -> None:
        return None

    monkeypatch.setattr(capture_admit_module, "artifact_shape_error", accept_shape)
    monkeypatch.setattr(reproduction_bundle_build_module, "artifact_shape_error", accept_shape)
    monkeypatch.setattr(reproduction_bundle_module, "artifact_shape_error", accept_shape)

    result = capture_admit_module.run_capture_admission(
        admission_id="terminal-bench-2.0-admission-001",
        requirements=_requirements("fixed_protocol_config", "model_backend_preflight_report"),
        artifact_dir=tmp_path / "admission" / "artifacts",
        bundle_path=tmp_path / "admission" / "bundle.json",
        bundle_id="terminal-bench-2.0-live-001",
        operator_label="self-harness-tests",
        created_at="2026-06-24T00:00:00Z",
        source_provider="harbor",
        source_captured_at="2026-06-24T00:00:00Z",
        raw_inputs={},
        raw_flags={},
        supplied_artifacts={
            "fixed_protocol_config": supplied / "fixed_protocol_config.json",
            "model_backend_preflight_report": supplied / "model_backend_preflight_report.json",
        },
        skip_readiness=True,
    )
    payload = result.payload

    assert payload["ok"] is False
    assert payload["bundle_verification"]["ok"] is False
    assert any(
        check["name"] == "cross_artifact_model_protocol_binding" and check["status"] == "fail"
        for check in payload["bundle_verification"]["checks"]
    )


def _run_admission(
    tmp_path: Path,
    *,
    skip_readiness: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    args, report_path = _admission_args(tmp_path, skip_readiness=skip_readiness)
    return _run_script_args(args), report_path


def _run_script_args(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _admission_args(
    tmp_path: Path,
    *,
    report_name: str = "report.json",
    skip_readiness: bool = False,
) -> tuple[list[str], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "raw"
    raw.mkdir()
    paths = _fixture_paths(raw)
    _write_attempt_rows_for_split(paths)
    _write_harbor_trials_for_split(paths)
    supplied = tmp_path / "supplied"
    supplied.mkdir()
    payloads = _class_shaped_payloads()
    for artifact_class in ("audit_verify_report", "release_candidate_evidence"):
        (supplied / f"{artifact_class}.json").write_text(
            stable_json_dumps(payloads[artifact_class]) + "\n",
            encoding="utf-8",
        )
    readiness = _provisioned_readiness_matrix(tmp_path)
    root = tmp_path / "admission"
    artifact_dir = root / "artifacts"
    bundle = root / "bundle.json"
    report = tmp_path / report_name
    args = [
        "--admission-id",
        "terminal-bench-2.0-admission-001",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--bundle-id",
        "terminal-bench-2.0-live-001",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--artifact-dir",
        str(artifact_dir),
        "--bundle-out",
        str(bundle),
        "--raw-flag",
        "capture_run_id=terminal-bench-2.0-live-fixture",
        "--raw-flag",
        "harbor_version=2.10.0",
        "--raw-flag",
        "proposer_backend_map=primary=minimax,secondary=qwen,tertiary=glm",
        "--raw-input",
        f"live_terminal_bench_split_manifest:split_manifest_result={paths['split_manifest_result']}",
        "--raw-input",
        f"live_harbor_preflight_report:harbor_discovery_result={paths['harbor_discovery_result']}",
        "--raw-input",
        f"container_image_trust_report:harbor_discovery_result={paths['harbor_discovery_result']}",
        "--raw-input",
        f"container_image_trust_report:image_policy={paths['image_policy']}",
        "--raw-input",
        f"fixed_protocol_config:fixed_protocol_declaration={paths['fixed_protocol_declaration']}",
        "--raw-input",
        f"model_backend_preflight_report:model_backend_preflight_result={paths['model_backend_preflight_result']}",
        "--raw-input",
        f"proposer_llm_request_log:capture_envelope={paths['capture_envelope']}",
        "--raw-input",
        f"proposer_llm_request_log:proposer_request_log={paths['proposer_request_log']}",
        "--raw-input",
        f"proposer_context_manifest:capture_envelope={paths['capture_envelope']}",
        "--raw-input",
        f"proposer_context_manifest:proposer_context_log={paths['proposer_context_log']}",
        "--raw-input",
        f"proposer_context_manifest:split_manifest_result={paths['split_manifest_result']}",
        "--raw-input",
        f"proposal_validation_manifest:capture_envelope={paths['capture_envelope']}",
        "--raw-input",
        f"proposal_validation_manifest:audit_run_dir={paths['audit_run_dir']}",
        "--raw-input",
        f"network_resource_controls_attestation:network_controls={paths['network_controls']}",
        "--raw-input",
        f"live_harbor_audit:harbor_run_dir={paths['harbor_run_dir']}",
        "--raw-input",
        f"live_two_repeat_evaluation_report:capture_envelope={paths['capture_envelope']}",
        "--raw-input",
        f"live_two_repeat_evaluation_report:attempts_jsonl={paths['attempts_jsonl']}",
        "--artifact",
        f"audit_verify_report={supplied / 'audit_verify_report.json'}",
        "--artifact",
        f"release_candidate_evidence={supplied / 'release_candidate_evidence.json'}",
        "--out",
        str(report),
    ]
    if skip_readiness:
        args.append("--skip-readiness")
    else:
        args.extend(["--readiness-matrix-result", str(readiness)])
    return args, report


def _write_attempt_rows_for_split(paths: dict[str, Path]) -> None:
    split = json.loads(paths["split_manifest_result"].read_text(encoding="utf-8"))
    task_ids = [*split["held_in_task_ids"], *split["held_out_task_ids"]]
    failing_attempts = {
        (task_id, attempt_index)
        for task_id in split["held_in_task_ids"][:2]
        for attempt_index in (0, 1)
    }
    _write_attempt_rows(paths["attempts_jsonl"], task_ids, failing_attempts=failing_attempts)


def _write_harbor_trials_for_split(paths: dict[str, Path]) -> None:
    split = json.loads(paths["split_manifest_result"].read_text(encoding="utf-8"))
    task_ids = [*split["held_in_task_ids"], *split["held_out_task_ids"]]
    _write_harbor_trials(paths["harbor_run_dir"], task_ids, failing_task_ids=set(split["held_in_task_ids"][:2]))


def _write_attempt_rows(
    path: Path,
    task_ids: list[str],
    *,
    failing_attempts: set[tuple[str, int]] | None = None,
) -> None:
    failing_attempts = failing_attempts or set()
    rows = []
    for task_id in task_ids:
        rows.append(
            stable_json_dumps(
                {
                    "task_id": task_id,
                    "attempt_index": 0,
                    "pass": (task_id, 0) not in failing_attempts,
                }
            )
        )
        rows.append(
            stable_json_dumps(
                {
                    "task_id": task_id,
                    "attempt_index": 1,
                    "pass": (task_id, 1) not in failing_attempts,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_harbor_trials(path: Path, task_ids: list[str], *, failing_task_ids: set[str] | None = None) -> None:
    failing_task_ids = failing_task_ids or set()
    shutil.rmtree(path, ignore_errors=True)
    for task_id in task_ids:
        for attempt_index in (0, 1):
            trial = path / task_id / str(attempt_index)
            trial.mkdir(parents=True)
            (trial / "metadata.json").write_text(stable_json_dumps({"task_id": task_id}) + "\n", encoding="utf-8")
            reward = 0.0 if task_id in failing_task_ids else 1.0
            (trial / "reward.json").write_text(stable_json_dumps({"reward": reward}) + "\n", encoding="utf-8")
            (trial / "trajectory.jsonl").write_text(
                stable_json_dumps({"kind": "assistant", "message": "finished"}) + "\n",
                encoding="utf-8",
            )


def _requirements(*artifact_classes: str) -> tuple[ReproductionRequirement, ...]:
    return tuple(
        ReproductionRequirement(
            requirement_id=f"{artifact_class}_requirement",
            paper_reference="Self-Harness fixed protocol",
            description=f"Fixture requirement for {artifact_class}",
            readiness_matrix_dependencies=(),
            required_artifact_class=artifact_class,
            required_state="provisioned",
            notes="test fixture",
        )
        for artifact_class in artifact_classes
    )
