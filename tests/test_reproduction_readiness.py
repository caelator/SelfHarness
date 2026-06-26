import json
import os
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from self_harness import reproduction_bundle as reproduction_bundle_module
from self_harness._artifact_shapes import artifact_shape_error_from_payload
from self_harness.corpus_signing import generate_keypair, public_key_fingerprint, public_key_raw_b64, sign_bytes
from self_harness.reproduction_bundle import (
    reproduction_bundle_report_to_jsonable,
    verify_reproduction_bundle,
)
from self_harness.reproduction_readiness import (
    evaluate_reproduction_readiness,
    load_readiness_matrix_report,
    load_reproduction_requirements,
    reproduction_readiness_report_to_jsonable,
)
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "reproduction_readiness_report.py"
SHAPE_LINT_SCRIPT = Path("scripts") / "reproduction_readiness_artifact_shape_lint.py"
BUNDLE_SCRIPT = Path("scripts") / "reproduction_bundle_verify.py"
BUILD_BUNDLE_SCRIPT = Path("scripts") / "reproduction_bundle_build.py"
SIGN_BUNDLE_SCRIPT = Path("scripts") / "sign_reproduction_bundle.py"
REQUIREMENTS = Path("docs") / "operations" / "benchmark_reproduction_requirements.json"
FIXTURES = Path("tests") / "fixtures" / "release_candidate"
FIXTURE_SIGNER = Path("tests") / "fixtures" / "external_signer.py"
AUDIT_IMAGE_DIGEST = "sha256:" + "c" * 64
OTHER_AUDIT_IMAGE_DIGEST = "sha256:" + "d" * 64
CHILD_AUDIT_IMAGE_DIGEST = "sha256:" + "e" * 64
OTHER_CHILD_AUDIT_IMAGE_DIGEST = "sha256:" + "f" * 64


def test_current_reproduction_readiness_report_is_not_ready(tmp_path: Path) -> None:
    out = tmp_path / "reproduction-readiness.json"

    completed = _run_report(
        "--readiness-matrix-result",
        str(FIXTURES / "readiness_matrix_result.json"),
        "--audit-verify-result",
        str(FIXTURES / "audit_verify_result.json"),
        "--out",
        str(out),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert payload["ok"] is True
    assert payload["reproduction_ready"] is False
    assert payload["reproduction_claimed"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", payload["report_hash"])
    assert {check["status"] for check in payload["checks"]} == {"fail"}
    assert any("Docker daemon=blocked" in check["detail"] for check in payload["checks"])


def test_reproduction_readiness_report_rejects_missing_readiness_matrix(tmp_path: Path) -> None:
    out = tmp_path / "reproduction-readiness.json"

    completed = _run_report(
        "--readiness-matrix-result",
        str(tmp_path / "missing-readiness-matrix.json"),
        "--out",
        str(out),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert completed.returncode == 3
    assert payload["ok"] is False
    assert "missing readiness matrix report" in payload["error"]


def test_reproduction_readiness_report_rejects_malformed_requirements(tmp_path: Path) -> None:
    malformed = tmp_path / "requirements.json"
    out = tmp_path / "reproduction-readiness.json"
    malformed.write_text("{", encoding="utf-8")

    completed = _run_report("--requirements", str(malformed), "--out", str(out))

    assert completed.returncode == 3
    assert "invalid benchmark reproduction requirements JSON" in completed.stderr


def test_reproduction_readiness_report_rejects_reproduction_claim_input(tmp_path: Path) -> None:
    claimed = tmp_path / "readiness-matrix.json"
    out = tmp_path / "reproduction-readiness.json"
    claimed.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "rows": [],
                "reproduction_claimed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_report("--readiness-matrix-result", str(claimed), "--out", str(out))

    assert completed.returncode == 3
    assert "claims benchmark reproduction" in completed.stderr


def test_reproduction_readiness_can_pass_with_class_shaped_provisioned_evidence(tmp_path: Path) -> None:
    readiness = _provisioned_readiness_matrix(tmp_path)
    artifacts = tmp_path / "artifacts"
    out = tmp_path / "reproduction-readiness.json"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)

    completed = _run_report(
        "--readiness-matrix-result",
        str(readiness),
        "--audit-verify-result",
        str(artifacts / "audit_verify_report.json"),
        "--artifact-dir",
        str(artifacts),
        "--out",
        str(out),
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["reproduction_ready"] is True
    assert {check["status"] for check in payload["checks"]} == {"pass"}


def test_reproduction_readiness_rejects_generic_placeholder_artifacts(tmp_path: Path) -> None:
    readiness = _provisioned_readiness_matrix(tmp_path)
    artifacts = tmp_path / "artifacts"
    out = tmp_path / "reproduction-readiness.json"
    artifacts.mkdir()
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
    for requirement in requirements:
        (artifacts / f"{requirement.required_artifact_class}.json").write_text(
            stable_json_dumps({"ok": True, "reproduction_claimed": False}) + "\n",
            encoding="utf-8",
        )

    completed = _run_report(
        "--readiness-matrix-result",
        str(readiness),
        "--audit-verify-result",
        str(artifacts / "audit_verify_report.json"),
        "--artifact-dir",
        str(artifacts),
        "--out",
        str(out),
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload["reproduction_ready"] is False
    assert any("invalid artifact evidence" in check["detail"] for check in payload["checks"])
    assert any("split manifest mode must be live" in check["detail"] for check in payload["checks"])


def test_reproduction_readiness_rejects_non_live_model_backend_artifact(tmp_path: Path) -> None:
    readiness = _provisioned_readiness_matrix(tmp_path)
    artifacts = tmp_path / "artifacts"
    out = tmp_path / "reproduction-readiness.json"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    model_artifact = artifacts / "model_backend_preflight_report.json"
    model_payload = json.loads(model_artifact.read_text(encoding="utf-8"))
    model_payload["mode"] = "replay"
    model_artifact.write_text(stable_json_dumps(model_payload) + "\n", encoding="utf-8")

    completed = _run_report(
        "--readiness-matrix-result",
        str(readiness),
        "--audit-verify-result",
        str(artifacts / "audit_verify_report.json"),
        "--artifact-dir",
        str(artifacts),
        "--out",
        str(out),
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload["reproduction_ready"] is False
    assert any("mode must be live" in check["detail"] for check in payload["checks"])


def test_artifact_shape_lint_accepts_class_shaped_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)

    completed = _run_shape_lint("--artifact-dir", str(artifacts))
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["artifact_shapes_ready"] is True
    assert {check["status"] for check in payload["checks"]} == {"pass"}


def test_artifact_shape_lint_rejects_placeholder_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for requirement in load_reproduction_requirements(REPO_ROOT / REQUIREMENTS):
        (artifacts / f"{requirement.required_artifact_class}.json").write_text(
            stable_json_dumps({"ok": True, "reproduction_claimed": False}) + "\n",
            encoding="utf-8",
        )

    completed = _run_shape_lint("--artifact-dir", str(artifacts))
    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload["artifact_shapes_ready"] is False
    assert any("invalid artifact evidence" in check["detail"] for check in payload["checks"])


def test_reproduction_bundle_accepts_signed_class_shaped_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    signature, public_key = _write_bundle_signature(tmp_path, bundle)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements, signature_path=signature, public_key=public_key)
    payload = reproduction_bundle_report_to_jsonable(report)

    assert report.ok is True
    assert payload["reproduction_claimed"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", report.report_hash)
    assert any(check.name == "bundle_signature" and check.status == "pass" for check in report.checks)
    assert any(
        check.name == "cross_artifact_split_evaluation_coverage" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_protocol_binding" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_model_protocol_binding" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_proposer_model_binding" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_proposer_round_count" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_proposer_context_binding" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_harbor_version_binding" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_capture_run_id_binding" and check.status == "pass"
        for check in report.checks
    )
    assert any(
        check.name == "cross_artifact_evaluation_audit_outcomes" and check.status == "pass"
        for check in report.checks
    )


def test_reproduction_bundle_binds_proposer_backends_to_preflight_and_protocol(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_model_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["proposer_backends"] == ["glm", "minimax", "qwen"]
    assert check.metadata["preflight_backends"] == ["glm", "minimax", "qwen"]
    assert check.metadata["protocol_backends"] == ["glm", "minimax", "qwen"]


def test_reproduction_bundle_rejects_proposer_backend_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_backends(artifacts, ["minimax", "minimax", "minimax"])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_model_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["missing_from_proposer"] == ["glm", "qwen"]


def test_reproduction_bundle_skips_proposer_binding_when_artifact_absent(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _remove_proposal_validation_round_traffic_fields(artifacts)
    (artifacts / "proposer_llm_request_log.json").unlink()
    (artifacts / "proposer_context_manifest.json").unlink()
    bundle = _write_reproduction_bundle(
        tmp_path,
        artifacts,
        exclude={"proposer_llm_request_log", "proposer_context_manifest"},
    )
    requirements = tuple(
        requirement
        for requirement in load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
        if requirement.required_artifact_class not in {"proposer_llm_request_log", "proposer_context_manifest"}
    )

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    assert not any(check.name == "cross_artifact_proposer_model_binding" for check in report.checks)
    assert not any(check.name == "cross_artifact_proposer_round_count" for check in report.checks)
    assert not any(check.name == "cross_artifact_proposer_context_binding" for check in report.checks)
    assert not any(check.name == "cross_artifact_proposer_context_evidence_binding" for check in report.checks)
    assert _bundle_check(report, "cross_artifact_capture_run_id_binding").status == "pass"


def test_reproduction_bundle_binds_proposer_round_count_to_fixed_protocol(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_round_count")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["proposer_round_count"] == 3
    assert check.metadata["protocol_self_harness_rounds"] == 3
    assert check.metadata["protocol_proposal_width"] == 2


def test_reproduction_bundle_rejects_proposer_round_count_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_fixed_protocol_rounds(artifacts, self_harness_rounds=2)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_round_count")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["proposer_round_count"] == 3
    assert check.metadata["protocol_self_harness_rounds"] == 2


def test_reproduction_bundle_rejects_proposer_attempted_proposals_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_attempted_proposals(artifacts, round_index=1, attempted_proposals=3)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_round_count")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["attempted_proposal_drift"] == [
        {"round_index": 1, "attempted_proposals": 3, "expected": 2}
    ]


def test_reproduction_bundle_binds_proposer_context_to_log_and_protocol(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["context_round_count"] == 3
    assert check.metadata["proposer_round_count"] == 3
    assert check.metadata["protocol_self_harness_rounds"] == 3
    assert check.metadata["empty_ingredient_rounds"] == []


def test_reproduction_bundle_rejects_proposer_context_presence_asymmetry(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    (artifacts / "proposer_context_manifest.json").unlink()
    bundle = _write_reproduction_bundle(tmp_path, artifacts, exclude={"proposer_context_manifest"})
    requirements = tuple(
        requirement
        for requirement in load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
        if requirement.required_artifact_class != "proposer_context_manifest"
    )

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "fail"
    assert "proposer context manifest artifact is missing" in check.detail


def test_reproduction_bundle_rejects_proposer_context_round_count_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_round_count(artifacts, 2)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["context_round_count"] == 2
    assert check.metadata["proposer_round_count"] == 3


def test_reproduction_bundle_rejects_empty_context_ingredients_for_attempted_round(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_empty_block(artifacts, round_index=1, block="held_in_failure_patterns")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["empty_ingredient_rounds"] == [
        {
            "round_index": 1,
            "attempted_proposals": 2,
            "empty_blocks": ["held_in_failure_patterns"],
        }
    ]


def test_reproduction_bundle_rejects_missing_previous_edits_after_round_zero(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_empty_block(artifacts, round_index=1, block="previous_attempted_edits")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["empty_ingredient_rounds"] == [
        {
            "round_index": 1,
            "attempted_proposals": 2,
            "empty_blocks": ["previous_attempted_edits"],
        }
    ]


def test_reproduction_bundle_accepts_empty_previous_edits_on_round_zero(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "pass"


def test_reproduction_bundle_rejects_duplicate_editable_surface_sha256(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_context_editable_surface(
        artifacts,
        round_index=0,
        sha256_value="7" * 64,
        kind="tool",
        name="system_prompt",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "duplicate editable surface" in check.detail


def test_reproduction_bundle_records_duplicate_editable_surface_sha256_cross_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_context_editable_surface(
        artifacts,
        round_index=0,
        sha256_value="7" * 64,
        kind="tool",
        name="system_prompt",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
    original_shape_error = reproduction_bundle_module.artifact_shape_error

    def bypass_proposer_context_shape(artifact_class: str, path: Path) -> str | None:
        if artifact_class == "proposer_context_manifest":
            return None
        return original_shape_error(artifact_class, path)

    monkeypatch.setattr(reproduction_bundle_module, "artifact_shape_error", bypass_proposer_context_shape)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "fail"
    assert "pairwise distinct" in check.detail
    assert check.metadata is not None
    assert check.metadata["editable_surface_duplicate_violations"] == [
        {
            "round_index": 0,
            "surface_index": 1,
            "first_seen_surface_index": 0,
            "sha256": "7" * 64,
            "name": "system_prompt",
        }
    ]


def test_reproduction_bundle_accepts_distinct_editable_surface_sha256s(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_context_editable_surface(
        artifacts,
        round_index=1,
        sha256_value="6" * 64,
        kind="tool",
        name="tool_manifest",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_context_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["editable_surface_duplicate_violations"] == []


def test_reproduction_bundle_binds_previous_edits_to_prior_context(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["rounds"][0]["edit_count"] == 0
    assert check.metadata["rounds"][1]["edits"][0]["proposal_round_index"] == 0
    assert check.metadata["rounds"][1]["edits"][0]["causal_status_sha256"] == _causal_status_hash("agent-causal")
    assert check.metadata["causal_status_violations"] == []
    assert check.metadata["previous_edit_duplicate_violations"] == []


def test_reproduction_bundle_rejects_duplicate_previous_attempted_edit_signature(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_previous_edit(artifacts, round_index=1, audit_decision_reason="duplicate summary")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "duplicate previous-attempted-edit signature" in check.detail


def test_reproduction_bundle_records_duplicate_previous_attempted_edit_signature_cross_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_previous_edit(artifacts, round_index=1, audit_decision_reason="duplicate summary")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
    original_shape_error = reproduction_bundle_module.artifact_shape_error

    def bypass_proposer_context_shape(artifact_class: str, path: Path) -> str | None:
        if artifact_class == "proposer_context_manifest":
            return None
        return original_shape_error(artifact_class, path)

    monkeypatch.setattr(reproduction_bundle_module, "artifact_shape_error", bypass_proposer_context_shape)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "fail"
    assert "pairwise distinct" in check.detail
    assert check.metadata is not None
    assert check.metadata["previous_edit_duplicate_violations"] == [
        {
            "round_index": 1,
            "edit_index": 1,
            "proposal_round_index": 0,
            "first_seen_edit_index": 0,
            "targeted_mechanism_sha256": "8" * 64,
            "edited_surface_sha256": "7" * 64,
        }
    ]


def test_reproduction_bundle_accepts_distinct_previous_attempted_edit_signatures(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_previous_edit(
        artifacts,
        round_index=2,
        proposal_round_index=0,
        audit_decision_reason="older accepted edit",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["rounds"][2]["edit_count"] == 2
    assert check.metadata["previous_edit_duplicate_violations"] == []


def test_reproduction_bundle_rejects_previous_edit_causal_status_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_previous_edit(
        artifacts,
        round_index=1,
        causal_status_sha256=_causal_status_hash("not-agent-causal"),
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "fail"
    assert "causal_status_sha256" in check.detail
    assert check.metadata is not None
    assert check.metadata["causal_status_violations"] == [
        {
            "round_index": 1,
            "edit_index": 0,
            "proposal_round_index": 0,
            "targeted_mechanism_sha256": "8" * 64,
            "causal_status_sha256": _causal_status_hash("not-agent-causal"),
            "prior_causal_status_sha256s": [_causal_status_hash("agent-causal")],
            "reasons": ["declared_causal_status_mismatch"],
        }
    ]


def test_reproduction_bundle_rejects_previous_edit_missing_prior_causal_status(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context_path = artifacts / "proposer_context_manifest.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["rounds"][0]["held_in_failure_patterns"]["patterns"][0].pop("causal_status_sha256")
    context_path.write_text(stable_json_dumps(context) + "\n", encoding="utf-8")
    _rewrite_proposer_previous_edit(
        artifacts,
        round_index=1,
        causal_status_sha256=_causal_status_hash("agent-causal"),
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "fail"
    assert "causal_status_sha256" in check.detail
    assert check.metadata is not None
    assert check.metadata["causal_status_violations"] == [
        {
            "round_index": 1,
            "edit_index": 0,
            "proposal_round_index": 0,
            "targeted_mechanism_sha256": "8" * 64,
            "causal_status_sha256": _causal_status_hash("agent-causal"),
            "prior_causal_status_sha256s": [],
            "reasons": ["missing_prior_causal_status"],
        }
    ]


def test_reproduction_bundle_accepts_context_without_causal_status_hashes(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context_path = artifacts / "proposer_context_manifest.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    for row in context["rounds"]:
        for pattern in row["held_in_failure_patterns"]["patterns"]:
            pattern.pop("causal_status_sha256", None)
        for edit in row["previous_attempted_edits"]["edits"]:
            edit.pop("causal_status_sha256", None)
    context_path.write_text(stable_json_dumps(context) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "pass"


def test_reproduction_bundle_binds_proposal_validation_to_protocol_proposer_and_context(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["validation_round_count"] == 3
    assert check.metadata["protocol_proposal_width"] == 2
    assert check.metadata["split_manifest_held_in_count"] == 32
    assert check.metadata["split_manifest_held_out_count"] == 32
    assert check.metadata["candidate_count_drift"] == []
    assert check.metadata["baseline_total_violations"] == []
    assert check.metadata["candidate_total_violations"] == []
    assert check.metadata["acceptance_rule_violations"] == []
    assert check.metadata["validation_failure_category_violations"] == []
    assert check.metadata["baseline_task_outcome_violations"] == []
    assert check.metadata["proposer_round_traffic_violations"] == []
    assert check.metadata["evaluation_repeats_mismatch_violations"] == []
    assert check.metadata["candidate_surface_name_violations"] == []
    assert check.metadata["merge_surface_conflict_violations"] == []
    assert check.metadata["lineage_continuity_violations"] == []
    assert check.metadata["lineage_continuity_skipped_rounds"] == []
    assert check.metadata["harness_continuity_violations"] == []
    assert check.metadata["harness_continuity_missing_rounds"] == []
    assert check.metadata["harness_continuity_skipped_rounds"] == []
    assert check.metadata["previous_edit_validation_violations"] == []
    assert check.metadata["rounds"][0]["harness_hashes_present"] is True
    assert check.metadata["rounds"][0]["harness_before_sha256"] == "a" * 64
    assert check.metadata["rounds"][0]["harness_after_sha256"] == "b" * 64
    assert check.metadata["rounds"][0]["baseline_task_outcomes_present"] is True
    assert check.metadata["rounds"][0]["baseline_evaluation_repeats"] == 2
    assert check.metadata["rounds"][0]["proposer_round_traffic_binding_declared"] is True
    assert check.metadata["rounds"][0]["accepted_merged_surface_sha256s"] == {"7" * 64: ["proposal-0-0"]}
    invalid_candidate = _read_proposal_validation_candidate(artifacts, round_index=1, candidate_index=1)
    assert invalid_candidate["audit_decision"] == "invalid"
    assert invalid_candidate["validation_failure_category"] == "no_editable_surface"
    assert invalid_candidate["changed_surfaces"] == []
    validation_payload = json.loads((artifacts / "proposal_validation_manifest.json").read_text(encoding="utf-8"))
    for row in validation_payload["rounds"]:
        for candidate in row["candidates"]:
            if candidate["validation_failure_category"] != "no_editable_surface":
                assert len(candidate["changed_surfaces"]) == 1


def test_reproduction_bundle_rejects_proposal_validation_unknown_current_mechanism(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_hash(
        artifacts,
        round_index=0,
        candidate_index=0,
        targeted_mechanism_sha256="9" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "same-round proposer context mechanisms" in check.detail
    assert check.metadata is not None
    assert check.metadata["candidate_mechanism_violations"] == [
        {
            "round_index": 0,
            "proposal_id": "proposal-0-0",
            "targeted_mechanism_sha256": "9" * 64,
            "allowed_mechanism_sha256s": ["8" * 64],
        }
    ]


def test_reproduction_bundle_rejects_proposal_validation_unknown_current_surface(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_hash(
        artifacts,
        round_index=0,
        candidate_index=0,
        edited_surface_sha256="6" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "same-round editable surfaces" in check.detail
    assert check.metadata is not None
    assert check.metadata["candidate_surface_violations"] == [
        {
            "round_index": 0,
            "proposal_id": "proposal-0-0",
            "edited_surface_sha256": "6" * 64,
            "allowed_surface_sha256s": ["7" * 64],
        }
    ]


def test_reproduction_bundle_rejects_proposal_validation_unknown_current_surface_name(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=0,
        candidate_index=0,
        changed_surfaces=["undeclared-surface"],
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "changed_surfaces must exist" in check.detail
    assert check.metadata is not None
    assert check.metadata["candidate_surface_violations"] == []
    assert check.metadata["candidate_surface_name_violations"] == [
        {
            "round_index": 0,
            "proposal_id": "proposal-0-0",
            "changed_surfaces": ["undeclared-surface"],
            "unknown_surface_names": ["undeclared-surface"],
            "allowed_surface_names": ["system_prompt"],
        }
    ]


def test_reproduction_bundle_rejects_duplicate_proposal_validation_candidate_signature(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_hash(
        artifacts,
        round_index=0,
        candidate_index=1,
        targeted_mechanism_sha256="8" * 64,
        edited_surface_sha256="7" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "materially distinct" in check.detail
    assert check.metadata is not None
    violation = check.metadata["candidate_distinctness_violations"][0]
    assert violation["round_index"] == 0
    assert violation["duplicate_signatures"] == [
        {
            "proposal_id": "proposal-0-1",
            "targeted_mechanism_sha256": "8" * 64,
            "edited_surface_sha256": "7" * 64,
        }
    ]


def test_reproduction_bundle_rejects_accepted_proposal_validation_surface_conflict(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=0,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-01"],
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="a" * 64,
        task_ids=["tb-held-in-02"],
    )
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=0,
        candidate_index=1,
        audit_decision="accepted",
        validation_failure_category=None,
        changed_surfaces=["system_prompt"],
        edited_surface_sha256="7" * 64,
        targeted_mechanism_sha256="9" * 64,
        decision_reason="candidate passed validation",
        rejection_reason=None,
    )
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="candidate",
        candidate_index=1,
        held_in_passed=31,
    )
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=0,
        committed_proposal_ids=["proposal-0-0", "proposal-0-1"],
        merge_decision="accepted",
        harness_after_merged_sha256="b" * 64,
        merged_split_outcomes={
            "held_in_passed": 30,
            "held_in_total": 32,
            "held_out_passed": 32,
            "held_out_total": 32,
            "evaluation_repeats": 2,
        },
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "pairwise-distinct editable surfaces" in check.detail
    assert check.metadata is not None
    assert check.metadata["candidate_distinctness_violations"] == []
    assert check.metadata["merge_surface_conflict_violations"] == [
        {
            "round_index": 0,
            "edited_surface_sha256": "7" * 64,
            "proposal_ids": ["proposal-0-0", "proposal-0-1"],
        }
    ]


def test_reproduction_bundle_ignores_non_accepted_proposal_validation_surface_overlap(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=0,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-01"],
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="a" * 64,
        task_ids=["tb-held-in-02"],
    )
    _rewrite_proposal_validation_candidate_hash(
        artifacts,
        round_index=0,
        candidate_index=1,
        targeted_mechanism_sha256="9" * 64,
        edited_surface_sha256="7" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["candidate_distinctness_violations"] == []
    assert check.metadata["merge_surface_conflict_violations"] == []


def test_reproduction_bundle_rejects_proposal_validation_proposer_traffic_hash_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=0,
        proposer_round_request_sha256="f" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "proposer-round traffic hashes" in check.detail
    assert check.metadata is not None
    assert check.metadata["proposer_round_traffic_violations"] == [
        {
            "round_index": 0,
            "reasons": ["request_sha256_mismatch"],
            "validation_request_sha256": "f" * 64,
            "proposer_request_sha256": "1" * 64,
            "validation_response_sha256": "2" * 64,
            "proposer_response_sha256": "2" * 64,
        }
    ]


def test_reproduction_bundle_allows_legacy_proposal_validation_without_proposer_traffic_binding(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _remove_proposal_validation_round_traffic_fields(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["proposer_round_traffic_violations"] == []
    assert check.metadata["rounds"][0]["proposer_round_traffic_binding_declared"] is False


def test_reproduction_bundle_rejects_proposer_failure_missing_from_baseline_task_outcomes(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_baseline_task_outcome(
        artifacts,
        round_index=0,
        task_id="tb-held-in-00",
        passed=True,
    )
    _rewrite_proposal_validation_baseline_task_outcome(
        artifacts,
        round_index=0,
        task_id="tb-held-in-01",
        passed=False,
    )
    _rewrite_proposal_validation_baseline_counts(
        artifacts,
        round_index=0,
        held_in_passed=30,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "baseline task outcomes" in check.detail
    assert check.metadata is not None
    assert check.metadata["baseline_task_outcome_violations"] == [
        {
            "round_index": 0,
            "cluster_id": "cluster-0",
            "missing_baseline_failing_task_ids": ["tb-held-in-00"],
            "baseline_failing_held_in_task_ids": ["tb-held-in-01", "tb-held-in-02"],
        }
    ]


def test_reproduction_bundle_rejects_accepted_proposal_validation_held_in_regression(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="candidate",
        candidate_index=0,
        held_in_passed=28,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["acceptance_rule_violations"] == [
        {
            "round_index": 0,
                "proposal_id": "proposal-0-0",
                "audit_decision": "accepted",
                "reasons": ["held_in_regression"],
                "baseline_held_in_passed": 29,
                "candidate_held_in_passed": 28,
                "baseline_held_out_passed": 32,
                "candidate_held_out_passed": 32,
            }
    ]


def test_reproduction_bundle_rejects_accepted_proposal_validation_held_out_regression(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="candidate",
        candidate_index=0,
        held_out_passed=31,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["acceptance_rule_violations"][0]["reasons"] == ["held_out_regression"]


def test_reproduction_bundle_rejects_accepted_proposal_validation_without_improvement(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="candidate",
        candidate_index=0,
        held_in_passed=29,
        held_out_passed=32,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["acceptance_rule_violations"][0]["reasons"] == ["no_improvement"]


def test_reproduction_bundle_exempts_rejected_proposal_validation_candidates_from_acceptance_rule(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=1,
        target="candidate",
        candidate_index=1,
        held_in_passed=0,
        held_out_passed=0,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["acceptance_rule_violations"] == []


def test_reproduction_bundle_rejects_non_invalid_candidate_with_validation_failure_category(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=0,
        candidate_index=0,
        validation_failure_category="execution_failure",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "validation_failure_category must be null unless audit_decision is invalid" in check.detail


def test_reproduction_bundle_rejects_invalid_candidate_missing_validation_failure_category(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=1,
        candidate_index=1,
        validation_failure_category=None,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "validation_failure_category must be non-null for invalid candidates" in check.detail


def test_reproduction_bundle_rejects_no_surface_invalid_candidate_with_changed_surfaces(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=1,
        candidate_index=1,
        changed_surfaces=["system_prompt"],
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "changed_surfaces must be empty for no_editable_surface" in check.detail


def test_reproduction_bundle_rejects_multi_surface_proposal_validation_candidate(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=1,
        candidate_index=1,
        changed_surfaces=["system_prompt", "tool_manifest"],
        validation_failure_category="execution_failure",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "changed_surfaces must contain exactly one surface" in check.detail


def test_reproduction_bundle_exempts_execution_failure_invalid_candidate_from_acceptance_rule(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_context_editable_surface(artifacts, round_index=1, sha256_value="6" * 64)
    _rewrite_proposal_validation_candidate_fields(
        artifacts,
        round_index=1,
        candidate_index=1,
        changed_surfaces=["system_prompt"],
        validation_failure_category="execution_failure",
        edited_surface_sha256="6" * 64,
    )
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=1,
        target="candidate",
        candidate_index=1,
        held_in_passed=0,
        held_out_passed=0,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["acceptance_rule_violations"] == []
    assert check.metadata["validation_failure_category_violations"] == []
    assert check.metadata["candidate_surface_violations"] == []
    assert check.metadata["candidate_distinctness_violations"] == []


def test_reproduction_bundle_rejects_proposal_validation_baseline_total_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="baseline",
        held_in_total=31,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "split totals" in check.detail
    assert check.metadata is not None
    assert check.metadata["baseline_total_violations"] == [
        {
            "round_index": 0,
            "held_in_total": 31,
            "held_out_total": 32,
            "expected_held_in_total": 32,
            "expected_held_out_total": 32,
        }
    ]


def test_reproduction_bundle_rejects_proposal_validation_candidate_total_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=1,
        target="candidate",
        candidate_index=1,
        held_out_passed=31,
        held_out_total=31,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["candidate_total_violations"] == [
        {
            "round_index": 1,
            "proposal_id": "proposal-1-1",
            "held_in_total": 32,
            "held_out_total": 31,
            "expected_held_in_total": 32,
            "expected_held_out_total": 32,
        }
    ]


def test_reproduction_bundle_rejects_candidate_evaluation_repeats_mismatch(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="baseline",
        evaluation_repeats=1,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "evaluation_repeats must match baseline_split_outcomes.evaluation_repeats" in check.detail


def test_reproduction_bundle_records_candidate_evaluation_repeats_mismatch_cross_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=0,
        target="baseline",
        evaluation_repeats=1,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
    original_shape_error = reproduction_bundle_module.artifact_shape_error

    def bypass_proposal_validation_shape(artifact_class: str, path: Path) -> str | None:
        if artifact_class == "proposal_validation_manifest":
            return None
        return original_shape_error(artifact_class, path)

    monkeypatch.setattr(reproduction_bundle_module, "artifact_shape_error", bypass_proposal_validation_shape)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "candidate evaluation_repeats must match baseline evaluation_repeats" in check.detail
    assert check.metadata is not None
    assert check.metadata["evaluation_repeat_drift"] == []
    assert check.metadata["evaluation_repeats_mismatch_violations"] == [
        {
            "round_index": 0,
            "proposal_id": "proposal-0-0",
            "baseline_evaluation_repeats": 1,
            "candidate_evaluation_repeats": 2,
        },
        {
            "round_index": 0,
            "proposal_id": "proposal-0-1",
            "baseline_evaluation_repeats": 1,
            "candidate_evaluation_repeats": 2,
        },
    ]


def test_reproduction_bundle_accepts_uniform_evaluation_repeats(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["evaluation_repeats_mismatch_violations"] == []
    assert check.metadata["rounds"][0]["baseline_evaluation_repeats"] == 2


def test_reproduction_bundle_rejects_unpaired_proposal_validation_harness_hash(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=1,
        harness_after_sha256=None,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "harness_before_sha256 and harness_after_sha256 must be present together" in check.detail


def test_reproduction_bundle_accepts_legacy_proposal_validation_without_harness_hashes(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _remove_proposal_validation_harness_hash_fields(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["harness_continuity_violations"] == []
    assert check.metadata["harness_continuity_missing_rounds"] == []
    assert check.metadata["rounds"][0]["harness_hashes_present"] is False


def test_reproduction_bundle_rejects_proposal_validation_without_split_manifest(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts, exclude={"live_terminal_bench_split_manifest"})
    requirements = [
        requirement
        for requirement in load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
        if requirement.required_artifact_class != "live_terminal_bench_split_manifest"
    ]

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert check.detail == "live Terminal-Bench split manifest artifact is missing"


def test_reproduction_bundle_does_not_bind_proposal_validation_pass_counts_to_final_evaluation(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["baseline_total_violations"] == []
    assert check.metadata["acceptance_rule_violations"] == []
    assert check.metadata["rounds"][0]["baseline_held_in_passed"] == 29
    assert check.metadata["rounds"][1]["baseline_held_in_passed"] == 30


def test_reproduction_bundle_rejects_proposal_validation_lineage_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_split_outcome(
        artifacts,
        round_index=1,
        target="baseline",
        held_in_passed=29,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "baselines must follow prior committed validation state" in check.detail
    assert check.metadata is not None
    assert check.metadata["lineage_continuity_violations"] == [
        {
            "round_index": 1,
            "previous_round_index": 0,
            "expected_source": {
                "kind": "single_committed_candidate",
                "proposal_id": "proposal-0-0",
            },
            "expected": {
                "held_in_passed": 30,
                "held_in_total": 32,
                "held_out_passed": 32,
                "held_out_total": 32,
                "evaluation_repeats": 2,
            },
            "actual": {
                "held_in_passed": 29,
                "held_in_total": 32,
                "held_out_passed": 32,
                "held_out_total": 32,
                "evaluation_repeats": 2,
            },
        }
    ]


def test_reproduction_bundle_rejects_proposal_validation_harness_hash_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=1,
        harness_before_sha256="0" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "harness hashes must follow prior committed validation state" in check.detail
    assert check.metadata is not None
    assert check.metadata["harness_continuity_violations"] == [
        {
            "round_index": 1,
            "previous_round_index": 0,
            "expected_source": {
                "kind": "single_committed_harness_state",
                "proposal_id": "proposal-0-0",
            },
            "expected_harness_before_sha256": "b" * 64,
            "actual_harness_before_sha256": "0" * 64,
        }
    ]


def test_reproduction_bundle_accepts_multi_commit_harness_merged_hash(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["harness_merged_hash_violations"] == []
    assert check.metadata["harness_continuity_violations"] == []
    assert check.metadata["harness_continuity_skipped_rounds"] == []
    assert check.metadata["lineage_continuity_violations"] == []
    assert check.metadata["lineage_continuity_skipped_rounds"] == []
    assert check.metadata["merged_split_outcome_lineage_closed_rounds"] == [
        {
            "round_index": 1,
            "previous_round_index": 0,
            "committed_proposal_ids": ["proposal-0-0", "proposal-0-1"],
        }
    ]
    assert check.metadata["rounds"][0]["harness_after_merged_sha256"] == "b" * 64
    assert check.metadata["rounds"][0]["merged_split_outcomes_present"] is True
    assert check.metadata["rounds"][0]["merged_split_outcomes"] == {
        "held_in_passed": 30,
        "held_in_total": 32,
        "held_out_passed": 32,
        "held_out_total": 32,
        "evaluation_repeats": 2,
    }


def test_reproduction_bundle_rejects_multi_commit_merged_split_outcome_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    validation_path = artifacts / "proposal_validation_manifest.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["rounds"][0]["merged_split_outcomes"] = dict(
        validation["rounds"][0]["baseline_split_outcomes"]
    )
    validation_path.write_text(stable_json_dumps(validation) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "baselines must follow prior committed validation state" in check.detail
    assert check.metadata is not None
    assert check.metadata["lineage_continuity_violations"] == [
        {
            "round_index": 1,
            "previous_round_index": 0,
            "expected_source": {
                "kind": "merged_split_outcomes",
                "proposal_ids": ["proposal-0-0", "proposal-0-1"],
            },
            "expected": {
                "held_in_passed": 29,
                "held_in_total": 32,
                "held_out_passed": 32,
                "held_out_total": 32,
                "evaluation_repeats": 2,
            },
            "actual": {
                "held_in_passed": 30,
                "held_in_total": 32,
                "held_out_passed": 32,
                "held_out_total": 32,
                "evaluation_repeats": 2,
            },
        }
    ]


def test_reproduction_bundle_rejects_multi_commit_harness_merged_hash_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=1,
        harness_before_sha256="0" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert "harness hashes must follow prior committed validation state" in check.detail
    assert check.metadata is not None
    assert check.metadata["harness_continuity_violations"] == [
        {
            "round_index": 1,
            "previous_round_index": 0,
            "expected_source": {
                "kind": "multi_committed_harness_state",
                "proposal_ids": ["proposal-0-0", "proposal-0-1"],
            },
            "expected_harness_before_sha256": "b" * 64,
            "actual_harness_before_sha256": "0" * 64,
        }
    ]


def test_reproduction_bundle_rejects_merged_harness_hash_on_single_commit_round(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=0,
        harness_after_merged_sha256="b" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "harness_after_merged_sha256 is only valid for multi-commit rounds" in check.detail


def test_reproduction_bundle_rejects_merged_split_outcomes_on_single_commit_round(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    validation_path = artifacts / "proposal_validation_manifest.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["rounds"][0]["merged_split_outcomes"] = dict(
        validation["rounds"][0]["candidates"][0]["split_outcomes"]
    )
    validation_path.write_text(stable_json_dumps(validation) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "merged_split_outcomes is only valid for multi-commit rounds" in check.detail


def test_reproduction_bundle_rejects_multi_commit_without_merged_harness_hash(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    _rewrite_proposal_validation_round_fields(
        artifacts,
        round_index=0,
        harness_after_merged_sha256=None,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "harness_after_merged_sha256 is required for multi-commit rounds with harness hashes" in check.detail


def test_reproduction_bundle_rejects_multi_commit_without_merged_split_outcomes(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    validation_path = artifacts / "proposal_validation_manifest.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["rounds"][0].pop("merged_split_outcomes")
    validation_path.write_text(stable_json_dumps(validation) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposal_validation_manifest")
    assert check.status == "fail"
    assert "merged_split_outcomes is required for multi-commit rounds with harness hashes" in check.detail


def test_reproduction_bundle_accepts_legacy_multi_commit_without_harness_hashes(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    _remove_proposal_validation_harness_hash_fields(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["harness_merged_hash_violations"] == []
    assert check.metadata["harness_continuity_violations"] == []
    assert check.metadata["lineage_continuity_skipped_rounds"] == [
        {
            "round_index": 1,
            "previous_round_index": 0,
            "reason": "missing_merged_split_outcomes",
            "committed_proposal_ids": ["proposal-0-0", "proposal-0-1"],
        }
    ]
    assert check.metadata["rounds"][0]["harness_hashes_present"] is False
    assert check.metadata["rounds"][0]["merged_split_outcomes_present"] is False


def test_reproduction_bundle_rejects_proposal_validation_previous_edit_hash_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_candidate_hash(
        artifacts,
        round_index=0,
        candidate_index=0,
        targeted_mechanism_sha256="9" * 64,
    )
    _rewrite_proposal_validation_candidate_hash(
        artifacts,
        round_index=0,
        candidate_index=1,
        targeted_mechanism_sha256="9" * 64,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposal_validation_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["previous_edit_validation_violations"][0]["proposal_round_index"] == 0


def test_reproduction_bundle_rejects_previous_edit_current_round_reference(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_previous_edit(artifacts, round_index=1, proposal_round_index=1)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["future_or_current_references"] == [
        {"round_index": 1, "edit_index": 0, "proposal_round_index": 1}
    ]


def test_reproduction_bundle_rejects_previous_edit_unknown_mechanism(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_previous_edit(artifacts, round_index=1, targeted_mechanism_sha256="9" * 64)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["unknown_targeted_mechanisms"][0]["targeted_mechanism_sha256"] == "9" * 64


def test_reproduction_bundle_rejects_previous_edit_unknown_surface(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_previous_edit(artifacts, round_index=1, edited_surface_sha256="6" * 64)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_previous_edits_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["unknown_edited_surfaces"][0]["edited_surface_sha256"] == "6" * 64


def test_reproduction_bundle_rejects_bad_previous_edit_audit_decision(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_previous_edit(artifacts, round_index=1, audit_decision="maybe")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "audit_decision" in check.detail


def test_reproduction_bundle_rejects_rejected_previous_edit_without_reason(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_previous_edit(artifacts, round_index=1, audit_decision="rejected", audit_decision_reason="")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "audit_decision_reason" in check.detail


def test_reproduction_bundle_rejects_duplicate_proposer_context_failure_signature(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=0,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="8" * 64,
        task_ids=["tb-held-in-01"],
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "duplicate failure signature" in check.detail


def test_reproduction_bundle_accepts_distinct_proposer_context_failure_signatures(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=0,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-01"],
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="a" * 64,
        task_ids=["tb-held-in-02"],
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "pass"


def test_reproduction_bundle_rejects_proposer_context_support_ordering_violation(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00", "tb-held-in-01"],
        size=2,
        presentation_order=1,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-extra"],
        size=1,
        presentation_order=0,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "support-rank ordering violation" in check.detail


def test_reproduction_bundle_accepts_equal_size_proposer_context_actionability_order(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=1,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-01"],
        size=1,
        presentation_order=0,
    )
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="a" * 64,
        task_ids=["tb-held-in-02"],
        size=1,
        presentation_order=2,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "pass"


def test_proposer_context_shape_accepts_distinct_size_support_ordering() -> None:
    payload = _class_shaped_payloads()["proposer_context_manifest"]
    block = payload["rounds"][0]["held_in_failure_patterns"]
    patterns = block["patterns"]
    patterns[0]["task_ids"] = ["task-a", "task-b"]
    patterns[0]["size"] = 2
    patterns[0]["presentation_order"] = 0
    pattern = dict(patterns[0])
    pattern["cluster_id"] = "cluster-extra"
    pattern["mechanism_sha256"] = "9" * 64
    pattern["task_ids"] = ["task-c"]
    pattern["size"] = 1
    pattern["presentation_order"] = 1
    patterns.append(pattern)
    block["pattern_count"] = len(patterns)

    error = artifact_shape_error_from_payload("proposer_context_manifest", payload)

    assert error is None


def test_reproduction_bundle_rejects_proposer_context_failure_pattern_task_overlap(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=1,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "task-id overlap violation" in check.detail


def test_reproduction_bundle_records_proposer_context_failure_pattern_task_overlap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _add_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        mechanism_sha256="9" * 64,
        task_ids=["tb-held-in-00"],
        size=1,
        presentation_order=1,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
    original_shape_error = reproduction_bundle_module.artifact_shape_error

    def bypass_proposer_context_shape(artifact_class: str, path: Path) -> str | None:
        if artifact_class == "proposer_context_manifest":
            return None
        return original_shape_error(artifact_class, path)

    monkeypatch.setattr(reproduction_bundle_module, "artifact_shape_error", bypass_proposer_context_shape)

    report = verify_reproduction_bundle(bundle, requirements)

    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["failure_pattern_task_overlap_violations"] == [
        {
            "round_index": 0,
            "overlapping_task_ids": [
                {"task_id": "tb-held-in-00", "clusters": ["cluster-0", "cluster-0-extra"]}
            ],
        }
    ]


def test_reproduction_bundle_binds_proposer_context_to_held_in_evidence(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["baseline_rounds"][0]["baseline_held_in_failing_task_ids"] == [
        "tb-held-in-00",
        "tb-held-in-01",
        "tb-held-in-02",
    ]
    assert check.metadata["baseline_rounds"][0]["baseline_held_in_failure_categories_by_task"] == {
        "tb-held-in-00": ["assertion-fail"],
        "tb-held-in-01": ["assertion-fail"],
        "tb-held-in-02": ["assertion-fail"],
    }
    assert check.metadata["baseline_rounds"][1]["baseline_held_in_failing_task_ids"] == [
        "tb-held-in-01",
        "tb-held-in-02",
    ]
    assert "tb-held-in-00" in check.metadata["baseline_rounds"][1]["baseline_held_in_passing_task_ids"]
    assert check.metadata["opaque_shared_symptoms_sha256_count"] == 3
    assert check.metadata["opaque_verifier_evidence_sha256_count"] == 3
    assert check.metadata["presentation_order_declared_count"] == 3
    assert check.metadata["actionability_hint_sha256_count"] == 3
    assert check.metadata["presentation_order_violations"] == []
    assert check.metadata["failure_pattern_task_overlap_violations"] == []
    assert check.metadata["failure_pattern_rounds"][0]["patterns"][0]["shared_symptoms_sha256"] == _evidence_hash(
        "shared_symptoms",
        ["assertion mismatch", "same verifier failure"],
    )
    assert check.metadata["failure_pattern_rounds"][0]["patterns"][0]["verifier_evidence_sha256"] == _evidence_hash(
        "verifier_evidence",
        ["terminal-bench verifier failed"],
    )
    assert check.metadata["failure_pattern_rounds"][0]["patterns"][0]["presentation_order"] == 0
    assert check.metadata["failure_pattern_rounds"][0]["patterns"][0]["actionability_hint_sha256"] == _evidence_hash(
        "actionability_hint",
        "high support, high actionability",
    )
    assert check.metadata["failure_pattern_category_violations"] == []
    assert check.metadata["passing_summary_hash_violations"] == []


def test_reproduction_bundle_accepts_context_without_failure_pattern_evidence_hashes(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context_path = artifacts / "proposer_context_manifest.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    for row in context["rounds"]:
        for pattern in row["held_in_failure_patterns"]["patterns"]:
            pattern.pop("shared_symptoms_sha256", None)
            pattern.pop("verifier_evidence_sha256", None)
            pattern.pop("presentation_order", None)
            pattern.pop("actionability_hint_sha256", None)
    context_path.write_text(stable_json_dumps(context) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["opaque_shared_symptoms_sha256_count"] == 0
    assert check.metadata["opaque_verifier_evidence_sha256_count"] == 0
    assert check.metadata["presentation_order_declared_count"] == 0
    assert check.metadata["actionability_hint_sha256_count"] == 0


def test_reproduction_bundle_rejects_invalid_failure_pattern_presentation_order(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context_path = artifacts / "proposer_context_manifest.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["rounds"][0]["held_in_failure_patterns"]["patterns"][0]["presentation_order"] = 1
    context_path.write_text(stable_json_dumps(context) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "presentation_order" in check.detail


def test_reproduction_bundle_requires_baseline_task_outcomes_for_proposer_context(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _remove_proposal_validation_baseline_task_outcomes(artifacts, round_index=0)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert "baselines must disclose task outcomes" in check.detail
    assert check.metadata is not None
    assert check.metadata["missing_baseline_task_outcome_rounds"] == [0]


def test_reproduction_bundle_rejects_failure_pattern_task_outside_held_in_failures(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(artifacts, round_index=0, task_ids=["tb-held-out-00"])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert "held-in failure pattern task_ids" in check.detail
    assert check.metadata is not None
    assert check.metadata["failure_pattern_task_id_violations"] == [
        {"round_index": 0, "cluster_id": "cluster-0", "unexpected_task_ids": ["tb-held-out-00"]}
    ]


def test_reproduction_bundle_rejects_failure_pattern_size_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00"],
        size=2,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["failure_pattern_size_violations"] == [
        {"round_index": 0, "cluster_id": "cluster-0", "size": 2, "task_id_count": 1}
    ]


def test_reproduction_bundle_rejects_failure_pattern_union_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_empty_block(artifacts, round_index=0, block="held_in_failure_patterns")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["failure_pattern_union_violations"][0] == {
        "round_index": 0,
        "missing_task_ids": ["tb-held-in-00", "tb-held-in-01", "tb-held-in-02"],
        "extra_task_ids": [],
    }


def test_reproduction_bundle_rejects_failure_pattern_category_mismatch(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_failure_pattern(
        artifacts,
        round_index=0,
        task_ids=["tb-held-in-00", "tb-held-in-01", "tb-held-in-02"],
        failure_category="timeout",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert "failure_category" in check.detail
    assert check.metadata is not None
    assert check.metadata["failure_pattern_category_violations"] == [
        {
            "round_index": 0,
            "cluster_id": "cluster-0",
            "reasons": ["declared_failure_category_mismatch"],
            "failure_category": "timeout",
            "baseline_failure_categories": ["assertion-fail"],
            "task_ids": ["tb-held-in-00", "tb-held-in-01", "tb-held-in-02"],
        }
    ]


def test_reproduction_bundle_rejects_failure_pattern_mixed_baseline_categories(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposal_validation_baseline_task_outcome(
        artifacts,
        round_index=0,
        task_id="tb-held-in-01",
        passed=False,
        failure_category="timeout",
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    violation = check.metadata["failure_pattern_category_violations"][0]
    assert violation["round_index"] == 0
    assert violation["cluster_id"] == "cluster-0"
    assert violation["reasons"] == [
        "mixed_baseline_failure_categories",
        "declared_failure_category_mismatch",
    ]
    assert violation["baseline_failure_categories"] == ["assertion-fail", "timeout"]


def test_reproduction_bundle_rejects_passing_summary_task_outside_held_in_passes(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_passing_summary(artifacts, round_index=0, task_ids=["tb-held-out-00"])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["passing_summary_task_id_violations"] == [
        {"round_index": 0, "summary_index": 0, "unexpected_task_ids": ["tb-held-out-00"]}
    ]


def test_reproduction_bundle_rejects_passing_summary_union_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_passing_summary(artifacts, round_index=1, task_ids=["tb-held-in-01"])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    violation = check.metadata["passing_summary_union_violations"][0]
    assert violation["round_index"] == 1
    assert "tb-held-in-00" in violation["missing_task_ids"]
    assert violation["extra_task_ids"] == ["tb-held-in-01"]


def test_reproduction_bundle_rejects_passing_summary_task_id_hash_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_proposer_context_passing_hash(artifacts, round_index=0, task_id_set_sha256="9" * 64)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_proposer_context_evidence_binding")
    assert check.status == "fail"
    assert "task_id_set_sha256" in check.detail
    assert check.metadata is not None
    assert check.metadata["passing_summary_hash_violations"][0]["actual"] == "9" * 64


def test_reproduction_bundle_rejects_malformed_proposer_context_hash(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    path = artifacts / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rounds"][0]["editable_surfaces"]["surfaces"][0]["sha256"] = "a" * 63
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_proposer_context_manifest")
    assert check.status == "fail"
    assert "sha256" in check.detail


def test_reproduction_bundle_binds_live_audit_image_digest_to_trust_report(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, AUDIT_IMAGE_DIGEST)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_audit_image_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["audit_image_digests"] == [AUDIT_IMAGE_DIGEST]
    assert check.metadata["trust_image_binding_mode"] == "manifest-digests"
    assert check.metadata["trust_image_digests"] == [AUDIT_IMAGE_DIGEST]
    assert "trust_child_digests" not in check.metadata


def test_reproduction_bundle_binds_live_audit_image_digest_to_trust_child_digests(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, CHILD_AUDIT_IMAGE_DIGEST)
    _rewrite_container_image_trust_children(
        artifacts,
        [CHILD_AUDIT_IMAGE_DIGEST, OTHER_CHILD_AUDIT_IMAGE_DIGEST],
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is True
    check = _bundle_check(report, "cross_artifact_audit_image_binding")
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["trust_image_binding_mode"] == "child-digests"
    assert check.metadata["trust_child_digests"] == [
        CHILD_AUDIT_IMAGE_DIGEST,
        OTHER_CHILD_AUDIT_IMAGE_DIGEST,
    ]
    assert check.metadata["missing_from_trust_children"] == []
    assert check.metadata["extra_in_trust_children"] == [OTHER_CHILD_AUDIT_IMAGE_DIGEST]


def test_reproduction_bundle_rejects_live_audit_child_digest_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, OTHER_AUDIT_IMAGE_DIGEST)
    _rewrite_container_image_trust_children(artifacts, [CHILD_AUDIT_IMAGE_DIGEST])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_audit_image_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["missing_from_trust_children"] == [OTHER_AUDIT_IMAGE_DIGEST]


def test_reproduction_bundle_rejects_mixed_child_digest_declarations(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, CHILD_AUDIT_IMAGE_DIGEST)
    _rewrite_container_image_trust_children(
        artifacts,
        [CHILD_AUDIT_IMAGE_DIGEST],
        add_image_without_children=True,
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_audit_image_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["mixed_child_digest_declarations"] == {
        "with_child_digests": ["registry.example/terminal-bench/agent"],
        "without_child_digests": ["registry.example/terminal-bench/sidecar"],
    }


def test_reproduction_bundle_rejects_live_audit_image_digest_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, OTHER_AUDIT_IMAGE_DIGEST)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_audit_image_binding")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["missing_from_trust"] == [OTHER_AUDIT_IMAGE_DIGEST]
    assert check.metadata["extra_in_trust"] == [AUDIT_IMAGE_DIGEST]


def test_reproduction_bundle_rejects_two_repeat_subset_of_split(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_two_repeat_artifact(artifacts, _split_task_ids()[:63])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_split_evaluation_coverage")
    assert check.status == "fail"
    assert "task_count must be 64" in check.detail
    assert "task ids must equal" in check.detail


def test_reproduction_bundle_rejects_two_repeat_disjoint_split_ids(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_two_repeat_artifact(artifacts, [f"foreign-task-{index:02d}" for index in range(64)])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_split_evaluation_coverage")
    assert check.status == "fail"
    assert "task ids must equal" in check.detail


def test_reproduction_bundle_rejects_two_repeat_same_count_id_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    task_ids = _split_task_ids()
    task_ids[-1] = "tb-unplanned-extra"
    _rewrite_two_repeat_artifact(artifacts, task_ids)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_split_evaluation_coverage")
    assert check.status == "fail"
    assert "task ids must equal" in check.detail
    assert check.metadata is not None
    assert check.metadata["missing"] == ["tb-held-out-31"]
    assert check.metadata["extra"] == ["tb-unplanned-extra"]


def test_reproduction_bundle_rejects_live_audit_subset_of_split(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_live_harbor_audit(artifacts, _split_task_ids()[:63])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_audit_split_coverage")
    assert check.status == "fail"
    assert "live Harbor audit task ids must equal split manifest ids" in check.detail


def test_reproduction_bundle_rejects_live_audit_extra_task(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_live_harbor_audit(artifacts, [*_split_task_ids(), "tb-unplanned-extra"])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_audit_split_coverage")
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["extra"] == ["tb-unplanned-extra"]


def test_reproduction_bundle_rejects_evaluation_audit_outcome_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    task_id = _split_task_ids()[0]
    _rewrite_evaluation_attempts(artifacts, task_id, [False, False])
    _rewrite_audit_attempts(artifacts, task_id, [True, True])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_evaluation_audit_outcomes")
    assert check.status == "fail"
    assert "attempt pass values" in check.detail
    assert "verifier_outcome" in check.detail
    assert check.metadata is not None
    assert check.metadata["verifier_outcome_mismatches"] == [
        {"actual": "pass", "expected": "fail", "task_id": task_id}
    ]


def test_reproduction_bundle_rejects_evaluation_audit_attempt_order_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    task_id = _split_task_ids()[0]
    _rewrite_evaluation_attempts(artifacts, task_id, [False, True])
    _rewrite_audit_attempts(artifacts, task_id, [True, False])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_evaluation_audit_outcomes")
    assert check.status == "fail"
    assert "attempt pass values" in check.detail
    assert check.metadata is not None
    assert check.metadata["per_attempt_mismatches"] == [
        {"audit_pass": True, "attempt_index": 0, "evaluation_pass": False, "task_id": task_id},
        {"audit_pass": False, "attempt_index": 1, "evaluation_pass": True, "task_id": task_id},
    ]
    assert check.metadata["verifier_outcome_mismatches"] == []


def test_reproduction_bundle_keeps_audit_coverage_failure_visible_when_outcomes_missing_task(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_live_harbor_audit(artifacts, _split_task_ids()[:63])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    coverage_check = _bundle_check(report, "cross_artifact_audit_split_coverage")
    outcome_check = _bundle_check(report, "cross_artifact_evaluation_audit_outcomes")
    assert coverage_check.status == "fail"
    assert outcome_check.status == "fail"
    assert outcome_check.metadata is not None
    assert outcome_check.metadata["missing_from_audit"] == ["tb-held-out-31"]


def test_reproduction_bundle_rejects_live_audit_attempt_count_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_live_harbor_audit(artifacts, _split_task_ids(), attempts_per_task=1)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "artifact_live_harbor_audit")
    assert check.status == "fail"
    assert "attempts must contain exactly 2 attempts" in check.detail


def test_reproduction_bundle_rejects_two_repeat_protocol_hash_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_fixed_protocol_hash(artifacts, "live_two_repeat_evaluation_report", "0" * 64)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_protocol_binding")
    assert check.status == "fail"
    assert "live two-repeat evaluation report fixed_protocol_sha256" in check.detail


def test_reproduction_bundle_rejects_live_audit_protocol_hash_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_fixed_protocol_hash(artifacts, "live_harbor_audit", "0" * 64)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    report = verify_reproduction_bundle(bundle, requirements)

    assert report.ok is False
    check = _bundle_check(report, "cross_artifact_protocol_binding")
    assert check.status == "fail"
    assert "live Harbor audit fixed_protocol_sha256" in check.detail


def test_reproduction_bundle_rejects_duplicate_class_digest_mismatch_and_required_signature(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)

    duplicate = _write_reproduction_bundle(tmp_path, artifacts, name="duplicate-bundle")
    duplicate_payload = json.loads(duplicate.read_text(encoding="utf-8"))
    duplicate_payload["entries"].append(dict(duplicate_payload["entries"][0]))
    duplicate.write_text(stable_json_dumps(duplicate_payload) + "\n", encoding="utf-8")
    duplicate_report = verify_reproduction_bundle(duplicate, requirements)
    assert duplicate_report.ok is False
    assert any("duplicate class" in check.detail for check in duplicate_report.checks)

    digest_mismatch = _write_reproduction_bundle(tmp_path, artifacts, name="digest-mismatch-bundle")
    mismatch_payload = json.loads(digest_mismatch.read_text(encoding="utf-8"))
    mismatch_payload["entries"][0]["sha256"] = "0" * 64
    digest_mismatch.write_text(stable_json_dumps(mismatch_payload) + "\n", encoding="utf-8")
    digest_report = verify_reproduction_bundle(digest_mismatch, requirements)
    assert digest_report.ok is False
    assert any("sha256 mismatch" in check.detail for check in digest_report.checks)

    signature_report = verify_reproduction_bundle(
        _write_reproduction_bundle(tmp_path, artifacts, name="unsigned-bundle"),
        requirements,
        require_signature=True,
    )
    assert signature_report.ok is False
    assert any("signature is required" in check.detail for check in signature_report.checks)


def test_reproduction_bundle_cli_and_shape_lint_use_bundle(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    out = tmp_path / "bundle-report.json"

    completed = _run_bundle_verify("--bundle", str(bundle), "--out", str(out))
    lint = _run_shape_lint("--reproduction-bundle", str(bundle))

    assert completed.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is True
    assert lint.returncode == 0
    assert json.loads(lint.stdout)["artifact_shapes_ready"] is True


def test_reproduction_bundle_build_script_is_deterministic_and_verifies(tmp_path: Path) -> None:
    root = tmp_path / "bundle-root"
    artifacts = root / "artifacts"
    bundle = root / "bundle.json"
    artifacts.mkdir(parents=True)
    _write_class_shaped_artifacts(artifacts)

    first = _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--bundle-id",
        "terminal-bench-2.0-live-001",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--entry-note",
        "live_harbor_preflight_report=captured by operator preflight",
        "--out",
        str(bundle),
    )
    first_bytes = bundle.read_bytes()
    second = _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--bundle-id",
        "terminal-bench-2.0-live-001",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--entry-note",
        "live_harbor_preflight_report=captured by operator preflight",
        "--out",
        str(bundle),
    )

    payload = json.loads(first.stdout)
    report = verify_reproduction_bundle(bundle, load_reproduction_requirements(REPO_ROOT / REQUIREMENTS))

    assert first.returncode == 0
    assert second.returncode == 0
    assert bundle.read_bytes() == first_bytes
    assert payload["reproduction_claimed"] is False
    assert {entry["source"]["provider"] for entry in payload["entries"]} == {"harbor"}
    assert any(entry.get("notes") == "captured by operator preflight" for entry in payload["entries"])
    assert all(not Path(entry["path"]).is_absolute() for entry in payload["entries"])
    assert report.ok is True


def test_reproduction_bundle_build_rejects_unsafe_inputs(tmp_path: Path) -> None:
    root = tmp_path / "bundle-root"
    artifacts = root / "artifacts"
    bundle = root / "bundle.json"
    artifacts.mkdir(parents=True)
    _write_class_shaped_artifacts(artifacts)

    duplicate = _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--artifact",
        f"live_terminal_bench_split_manifest={artifacts / 'live_terminal_bench_split_manifest.json'}",
        "--bundle-id",
        "duplicate",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--out",
        str(bundle),
    )
    assert duplicate.returncode == 2
    assert "duplicate artifact class" in duplicate.stderr

    unknown = _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--artifact",
        f"unknown_artifact_class={artifacts / 'audit_verify_report.json'}",
        "--bundle-id",
        "unknown",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--out",
        str(bundle),
    )
    assert unknown.returncode == 2
    assert "unknown class" in unknown.stderr

    invalid = artifacts / "live_terminal_bench_split_manifest.json"
    invalid.write_text(stable_json_dumps({"ok": True, "reproduction_claimed": False}) + "\n", encoding="utf-8")
    invalid_shape = _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--bundle-id",
        "invalid-shape",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--out",
        str(bundle),
    )
    assert invalid_shape.returncode == 2
    assert "invalid artifact evidence" in invalid_shape.stderr


def test_reproduction_bundle_builder_rejects_paths_outside_bundle_directory(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    bundle = tmp_path / "bundle-root" / "bundle.json"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)

    completed = _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--bundle-id",
        "path-escape",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--out",
        str(bundle),
    )

    assert completed.returncode == 2
    assert "artifact path must be inside bundle directory" in completed.stderr


def test_sign_reproduction_bundle_round_trips_with_local_key_and_external_signer(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    root = tmp_path / "bundle-root"
    artifacts = root / "artifacts"
    bundle = root / "bundle.json"
    artifacts.mkdir(parents=True)
    _write_class_shaped_artifacts(artifacts)
    _run_bundle_build(
        "--artifact-dir",
        str(artifacts),
        "--bundle-id",
        "signed-bundle",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--source-provider",
        "harbor",
        "--source-captured-at",
        "2026-06-24T00:00:00Z",
        "--out",
        str(bundle),
    )
    secret = "bundle-passphrase"
    private_key, public_key = generate_keypair(passphrase=secret)
    private_path = tmp_path / "bundle.ed25519"
    public_path = tmp_path / "bundle.ed25519.pub"
    passphrase_path = tmp_path / "passphrase.txt"
    private_path.write_bytes(private_key)
    public_path.write_bytes(public_key)
    passphrase_path.write_text(secret + "\n", encoding="utf-8")

    local_signature = _run_sign_bundle(
        "--bundle",
        str(bundle),
        "--private-key",
        str(private_path),
        "--public-key",
        str(public_path),
        "--passphrase-file",
        str(passphrase_path),
        "--provider",
        "local-fixture",
        "--key-id",
        "bundle-test",
    ).stdout.strip()
    local_verify = _run_bundle_verify(
        "--bundle",
        str(bundle),
        "--signature",
        local_signature,
        "--public-key",
        str(public_path),
        "--require-signature",
        "--out",
        str(tmp_path / "local-report.json"),
    )

    external_signature = _run_sign_bundle(
        "--bundle",
        str(bundle),
        "--external-signer",
        f"{sys.executable} {REPO_ROOT / FIXTURE_SIGNER}",
        "--provider",
        "fixture",
        "--out",
        str(tmp_path / "external.sig"),
    ).stdout.strip()
    external_verify = _run_bundle_verify(
        "--bundle",
        str(bundle),
        "--signature",
        external_signature,
        "--require-signature",
        "--out",
        str(tmp_path / "external-report.json"),
    )

    sidecar_text = Path(local_signature).read_text(encoding="utf-8")
    external_sidecar = json.loads(Path(external_signature).read_text(encoding="utf-8"))

    assert local_verify.returncode == 0
    assert external_verify.returncode == 0
    assert "PRIVATE KEY" not in sidecar_text
    assert secret not in sidecar_text
    assert external_sidecar["provider"] == "fixture"
    assert external_sidecar["key_id"] == "fixture-key-1"


def test_reproduction_readiness_can_use_bundle_as_artifact_index(tmp_path: Path) -> None:
    readiness = _provisioned_readiness_matrix(tmp_path)
    artifacts = tmp_path / "artifacts"
    out = tmp_path / "reproduction-readiness.json"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)

    completed = _run_report(
        "--readiness-matrix-result",
        str(readiness),
        "--reproduction-bundle",
        str(bundle),
        "--out",
        str(out),
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["reproduction_ready"] is True
    assert payload["metadata"]["reproduction_bundle"]["ok"] is True


def test_reproduction_readiness_rejects_bundle_with_ad_hoc_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    out = tmp_path / "reproduction-readiness.json"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)

    completed = _run_report(
        "--reproduction-bundle",
        str(bundle),
        "--artifact-dir",
        str(artifacts),
        "--readiness-matrix-result",
        str(tmp_path / "missing-readiness-matrix.json"),
        "--out",
        str(out),
    )

    assert completed.returncode == 3
    assert "cannot be combined" in completed.stderr


def test_reproduction_readiness_report_hash_matches_committed_fixture() -> None:
    requirements = load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)
    readiness = load_readiness_matrix_report(REPO_ROOT / FIXTURES / "readiness_matrix_result.json")
    report = evaluate_reproduction_readiness(
        requirements,
        readiness,
        {"audit_verify_report": [FIXTURES / "audit_verify_result.json"]},
    )
    committed = json.loads((REPO_ROOT / FIXTURES / "reproduction_readiness_result.json").read_text(encoding="utf-8"))

    assert reproduction_readiness_report_to_jsonable(report) == committed


def _run_report(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_shape_lint(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SHAPE_LINT_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_bundle_verify(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUNDLE_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_bundle_build(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILD_BUNDLE_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_sign_bundle(*extra_args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    if env is not None:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SIGN_BUNDLE_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        env=full_env,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_class_shaped_artifacts(artifact_dir: Path) -> None:
    for artifact_class, payload in _class_shaped_payloads().items():
        (artifact_dir / f"{artifact_class}.json").write_text(
            stable_json_dumps(payload) + "\n",
            encoding="utf-8",
        )


def _write_reproduction_bundle(
    tmp_path: Path,
    artifact_dir: Path,
    *,
    name: str = "bundle",
    exclude: set[str] | None = None,
) -> Path:
    bundle = tmp_path / f"{name}.json"
    entries: list[dict[str, object]] = []
    excluded = exclude or set()
    for artifact_class in sorted(set(_class_shaped_payloads()) - excluded):
        path = artifact_dir / f"{artifact_class}.json"
        data = path.read_bytes()
        entries.append(
            {
                "required_artifact_class": artifact_class,
                "path": str(path.relative_to(bundle.parent)),
                "sha256": sha256(data).hexdigest(),
                "byte_size": len(data),
                "source": {
                    "provider": "fixture",
                    "captured_at": "2026-06-24T00:00:00Z",
                    "operator_label": "self-harness-tests",
                },
                "notes": "test fixture",
            }
        )
    payload = {
        "schema_version": "1.0",
        "bundle_id": name,
        "created_at": "2026-06-24T00:00:00Z",
        "operator_label": "self-harness-tests",
        "entries": entries,
        "reproduction_claimed": False,
    }
    bundle.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    return bundle


def _split_task_ids() -> list[str]:
    payloads = _class_shaped_payloads()
    split = payloads["live_terminal_bench_split_manifest"]
    return [*split["held_in_task_ids"], *split["held_out_task_ids"]]


def _rewrite_two_repeat_artifact(artifact_dir: Path, task_ids: list[str]) -> None:
    attempts = [{"task_id": task_id, "attempts": [{"pass": True}, {"pass": True}]} for task_id in task_ids]
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "attempts_per_task": 2,
        "per_task_attempts": attempts,
        "task_count": len(task_ids),
        "attempt_count": len(task_ids) * 2,
        "pass_count": len(task_ids) * 2,
        "fail_count": 0,
        "fixed_protocol_sha256": _artifact_file_sha256(artifact_dir / "fixed_protocol_config.json"),
        "capture_run_id": "fixture-capture-run-p72",
        "reproduction_claimed": False,
    }
    (artifact_dir / "live_two_repeat_evaluation_report.json").write_text(
        stable_json_dumps(payload) + "\n",
        encoding="utf-8",
    )


def _rewrite_live_harbor_audit(
    artifact_dir: Path,
    task_ids: list[str],
    *,
    attempts_per_task: int = 2,
) -> None:
    attempts = [
        {"attempt_index": attempt_index, "pass": True, "terminal_cause": None}
        for attempt_index in range(attempts_per_task)
    ]
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "trial_artifacts": [
            {
                "task_id": task_id,
                "captured": True,
                "verifier_outcome": "pass",
                "attempts": attempts,
            }
            for task_id in task_ids
        ],
        "fixed_protocol_sha256": _artifact_file_sha256(artifact_dir / "fixed_protocol_config.json"),
        "capture_run_id": "fixture-capture-run-p72",
        "reproduction_claimed": False,
    }
    (artifact_dir / "live_harbor_audit.json").write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_evaluation_attempts(artifact_dir: Path, task_id: str, pass_values: list[bool]) -> None:
    path = artifact_dir / "live_two_repeat_evaluation_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["per_task_attempts"]
    for row in rows:
        if row["task_id"] == task_id:
            row["attempts"] = [{"pass": value} for value in pass_values]
            break
    else:
        raise AssertionError(f"missing evaluation task fixture row: {task_id}")
    all_passes = [
        attempt["pass"]
        for row in rows
        for attempt in row["attempts"]
    ]
    payload["attempt_count"] = len(all_passes)
    payload["pass_count"] = sum(1 for value in all_passes if value is True)
    payload["fail_count"] = payload["attempt_count"] - payload["pass_count"]
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_audit_attempts(artifact_dir: Path, task_id: str, pass_values: list[bool]) -> None:
    path = artifact_dir / "live_harbor_audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["trial_artifacts"]
    for row in rows:
        if row["task_id"] == task_id:
            row["attempts"] = [
                {"attempt_index": attempt_index, "pass": value, "terminal_cause": None}
                for attempt_index, value in enumerate(pass_values)
            ]
            row["verifier_outcome"] = "pass" if all(pass_values) else "fail"
            break
    else:
        raise AssertionError(f"missing audit task fixture row: {task_id}")
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_audit_image_digest(artifact_dir: Path, image_digest: str) -> None:
    path = artifact_dir / "live_harbor_audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload["trial_artifacts"]:
        row["image_digest"] = image_digest
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_container_image_trust_digest(artifact_dir: Path, image_digest: str) -> None:
    path = artifact_dir / "container_image_trust_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["images"] = [
        {
            **image,
            "digest": image_digest,
        }
        for image in payload["images"]
    ]
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_container_image_trust_children(
    artifact_dir: Path,
    child_digests: list[str],
    *,
    add_image_without_children: bool = False,
) -> None:
    path = artifact_dir / "container_image_trust_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["images"][0]["child_digests"] = child_digests
    if add_image_without_children:
        payload["images"].append(
            {
                "name": "registry.example/terminal-bench/sidecar",
                "digest": "sha256:" + "b" * 64,
            }
        )
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_backends(artifact_dir: Path, backends: list[str]) -> None:
    path = artifact_dir / "proposer_llm_request_log.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = {
        "minimax": "MiniMax-M2.5",
        "qwen": "Qwen3.5-35B-A3B",
        "glm": "GLM-5.2",
    }
    for row, backend in zip(payload["rounds"], backends, strict=True):
        row["backend"] = backend
        row["model"] = names[backend]
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_fixed_protocol_rounds(
    artifact_dir: Path,
    *,
    self_harness_rounds: int | None = None,
    proposal_width: int | None = None,
) -> None:
    path = artifact_dir / "fixed_protocol_config.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if self_harness_rounds is not None:
        payload["self_harness_rounds"] = self_harness_rounds
    if proposal_width is not None:
        payload["proposal_width"] = proposal_width
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    fixed_protocol_sha256 = _artifact_file_sha256(path)
    _rewrite_fixed_protocol_hash(
        artifact_dir,
        "live_two_repeat_evaluation_report",
        fixed_protocol_sha256,
    )
    _rewrite_fixed_protocol_hash(artifact_dir, "live_harbor_audit", fixed_protocol_sha256)
    _rewrite_fixed_protocol_hash(artifact_dir, "proposal_validation_manifest", fixed_protocol_sha256)


def _rewrite_proposer_attempted_proposals(
    artifact_dir: Path,
    *,
    round_index: int,
    attempted_proposals: int,
) -> None:
    path = artifact_dir / "proposer_llm_request_log.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rounds"][round_index]["attempted_proposals"] = attempted_proposals
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_context_round_count(artifact_dir: Path, round_count: int) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["round_count"] = round_count
    payload["rounds"] = payload["rounds"][:round_count]
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_context_empty_block(artifact_dir: Path, *, round_index: int, block: str) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    field_by_block = {
        "editable_surfaces": ("surface_count", "surfaces"),
        "held_in_failure_patterns": ("pattern_count", "patterns"),
        "passing_behavior_summaries": ("summary_count", "summaries"),
        "previous_attempted_edits": ("edit_count", "edits"),
    }
    count_field, list_field = field_by_block[block]
    payload["rounds"][round_index][block][count_field] = 0
    payload["rounds"][round_index][block][list_field] = []
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _add_proposer_context_editable_surface(
    artifact_dir: Path,
    *,
    round_index: int,
    sha256_value: str,
    kind: str = "prompt",
    name: str | None = None,
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = payload["rounds"][round_index]["editable_surfaces"]
    block["surfaces"].append(
        {
            "kind": kind,
            "name": f"surface-{sha256_value[:8]}" if name is None else name,
            "sha256": sha256_value,
        }
    )
    block["surface_count"] = len(block["surfaces"])
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_context_failure_pattern(
    artifact_dir: Path,
    *,
    round_index: int,
    task_ids: list[str],
    size: int | None = None,
    failure_category: str | None = None,
    causal_status_sha256: str | None = None,
    presentation_order: int | None = None,
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    pattern = payload["rounds"][round_index]["held_in_failure_patterns"]["patterns"][0]
    pattern["task_ids"] = task_ids
    pattern["size"] = len(task_ids) if size is None else size
    if failure_category is not None:
        pattern["failure_category"] = failure_category
    if causal_status_sha256 is not None:
        pattern["causal_status_sha256"] = causal_status_sha256
    if presentation_order is not None:
        pattern["presentation_order"] = presentation_order
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _add_proposer_context_failure_pattern(
    artifact_dir: Path,
    *,
    round_index: int,
    mechanism_sha256: str,
    task_ids: list[str] | None = None,
    size: int | None = None,
    presentation_order: int | None = None,
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = payload["rounds"][round_index]["held_in_failure_patterns"]
    patterns = block["patterns"]
    pattern = dict(patterns[0])
    suffix = "extra" if len(patterns) == 1 else f"extra-{len(patterns)}"
    pattern["cluster_id"] = f"{pattern['cluster_id']}-{suffix}"
    pattern["mechanism_sha256"] = mechanism_sha256
    if task_ids is not None:
        pattern["task_ids"] = task_ids
    if size is not None:
        pattern["size"] = size
    elif task_ids is not None:
        pattern["size"] = len(task_ids)
    pattern["presentation_order"] = len(patterns) if presentation_order is None else presentation_order
    patterns.append(pattern)
    block["pattern_count"] = len(patterns)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_context_passing_summary(
    artifact_dir: Path,
    *,
    round_index: int,
    task_ids: list[str],
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload["rounds"][round_index]["passing_behavior_summaries"]["summaries"][0]
    summary["task_ids"] = task_ids
    summary["task_id_set_sha256"] = _task_id_set_hash(task_ids)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_context_passing_hash(
    artifact_dir: Path,
    *,
    round_index: int,
    task_id_set_sha256: str,
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rounds"][round_index]["passing_behavior_summaries"]["summaries"][0][
        "task_id_set_sha256"
    ] = task_id_set_sha256
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposer_previous_edit(
    artifact_dir: Path,
    *,
    round_index: int,
    proposal_round_index: int | None = None,
    targeted_mechanism_sha256: str | None = None,
    causal_status_sha256: str | None = None,
    edited_surface_sha256: str | None = None,
    audit_decision: str | None = None,
    audit_decision_reason: str | None = None,
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    edit = payload["rounds"][round_index]["previous_attempted_edits"]["edits"][0]
    if proposal_round_index is not None:
        edit["proposal_round_index"] = proposal_round_index
    if targeted_mechanism_sha256 is not None:
        edit["targeted_mechanism_sha256"] = targeted_mechanism_sha256
    if causal_status_sha256 is not None:
        edit["causal_status_sha256"] = causal_status_sha256
    if edited_surface_sha256 is not None:
        edit["edited_surface_sha256"] = edited_surface_sha256
    if audit_decision is not None:
        edit["audit_decision"] = audit_decision
    if audit_decision_reason is not None:
        edit["audit_decision_reason"] = audit_decision_reason
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _add_proposer_previous_edit(
    artifact_dir: Path,
    *,
    round_index: int,
    proposal_round_index: int | None = None,
    targeted_mechanism_sha256: str | None = None,
    causal_status_sha256: str | None = None,
    edited_surface_sha256: str | None = None,
    audit_decision: str | None = None,
    audit_decision_reason: str | None = None,
) -> None:
    path = artifact_dir / "proposer_context_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = payload["rounds"][round_index]["previous_attempted_edits"]
    edit = dict(block["edits"][0])
    if proposal_round_index is not None:
        edit["proposal_round_index"] = proposal_round_index
    if targeted_mechanism_sha256 is not None:
        edit["targeted_mechanism_sha256"] = targeted_mechanism_sha256
    if causal_status_sha256 is not None:
        edit["causal_status_sha256"] = causal_status_sha256
    if edited_surface_sha256 is not None:
        edit["edited_surface_sha256"] = edited_surface_sha256
    if audit_decision is not None:
        edit["audit_decision"] = audit_decision
    if audit_decision_reason is not None:
        edit["audit_decision_reason"] = audit_decision_reason
    block["edits"].append(edit)
    block["edit_count"] = len(block["edits"])
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposal_validation_candidate_hash(
    artifact_dir: Path,
    *,
    round_index: int,
    candidate_index: int,
    targeted_mechanism_sha256: str | None = None,
    edited_surface_sha256: str | None = None,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidate = payload["rounds"][round_index]["candidates"][candidate_index]
    if targeted_mechanism_sha256 is not None:
        candidate["targeted_mechanism_sha256"] = targeted_mechanism_sha256
    if edited_surface_sha256 is not None:
        candidate["edited_surface_sha256"] = edited_surface_sha256
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposal_validation_candidate_fields(
    artifact_dir: Path,
    *,
    round_index: int,
    candidate_index: int,
    **updates: object,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidate = payload["rounds"][round_index]["candidates"][candidate_index]
    candidate.update(updates)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposal_validation_round_fields(
    artifact_dir: Path,
    *,
    round_index: int,
    **updates: object,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rounds"][round_index].update(updates)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _remove_proposal_validation_round_traffic_fields(artifact_dir: Path) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload["rounds"]:
        row.pop("proposer_round_request_sha256", None)
        row.pop("proposer_round_response_sha256", None)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _remove_proposal_validation_harness_hash_fields(artifact_dir: Path) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload["rounds"]:
        row.pop("harness_before_sha256", None)
        row.pop("harness_after_sha256", None)
        row.pop("harness_after_merged_sha256", None)
        row.pop("merged_split_outcomes", None)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _make_proposal_validation_round_multi_commit(
    artifact_dir: Path,
    *,
    round_index: int,
) -> None:
    validation_path = artifact_dir / "proposal_validation_manifest.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    round_row = validation["rounds"][round_index]
    candidate = round_row["candidates"][1]
    candidate["audit_decision"] = "merged"
    candidate["validation_failure_category"] = None
    candidate["changed_surfaces"] = ["tool_policy"]
    candidate["edited_surface_sha256"] = "9" * 64
    candidate["summary_sha256"] = "e" * 64
    candidate["decision_reason"] = "candidate merged with compatible accepted edit"
    candidate["rejection_reason"] = None
    candidate["split_outcomes"] = dict(round_row["candidates"][0]["split_outcomes"])
    round_row["committed_proposal_ids"] = [
        str(round_row["candidates"][0]["proposal_id"]),
        str(candidate["proposal_id"]),
    ]
    round_row["merge_decision"] = "accepted"
    round_row["harness_after_merged_sha256"] = round_row["harness_after_sha256"]
    round_row["merged_split_outcomes"] = dict(round_row["candidates"][0]["split_outcomes"])
    validation_path.write_text(stable_json_dumps(validation) + "\n", encoding="utf-8")

    context_path = artifact_dir / "proposer_context_manifest.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    surfaces = context["rounds"][round_index]["editable_surfaces"]
    surfaces["surfaces"].append(
        {
            "kind": "policy",
            "name": "tool_policy",
            "sha256": "9" * 64,
        }
    )
    surfaces["surface_count"] = len(surfaces["surfaces"])
    context_path.write_text(stable_json_dumps(context) + "\n", encoding="utf-8")

    proposer_path = artifact_dir / "proposer_llm_request_log.json"
    proposer = json.loads(proposer_path.read_text(encoding="utf-8"))
    proposer["rounds"][round_index]["committed_proposals"] = 2
    proposer_path.write_text(stable_json_dumps(proposer) + "\n", encoding="utf-8")


def _rewrite_proposal_validation_split_outcome(
    artifact_dir: Path,
    *,
    round_index: int,
    target: str,
    candidate_index: int | None = None,
    held_in_passed: int | None = None,
    held_in_total: int | None = None,
    held_out_passed: int | None = None,
    held_out_total: int | None = None,
    evaluation_repeats: int | None = None,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    round_payload = payload["rounds"][round_index]
    if target == "baseline":
        split_outcomes = round_payload["baseline_split_outcomes"]
    elif target == "candidate" and candidate_index is not None:
        split_outcomes = round_payload["candidates"][candidate_index]["split_outcomes"]
    else:
        raise AssertionError(f"unknown proposal validation split outcome target: {target}")
    updates = {
        "held_in_passed": held_in_passed,
        "held_in_total": held_in_total,
        "held_out_passed": held_out_passed,
        "held_out_total": held_out_total,
        "evaluation_repeats": evaluation_repeats,
    }
    for key, value in updates.items():
        if value is not None:
            split_outcomes[key] = value
            if key != "evaluation_repeats":
                split_outcomes.pop("task_outcomes", None)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposal_validation_baseline_task_outcome(
    artifact_dir: Path,
    *,
    round_index: int,
    task_id: str,
    passed: bool,
    failure_category: str | None = None,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    outcomes = payload["rounds"][round_index]["baseline_split_outcomes"]["task_outcomes"]
    for outcome in outcomes:
        if outcome["task_id"] == task_id:
            outcome["pass"] = passed
            if failure_category is not None:
                outcome["failure_category"] = failure_category
            elif passed:
                outcome.pop("failure_category", None)
            break
    else:
        raise AssertionError(f"missing baseline task outcome: {task_id}")
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _rewrite_proposal_validation_baseline_counts(
    artifact_dir: Path,
    *,
    round_index: int,
    held_in_passed: int,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rounds"][round_index]["baseline_split_outcomes"]["held_in_passed"] = held_in_passed
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _remove_proposal_validation_baseline_task_outcomes(
    artifact_dir: Path,
    *,
    round_index: int,
) -> None:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rounds"][round_index]["baseline_split_outcomes"].pop("task_outcomes", None)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _read_proposal_validation_candidate(
    artifact_dir: Path,
    *,
    round_index: int,
    candidate_index: int,
) -> dict[str, object]:
    path = artifact_dir / "proposal_validation_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["rounds"][round_index]["candidates"][candidate_index]


def _rewrite_fixed_protocol_hash(artifact_dir: Path, artifact_class: str, value: str) -> None:
    path = artifact_dir / f"{artifact_class}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fixed_protocol_sha256"] = value
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _bundle_check(report, name: str):
    return next(check for check in report.checks if check.name == name)


def _write_bundle_signature(tmp_path: Path, bundle: Path) -> tuple[Path, Path]:
    private_key, public_key = generate_keypair()
    public_key_path = tmp_path / "bundle.ed25519.pub"
    signature_path = tmp_path / "bundle.sig"
    public_key_path.write_bytes(public_key)
    bundle_bytes = bundle.read_bytes()
    payload = {
        "schema_version": 1,
        "manifest_sha256": sha256(bundle_bytes).hexdigest(),
        "signature_algorithm": "ed25519",
        "signature_b64": sign_bytes(bundle_bytes, private_key),
        "public_key_b64": public_key_raw_b64(public_key),
        "fingerprint": public_key_fingerprint(public_key),
        "fingerprint_algorithm": "sha256-spki-der-hex",
        "provider": "local-fixture",
        "key_id": "bundle-test",
        "manifest_filename": bundle.name,
    }
    signature_path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    return signature_path, public_key_path


def _class_shaped_payloads() -> dict[str, dict[str, object]]:
    capture_run_id = "fixture-capture-run-p72"
    held_in_ids = [f"tb-held-in-{index:02d}" for index in range(32)]
    held_out_ids = [f"tb-held-out-{index:02d}" for index in range(32)]
    held_in_failing_ids: list[str] = []
    evaluated_ids = [*held_in_ids, *held_out_ids]
    evaluation_attempts = [
        _evaluation_attempt_row(task_id, passing=task_id not in held_in_failing_ids)
        for task_id in evaluated_ids
    ]
    pass_count = sum(
        1
        for row in evaluation_attempts
        for attempt in row["attempts"]
        if attempt["pass"] is True
    )
    fixed_protocol_config: dict[str, object] = {
        "schema_version": "1.0",
        "mode": "live",
        "benchmark_protocol": "terminal-bench@2.0",
        "capture_run_id": capture_run_id,
        "models": ["minimax", "qwen", "glm"],
        "evaluator": "terminal-bench-verifier",
        "tool_set": "minimal-terminal-tools",
        "decoding_budget": {"max_tokens": 8192, "max_tool_calls": 100},
        "self_harness_rounds": 3,
        "proposal_width": 2,
        "fixed_across_variants": True,
        "reproduction_claimed": False,
    }
    fixed_protocol_sha256 = _payload_sha256(fixed_protocol_config)
    return {
        "live_terminal_bench_split_manifest": {
            "schema_version": "1.0",
            "mode": "live",
            "source": "harbor",
            "capture_run_id": capture_run_id,
            "total_cases": 64,
            "held_in_count": len(held_in_ids),
            "held_out_count": len(held_out_ids),
            "held_in_task_ids": held_in_ids,
            "held_out_task_ids": held_out_ids,
            "fixed_across_variants": True,
            "harbor_version": "2.10.0",
            "reproduction_claimed": False,
        },
        "live_two_repeat_evaluation_report": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "attempts_per_task": 2,
            "per_task_attempts": evaluation_attempts,
            "task_count": len(evaluated_ids),
            "attempt_count": len(evaluated_ids) * 2,
            "pass_count": pass_count,
            "fail_count": (len(evaluated_ids) * 2) - pass_count,
            "fixed_protocol_sha256": fixed_protocol_sha256,
            "capture_run_id": capture_run_id,
            "reproduction_claimed": False,
        },
        "fixed_protocol_config": fixed_protocol_config,
        "live_harbor_preflight_report": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "harbor_reachable": True,
            "harbor_version": "2.10.0",
            "reproduction_claimed": False,
        },
        "container_image_trust_report": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "policy": "digest-bound",
            "all_digest_bound": True,
            "images": [
                {
                    "name": "registry.example/terminal-bench/agent",
                    "digest": "sha256:" + "c" * 64,
                }
            ],
            "reproduction_claimed": False,
        },
        "model_backend_preflight_report": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "backends": ["minimax", "qwen", "glm"],
            "checks": [
                {"name": "minimax_backend_reachable", "backend": "minimax", "status": "pass", "required": True},
                {"name": "qwen_backend_reachable", "backend": "qwen", "status": "pass", "required": True},
                {"name": "glm_backend_reachable", "backend": "glm", "status": "pass", "required": True},
            ],
            "report_hash": "d" * 64,
            "reproduction_claimed": False,
        },
        "proposer_llm_request_log": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "round_count": 3,
            "rounds": [
                {
                    "round_index": 0,
                    "backend": "minimax",
                    "model": "MiniMax-M2.5",
                    "request_sha256": "1" * 64,
                    "response_sha256": "2" * 64,
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "attempted_proposals": 2,
                    "committed_proposals": 1,
                },
                {
                    "round_index": 1,
                    "backend": "qwen",
                    "model": "Qwen3.5-35B-A3B",
                    "request_sha256": "3" * 64,
                    "response_sha256": "4" * 64,
                    "prompt_tokens": 13,
                    "completion_tokens": 5,
                    "attempted_proposals": 2,
                    "committed_proposals": 1,
                },
                {
                    "round_index": 2,
                    "backend": "glm",
                    "model": "GLM-5.2",
                    "request_sha256": "5" * 64,
                    "response_sha256": "6" * 64,
                    "prompt_tokens": 17,
                    "completion_tokens": 9,
                    "attempted_proposals": 2,
                    "committed_proposals": 1,
                },
            ],
            "reproduction_claimed": False,
        },
        "proposer_context_manifest": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "round_count": 3,
            "rounds": _proposer_context_rounds(
                held_in_ids=held_in_ids,
            ),
            "reproduction_claimed": False,
        },
        "proposal_validation_manifest": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "round_count": 3,
            "rounds": _proposal_validation_rounds(
                held_in_ids=held_in_ids,
                held_out_ids=held_out_ids,
            ),
            "fixed_protocol_sha256": fixed_protocol_sha256,
            "reproduction_claimed": False,
        },
        "network_resource_controls_attestation": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "capture_run_id": capture_run_id,
            "outbound_bandwidth_cap_bps": 2_000_000,
            "mirrored_resources": ["https://resources.example/terminal-bench"],
            "reproduction_claimed": False,
        },
        "live_harbor_audit": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "trial_artifacts": [
                _audit_trial_artifact(task_id, passing=task_id not in held_in_failing_ids)
                for task_id in evaluated_ids
            ],
            "fixed_protocol_sha256": fixed_protocol_sha256,
            "capture_run_id": capture_run_id,
            "reproduction_claimed": False,
        },
        "audit_verify_report": {
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "held_out_leakage": False,
            "proposer_evidence_inspected": True,
            "changed_surfaces_recorded": True,
            "evaluation_repeats_recorded": True,
            "rejected_reasons_recorded": True,
            "report_hash": "e" * 64,
            "reproduction_claimed": False,
        },
        "release_candidate_evidence": {
            "schema_version": "1.0",
            "ok": True,
            "decision": "ready",
            "evidence_sha256": "f" * 64,
            "gates": [
                {"name": "audit_integrity", "status": "pass", "metadata": {"report_hash": "e" * 64}},
                {"name": "provenance_manifest", "status": "pass", "metadata": {"artifact_count": 1}},
                {"name": "attestation", "status": "pass", "metadata": {"report_hash": "b" * 64}},
                {
                    "name": "reproduction_readiness",
                    "status": "pass",
                    "metadata": {"reproduction_ready": True, "report_hash": "a" * 64},
                },
            ],
            "reproduction_claimed": False,
        },
    }


def _proposer_context_rounds(
    *,
    held_in_ids: list[str],
) -> list[dict[str, object]]:
    rounds: list[dict[str, object]] = []
    for index in range(3):
        held_in_failing_ids = _baseline_held_in_failing_ids_for_round(index, held_in_ids)
        held_in_passing_ids = [
            task_id for task_id in held_in_ids if task_id not in set(held_in_failing_ids)
        ]
        rounds.append(
            {
                "round_index": index,
                "editable_surfaces": {
                    "surface_count": 1,
                    "surfaces": [
                        {
                            "kind": "prompt",
                            "name": "system_prompt",
                            "sha256": "7" * 64,
                        }
                    ],
                },
                "held_in_failure_patterns": {
                    "pattern_count": 1,
                    "patterns": [
                        {
                            "cluster_id": f"cluster-{index}",
                            "size": len(held_in_failing_ids),
                            "task_ids": held_in_failing_ids,
                            "mechanism_sha256": "8" * 64,
                            "failure_category": "assertion-fail",
                            "causal_status_sha256": _causal_status_hash("agent-causal"),
                            "shared_symptoms_sha256": _evidence_hash(
                                "shared_symptoms",
                                ["assertion mismatch", "same verifier failure"],
                            ),
                            "verifier_evidence_sha256": _evidence_hash(
                                "verifier_evidence",
                                ["terminal-bench verifier failed"],
                            ),
                            "presentation_order": 0,
                            "actionability_hint_sha256": _evidence_hash(
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
                            "task_ids": held_in_passing_ids,
                            "task_id_set_sha256": _task_id_set_hash(held_in_passing_ids),
                            "preserved_behavior_sha256": "a" * 64,
                        }
                    ],
                },
                "previous_attempted_edits": _proposer_context_previous_edits(index),
            }
        )
    return rounds


def _proposal_validation_rounds(
    *,
    held_in_ids: list[str],
    held_out_ids: list[str],
) -> list[dict[str, object]]:
    held_in_total = len(held_in_ids)
    held_out_total = len(held_out_ids)
    held_out_passed = held_out_total
    rounds: list[dict[str, object]] = []
    for index in range(3):
        held_in_failing_ids = _baseline_held_in_failing_ids_for_round(index, held_in_ids)
        held_in_passed = held_in_total - len(held_in_failing_ids)
        candidates = [
            _proposal_validation_candidate(
                round_index=index,
                candidate_index=0,
                audit_decision="accepted",
                held_in_ids=held_in_ids,
                held_out_ids=held_out_ids,
                held_in_failing_ids=held_in_failing_ids,
            ),
            _proposal_validation_candidate(
                round_index=index,
                candidate_index=1,
                audit_decision="invalid",
                held_in_ids=held_in_ids,
                held_out_ids=held_out_ids,
                held_in_failing_ids=held_in_failing_ids,
            ),
        ]
        committed_proposal_ids = [
            str(candidate["proposal_id"])
            for candidate in candidates
            if candidate["audit_decision"] in {"accepted", "merged"}
        ]
        rounds.append(
            {
                "round_index": index,
                "harness_before_sha256": _proposal_validation_harness_state_sha256(index),
                "harness_after_sha256": _proposal_validation_harness_state_sha256(index + 1),
                "proposer_round_request_sha256": _proposal_validation_proposer_request_sha256(index),
                "proposer_round_response_sha256": _proposal_validation_proposer_response_sha256(index),
                "baseline_split_outcomes": _proposal_validation_split_outcomes(
                    held_in_total=held_in_total,
                    held_in_passed=held_in_passed,
                    held_out_total=held_out_total,
                    held_out_passed=held_out_passed,
                    task_outcomes=_proposal_validation_task_outcomes(
                        held_in_ids=held_in_ids,
                        held_out_ids=held_out_ids,
                        held_in_failing_ids=held_in_failing_ids,
                    ),
                ),
                "candidates": candidates,
                "committed_proposal_ids": committed_proposal_ids,
                "merge_decision": "none",
            }
        )
    return rounds


def _proposal_validation_harness_state_sha256(state_index: int) -> str:
    values = ("a", "b", "c", "d")
    return values[state_index] * 64


def _baseline_held_in_failing_ids_for_round(round_index: int, held_in_ids: list[str]) -> list[str]:
    return held_in_ids[round_index:3]


def _proposal_validation_proposer_request_sha256(round_index: int) -> str:
    values = ("1", "3", "5")
    return values[round_index] * 64


def _proposal_validation_proposer_response_sha256(round_index: int) -> str:
    values = ("2", "4", "6")
    return values[round_index] * 64


def _proposal_validation_candidate(
    *,
    round_index: int,
    candidate_index: int,
    audit_decision: str,
    held_in_ids: list[str],
    held_out_ids: list[str],
    held_in_failing_ids: list[str],
) -> dict[str, object]:
    held_in_total = len(held_in_ids)
    held_out_total = len(held_out_ids)
    held_in_passed = held_in_total - len(held_in_failing_ids)
    held_out_passed = held_out_total
    accepted = audit_decision == "accepted"
    invalid_no_surface = audit_decision == "invalid"
    candidate_held_in_passed = held_in_passed
    candidate_held_out_passed = held_out_passed
    candidate_held_in_failing_ids = list(held_in_failing_ids)
    if accepted:
        if candidate_held_in_failing_ids:
            candidate_held_in_failing_ids = candidate_held_in_failing_ids[1:]
            candidate_held_in_passed = held_in_passed + 1
        elif held_out_passed < held_out_total:
            candidate_held_out_passed = held_out_passed + 1
    elif not invalid_no_surface:
        candidate_held_in_passed = max(0, held_in_passed - 1)
    changed_surfaces: list[str] = [] if invalid_no_surface else ["system_prompt"]
    edited_surface_sha256 = (
        _payload_sha256({"changed_surfaces": []})
        if invalid_no_surface
        else "7" * 64
        if accepted
        else "9" * 64
    )
    decision_reason = (
        "candidate passed validation"
        if accepted
        else "candidate did not modify an editable surface"
        if invalid_no_surface
        else "candidate regressed held-in split"
    )
    return {
        "proposal_id": f"proposal-{round_index}-{candidate_index}",
        "proposal_round_index": round_index,
        "pattern_id": f"cluster-{round_index}",
        "changed_surfaces": changed_surfaces,
        "edited_surface_sha256": edited_surface_sha256,
        "targeted_mechanism_sha256": "8" * 64,
        "summary_sha256": "c" * 64 if accepted else "d" * 64,
        "split_outcomes": _proposal_validation_split_outcomes(
            held_in_total=held_in_total,
            held_in_passed=candidate_held_in_passed,
            held_out_total=held_out_total,
            held_out_passed=candidate_held_out_passed,
            task_outcomes=_proposal_validation_task_outcomes(
                held_in_ids=held_in_ids,
                held_out_ids=held_out_ids,
                held_in_failing_ids=candidate_held_in_failing_ids,
            ),
        ),
        "audit_decision": audit_decision,
        "validation_failure_category": "no_editable_surface" if invalid_no_surface else None,
        "decision_reason": decision_reason,
        "rejection_reason": None if accepted else decision_reason,
    }


def _proposal_validation_split_outcomes(
    *,
    held_in_total: int,
    held_in_passed: int,
    held_out_total: int,
    held_out_passed: int,
    task_outcomes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "held_in_passed": held_in_passed,
        "held_in_total": held_in_total,
        "held_out_passed": held_out_passed,
        "held_out_total": held_out_total,
        "evaluation_repeats": 2,
    }
    if task_outcomes is not None:
        payload["task_outcomes"] = task_outcomes
    return payload


def _proposal_validation_task_outcomes(
    *,
    held_in_ids: list[str],
    held_out_ids: list[str],
    held_in_failing_ids: list[str],
) -> list[dict[str, object]]:
    failing = set(held_in_failing_ids)
    outcomes: list[dict[str, object]] = []
    for task_id in held_in_ids:
        passed = task_id not in failing
        outcome: dict[str, object] = {"task_id": task_id, "split": "held_in", "pass": passed}
        if not passed:
            outcome["failure_category"] = "assertion-fail"
        outcomes.append(outcome)
    outcomes.extend({"task_id": task_id, "split": "held_out", "pass": True} for task_id in held_out_ids)
    return outcomes


def _proposer_context_previous_edits(round_index: int) -> dict[str, object]:
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
                "targeted_mechanism_sha256": "8" * 64,
                "causal_status_sha256": _causal_status_hash("agent-causal"),
                "edited_surface_sha256": "7" * 64,
                "audit_decision": "accepted",
                "audit_decision_reason": "",
            }
        ],
    }


def _evaluation_attempt_row(task_id: str, *, passing: bool) -> dict[str, object]:
    return {"task_id": task_id, "attempts": [{"pass": passing}, {"pass": passing}]}


def _audit_trial_artifact(task_id: str, *, passing: bool) -> dict[str, object]:
    return {
        "task_id": task_id,
        "captured": True,
        "verifier_outcome": "pass" if passing else "fail",
        "attempts": [
            {"attempt_index": 0, "pass": passing, "terminal_cause": None},
            {"attempt_index": 1, "pass": passing, "terminal_cause": None},
        ],
    }


def _task_id_set_hash(task_ids: list[str]) -> str:
    return sha256((stable_json_dumps({"task_ids": sorted(task_ids)}) + "\n").encode("utf-8")).hexdigest()


def _causal_status_hash(causal_status: str) -> str:
    return _payload_sha256({"causal_status": causal_status})


def _evidence_hash(key: str, values: object) -> str:
    return _payload_sha256({key: values})


def _payload_sha256(payload: dict[str, object]) -> str:
    return sha256((stable_json_dumps(payload) + "\n").encode("utf-8")).hexdigest()


def _artifact_file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _provisioned_readiness_matrix(tmp_path: Path) -> Path:
    source = json.loads((REPO_ROOT / FIXTURES / "readiness_matrix_result.json").read_text(encoding="utf-8"))
    for row in source["rows"]:
        row["status"] = "provisioned"
    source["live_execution_blocked"] = False
    source["blocked_count"] = 0
    source["optional_count"] = 0
    source["provisioned_count"] = len(source["rows"])
    source["report_hash"] = "f" * 64
    path = tmp_path / "readiness-matrix.json"
    path.write_text(stable_json_dumps(source) + "\n", encoding="utf-8")
    return path
