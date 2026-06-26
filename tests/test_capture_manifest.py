import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from self_harness.capture_manifest import (
    capture_manifest_report_to_jsonable,
    verify_capture_manifest,
)
from self_harness.capture_manifest_diff import (
    capture_manifest_diff_report_to_jsonable,
    diff_capture_manifest_to_bundle,
)
from self_harness.corpus_signing import generate_keypair
from self_harness.reproduction_readiness import load_reproduction_requirements
from self_harness.types import stable_json_dumps
from test_reproduction_readiness import (
    AUDIT_IMAGE_DIGEST,
    CHILD_AUDIT_IMAGE_DIGEST,
    FIXTURE_SIGNER,
    OTHER_AUDIT_IMAGE_DIGEST,
    OTHER_CHILD_AUDIT_IMAGE_DIGEST,
    REPO_ROOT,
    REQUIREMENTS,
    _class_shaped_payloads,
    _make_proposal_validation_round_multi_commit,
    _remove_proposal_validation_harness_hash_fields,
    _remove_proposal_validation_round_traffic_fields,
    _rewrite_audit_image_digest,
    _rewrite_container_image_trust_children,
    _rewrite_container_image_trust_digest,
    _write_bundle_signature,
    _write_class_shaped_artifacts,
    _write_reproduction_bundle,
)

VERIFY_SCRIPT = Path("scripts") / "capture_manifest_verify.py"
DIFF_SCRIPT = Path("scripts") / "capture_manifest_diff.py"
SIGN_SCRIPT = Path("scripts") / "sign_capture_manifest.py"


def test_capture_manifest_verifies_valid_plan(tmp_path: Path) -> None:
    manifest = _write_capture_manifest(tmp_path)
    report = verify_capture_manifest(manifest, _requirements())
    payload = capture_manifest_report_to_jsonable(report)

    assert report.ok is True
    assert payload["reproduction_claimed"] is False
    assert any(check["name"] == "class_coverage" and check["status"] == "pass" for check in payload["checks"])
    assert all("live" not in check["detail"].lower() or check["status"] == "pass" for check in payload["checks"])


def test_capture_manifest_rejects_missing_class_and_malformed_shape(tmp_path: Path) -> None:
    missing = _write_capture_manifest(tmp_path, omit_class="live_harbor_audit", name="missing")
    malformed = _write_capture_manifest(tmp_path, name="malformed")
    malformed_payload = json.loads(malformed.read_text(encoding="utf-8"))
    for entry in malformed_payload["entries"]:
        if entry["required_artifact_class"] == "live_terminal_bench_split_manifest":
            entry["planned_artifact"]["total_cases"] = 1
    malformed.write_text(stable_json_dumps(malformed_payload) + "\n", encoding="utf-8")

    missing_report = verify_capture_manifest(missing, _requirements())
    malformed_report = verify_capture_manifest(malformed, _requirements())

    assert missing_report.ok is False
    missing_checks = capture_manifest_report_to_jsonable(missing_report)["checks"]
    coverage = next(check for check in missing_checks if check["name"] == "class_coverage")
    assert "missing required class" in coverage["detail"]
    assert malformed_report.ok is False
    assert any("invalid planned artifact shape" in check.detail for check in malformed_report.checks)


def test_capture_manifest_rejects_reproduction_claim_leakage(tmp_path: Path) -> None:
    manifest = _write_capture_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["reproduction_claimed"] = True
    manifest.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    report = verify_capture_manifest(manifest, _requirements())

    assert report.ok is False
    assert "reproduction_claimed" in report.checks[0].detail


def test_capture_manifest_signature_round_trips_local_and_external(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _write_capture_manifest(tmp_path)
    secret = "capture-passphrase"
    private_key, public_key = generate_keypair(passphrase=secret)
    private_path = tmp_path / "capture.ed25519"
    public_path = tmp_path / "capture.ed25519.pub"
    passphrase_path = tmp_path / "passphrase.txt"
    private_path.write_bytes(private_key)
    public_path.write_bytes(public_key)
    passphrase_path.write_text(secret + "\n", encoding="utf-8")

    local_signature = _run_sign_manifest(
        "--manifest",
        str(manifest),
        "--private-key",
        str(private_path),
        "--public-key",
        str(public_path),
        "--passphrase-file",
        str(passphrase_path),
        "--provider",
        "local-fixture",
        "--key-id",
        "capture-test",
    ).stdout.strip()
    local_verify = _run_verify(
        "--manifest",
        str(manifest),
        "--signature",
        local_signature,
        "--public-key",
        str(public_path),
        "--require-signature",
    )

    external_signature = _run_sign_manifest(
        "--manifest",
        str(manifest),
        "--external-signer",
        f"{sys.executable} {REPO_ROOT / FIXTURE_SIGNER}",
        "--provider",
        "fixture",
        "--out",
        str(tmp_path / "external.sig"),
    ).stdout.strip()
    external_verify = _run_verify("--manifest", str(manifest), "--signature", external_signature, "--require-signature")

    assert local_verify.returncode == 0
    assert external_verify.returncode == 0
    assert secret not in Path(local_signature).read_text(encoding="utf-8")
    assert json.loads(Path(external_signature).read_text(encoding="utf-8"))["key_id"] == "fixture-key-1"


def test_capture_manifest_diff_matches_realized_bundle(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
        require_bundle_signature=True,
    )
    payload = capture_manifest_diff_report_to_jsonable(report)

    assert report.ok is True
    assert payload["matched_count"] == len(_class_shaped_payloads())
    assert {finding["status"] for finding in payload["findings"]} == {"pass"}
    capture_run_binding = next(
        finding for finding in payload["findings"] if finding["category"] == "capture-run-id-binding"
    )
    assert capture_run_binding["metadata"]["expected"] == "fixture-capture-run-p72"
    assert capture_run_binding["metadata"]["actual"] == "fixture-capture-run-p72"
    network_binding = next(
        finding for finding in payload["findings"] if finding["category"] == "network-control-binding"
    )
    assert network_binding["metadata"]["expected"] == {
        "outbound_bandwidth_cap_bps": 2_000_000,
        "mirrored_resources": ["https://resources.example/terminal-bench"],
    }
    assert network_binding["metadata"]["actual"] == network_binding["metadata"]["expected"]
    fixed_protocol_binding = next(
        finding for finding in payload["findings"] if finding["category"] == "fixed-protocol-binding"
    )
    assert fixed_protocol_binding["metadata"]["expected"] == fixed_protocol_binding["metadata"]["actual"]
    proposer_context_binding = next(
        finding for finding in payload["findings"] if finding["category"] == "proposer-context-evidence-derivation"
    )
    assert proposer_context_binding["metadata"]["coverage_violations"] == []
    assert proposer_context_binding["metadata"]["failure_category_violations"] == []
    assert proposer_context_binding["metadata"]["planned_held_in_task_ids"][0] == "tb-held-in-00"
    assert proposer_context_binding["metadata"]["planned_failure_category_rounds"][0][
        "failure_pattern_categories"
    ] == {"cluster-0": "assertion-fail"}
    assert proposer_context_binding["metadata"]["planned_failure_category_rounds"][0][
        "failure_pattern_causal_status_sha256s"
    ] == {"cluster-0": _causal_status_hash("agent-causal")}
    assert proposer_context_binding["metadata"]["planned_failure_category_rounds"][0][
        "failure_pattern_shared_symptoms_sha256s"
    ] == {"cluster-0": _evidence_hash("shared_symptoms", ["assertion mismatch", "same verifier failure"])}
    assert proposer_context_binding["metadata"]["planned_failure_category_rounds"][0][
        "failure_pattern_verifier_evidence_sha256s"
    ] == {"cluster-0": _evidence_hash("verifier_evidence", ["terminal-bench verifier failed"])}
    assert proposer_context_binding["metadata"]["planned_failure_category_rounds"][0][
        "failure_pattern_presentation_orders"
    ] == {"cluster-0": 0}
    assert proposer_context_binding["metadata"]["planned_failure_category_rounds"][0][
        "failure_pattern_actionability_hint_sha256s"
    ] == {"cluster-0": _evidence_hash("actionability_hint", "high support, high actionability")}
    proposal_validation = next(
        finding for finding in payload["findings"] if finding["category"] == "proposal-validation-derivation"
    )
    assert proposal_validation["metadata"]["round_violations"] == []
    assert proposal_validation["metadata"]["task_outcomes_digest_version"] == 2
    assert proposal_validation["metadata"]["planned_rounds"][0]["validation_failure_category_counts"] == {
        "execution_failure": 0,
        "no_editable_surface": 1,
        "none": 1,
    }
    assert proposal_validation["metadata"]["planned_rounds"][0]["changed_surfaces_empty_count"] == 1
    assert proposal_validation["metadata"]["planned_rounds"][0]["single_surface_violation_count"] == 0
    assert proposal_validation["metadata"]["planned_rounds"][0]["harness_hash_presence_count"] == 2
    assert proposal_validation["metadata"]["planned_rounds"][0]["task_outcomes_present_count"] == 2
    planned_round = proposal_validation["metadata"]["planned_rounds"][0]
    actual_round = proposal_validation["metadata"]["actual_rounds"][0]
    assert planned_round["proposer_round_request_sha256"] == actual_round["proposer_round_request_sha256"]
    assert planned_round["proposer_round_response_sha256"] == actual_round["proposer_round_response_sha256"]
    assert planned_round["harness_hash_presence_count"] == actual_round["harness_hash_presence_count"]
    assert planned_round["baseline_task_outcomes_digest"] == actual_round["baseline_task_outcomes_digest"]
    assert planned_round["candidate_task_outcomes_digests"] == actual_round["candidate_task_outcomes_digests"]
    assert set(planned_round["candidate_task_outcomes_digests"]) == {"proposal-0-0", "proposal-0-1"}
    assert planned_round["accepted_merged_surface_sha256s"] == {"7" * 64: ["proposal-0-0"]}
    assert planned_round["accepted_merged_surface_sha256s"] == actual_round["accepted_merged_surface_sha256s"]
    assert planned_round["single_surface_violation_count"] == actual_round["single_surface_violation_count"]


def test_capture_manifest_diff_reports_proposal_validation_failure_category_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    validation = artifacts / "proposal_validation_manifest.json"
    payload = json.loads(validation.read_text(encoding="utf-8"))
    candidate = payload["rounds"][0]["candidates"][1]
    candidate["validation_failure_category"] = "execution_failure"
    candidate["changed_surfaces"] = ["system_prompt"]
    validation.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["validation_failure_category_counts"] == {
        "expected": {"execution_failure": 0, "no_editable_surface": 1, "none": 1},
        "actual": {"execution_failure": 1, "no_editable_surface": 0, "none": 1},
    }
    assert violation["changed_surfaces_empty_count"] == {"expected": 1, "actual": 0}


def test_capture_manifest_diff_reports_proposal_validation_changed_surface_name_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    validation_entry = next(
        entry
        for entry in manifest_payload["entries"]
        if entry["required_artifact_class"] == "proposal_validation_manifest"
    )
    validation_entry["planned_artifact"]["rounds"][0]["candidates"][0]["changed_surfaces"] = [
        "planned-only-surface"
    ]
    manifest.write_text(stable_json_dumps(manifest_payload) + "\n", encoding="utf-8")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["candidate_changed_surface_names"] == {
        "expected": {
            "proposal-0-0": ["planned-only-surface"],
            "proposal-0-1": [],
        },
        "actual": {
            "proposal-0-0": ["system_prompt"],
            "proposal-0-1": [],
        },
    }


def test_capture_manifest_diff_reports_proposal_validation_single_surface_violation_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    validation_entry = next(
        entry
        for entry in manifest_payload["entries"]
        if entry["required_artifact_class"] == "proposal_validation_manifest"
    )
    validation_entry["planned_artifact"]["rounds"][0]["candidates"][0]["changed_surfaces"] = [
        "system_prompt",
        "tool_manifest",
    ]
    manifest.write_text(stable_json_dumps(manifest_payload) + "\n", encoding="utf-8")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["single_surface_violation_count"] == {"expected": 1, "actual": 0}


def test_capture_manifest_diff_reports_proposal_validation_accepted_surface_hash_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    validation_entry = next(
        entry
        for entry in manifest_payload["entries"]
        if entry["required_artifact_class"] == "proposal_validation_manifest"
    )
    validation_entry["planned_artifact"]["rounds"][0]["candidates"][0]["edited_surface_sha256"] = "a" * 64
    manifest.write_text(stable_json_dumps(manifest_payload) + "\n", encoding="utf-8")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["accepted_merged_surface_sha256s"] == {
        "expected": {"a" * 64: ["proposal-0-0"]},
        "actual": {"7" * 64: ["proposal-0-0"]},
    }


def test_capture_manifest_diff_reports_proposal_validation_proposer_traffic_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    validation = artifacts / "proposal_validation_manifest.json"
    payload = json.loads(validation.read_text(encoding="utf-8"))
    payload["rounds"][0]["proposer_round_request_sha256"] = "f" * 64
    validation.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["proposer_round_request_sha256"] == {"expected": "1" * 64, "actual": "f" * 64}


def test_capture_manifest_diff_reports_proposal_validation_harness_hash_presence_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _remove_proposal_validation_harness_hash_fields(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["harness_hash_presence_count"] == {"expected": 2, "actual": 0}


def test_capture_manifest_diff_reports_proposal_validation_merged_hash_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    _remove_proposal_validation_harness_hash_fields(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    _make_manifest_proposal_validation_round_multi_commit(manifest, round_index=0)

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["harness_after_merged_sha256"] == {"expected": "b" * 64, "actual": None}


def test_capture_manifest_diff_reports_proposal_validation_merged_split_outcome_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _make_proposal_validation_round_multi_commit(artifacts, round_index=0)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    _make_manifest_proposal_validation_round_multi_commit(manifest, round_index=0)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    validation_entry = next(
        entry
        for entry in payload["entries"]
        if entry["required_artifact_class"] == "proposal_validation_manifest"
    )
    round_row = validation_entry["planned_artifact"]["rounds"][0]
    round_row["merged_split_outcomes"] = dict(round_row["baseline_split_outcomes"])
    manifest.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert "merged_split_outcomes_digest" in violation
    assert violation["merged_split_outcomes_digest"]["expected"] != violation["merged_split_outcomes_digest"]["actual"]


def test_capture_manifest_diff_reports_proposal_validation_task_outcome_presence_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    validation = artifacts / "proposal_validation_manifest.json"
    payload = json.loads(validation.read_text(encoding="utf-8"))
    for candidate in payload["rounds"][0]["candidates"]:
        candidate["split_outcomes"].pop("task_outcomes")
    validation.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert violation["task_outcomes_present_count"] == {"expected": 2, "actual": 0}
    assert "candidate_task_outcome_digest_drifts" not in violation


def test_capture_manifest_diff_reports_proposal_validation_task_outcome_content_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    validation = artifacts / "proposal_validation_manifest.json"
    payload = json.loads(validation.read_text(encoding="utf-8"))
    outcomes = payload["rounds"][0]["candidates"][1]["split_outcomes"]["task_outcomes"]
    failing = next(row for row in outcomes if row["split"] == "held_in" and row["pass"] is False)
    passing = next(row for row in outcomes if row["split"] == "held_in" and row["pass"] is True)
    failing["pass"] = True
    failing.pop("failure_category", None)
    passing["pass"] = False
    passing["failure_category"] = "assertion-fail"
    validation.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposal-validation-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    violation = finding.metadata["round_violations"][0]
    assert violation["round_index"] == 0
    assert "task_outcomes_present_count" not in violation
    drifts = violation["candidate_task_outcome_digest_drifts"]
    assert len(drifts) == 1
    assert drifts[0]["proposal_id"] == "proposal-0-1"
    assert drifts[0]["expected"] != drifts[0]["actual"]


def test_capture_manifest_diff_reports_source_and_custody_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    source_drift = _write_capture_manifest(tmp_path, bundle_id="bundle", source_provider="harbor", name="source")
    custody_drift = _write_capture_manifest(tmp_path, bundle_id="bundle", signing_key_id="wrong-key", name="custody")

    source_report = diff_capture_manifest_to_bundle(
        source_drift,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    custody_report = diff_capture_manifest_to_bundle(
        custody_drift,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )

    assert source_report.ok is False
    assert any(finding.category == "source-provider-drift" for finding in source_report.findings)
    assert custody_report.ok is False
    assert any(finding.category == "custody-drift" for finding in custody_report.findings)


def test_capture_manifest_diff_reports_capture_run_id_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle", run_id="different-planned-run")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "capture-run-id-binding")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["expected"] == "different-planned-run"
    assert finding.metadata["actual"] == "fixture-capture-run-p72"


def test_capture_manifest_diff_reports_network_control_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    attestation = artifacts / "network_resource_controls_attestation.json"
    payload = json.loads(attestation.read_text(encoding="utf-8"))
    payload["outbound_bandwidth_cap_bps"] = 1_000_000
    payload["mirrored_resources"] = ["https://mirror.example/terminal-bench"]
    attestation.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "network-control-binding")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["expected"] == {
        "outbound_bandwidth_cap_bps": 2_000_000,
        "mirrored_resources": ["https://resources.example/terminal-bench"],
    }
    assert finding.metadata["actual"] == {
        "outbound_bandwidth_cap_bps": 1_000_000,
        "mirrored_resources": ["https://mirror.example/terminal-bench"],
    }
    assert finding.metadata["missing"] == ["https://resources.example/terminal-bench"]
    assert finding.metadata["extra"] == ["https://mirror.example/terminal-bench"]


def test_capture_manifest_diff_reports_fixed_protocol_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        if entry["required_artifact_class"] == "fixed_protocol_config":
            entry["planned_artifact"]["evaluator"] = "different-terminal-bench-verifier"
            break
    else:
        raise AssertionError("fixed_protocol_config plan fixture is missing")
    manifest.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "fixed-protocol-binding")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["expected"] != finding.metadata["actual"]


def test_capture_manifest_diff_reports_proposer_context_extra_task_id(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    pattern["task_ids"] = ["tb-held-in-00", "tb-held-out-00"]
    pattern["size"] = 2
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["coverage_violations"][0]["extra_task_ids"] == ["tb-held-out-00"]


def test_capture_manifest_diff_reports_proposer_context_failure_category_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    pattern["failure_category"] = "timeout"
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["coverage_violations"] == []
    assert finding.metadata["failure_category_violations"] == [
        {
            "round_index": 0,
            "expected": {"cluster-0": "assertion-fail"},
            "actual": {"cluster-0": "timeout"},
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_causal_status_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    pattern["causal_status_sha256"] = _causal_status_hash("different-causal-status")
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["coverage_violations"] == []
    assert finding.metadata["failure_category_violations"] == []
    assert finding.metadata["causal_status_violations"] == [
        {
            "round_index": 0,
            "expected": {"cluster-0": _causal_status_hash("agent-causal")},
            "actual": {"cluster-0": _causal_status_hash("different-causal-status")},
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_shared_symptom_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    pattern["shared_symptoms_sha256"] = _evidence_hash("shared_symptoms", ["different symptom"])
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["coverage_violations"] == []
    assert finding.metadata["failure_category_violations"] == []
    assert finding.metadata["causal_status_violations"] == []
    assert finding.metadata["shared_symptoms_violations"] == [
        {
            "round_index": 0,
            "expected": {
                "cluster-0": _evidence_hash("shared_symptoms", ["assertion mismatch", "same verifier failure"])
            },
            "actual": {"cluster-0": _evidence_hash("shared_symptoms", ["different symptom"])},
        }
    ]
    assert finding.metadata["verifier_evidence_violations"] == []


def test_capture_manifest_diff_reports_proposer_context_presentation_order_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    pattern.pop("presentation_order")
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["presentation_order_violations"] == [
        {
            "round_index": 0,
            "expected": {"cluster-0": 0},
            "actual": {"cluster-0": None},
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_actionability_hint_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    pattern = payload["rounds"][0]["held_in_failure_patterns"]["patterns"][0]
    pattern["actionability_hint_sha256"] = _evidence_hash("actionability_hint", "different actionability")
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["actionability_hint_violations"] == [
        {
            "round_index": 0,
            "expected": {"cluster-0": _evidence_hash("actionability_hint", "high support, high actionability")},
            "actual": {"cluster-0": _evidence_hash("actionability_hint", "different actionability")},
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_task_overlap_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    block = payload["rounds"][0]["held_in_failure_patterns"]
    patterns = block["patterns"]
    pattern = dict(patterns[0])
    pattern["cluster_id"] = f"{pattern['cluster_id']}-extra"
    pattern["mechanism_sha256"] = "9" * 64
    pattern["task_ids"] = ["tb-held-in-00"]
    pattern["size"] = 1
    pattern["presentation_order"] = 1
    patterns.append(pattern)
    block["pattern_count"] = len(patterns)
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["task_overlap_violations"] == [
        {
            "round_index": 0,
            "expected": 0,
            "actual": 1,
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_previous_edit_duplicate_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    block = payload["rounds"][1]["previous_attempted_edits"]
    edit = dict(block["edits"][0])
    edit["audit_decision_reason"] = "duplicate summary"
    block["edits"].append(edit)
    block["edit_count"] = len(block["edits"])
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["previous_edit_duplicate_violations"] == [
        {
            "round_index": 1,
            "expected": 0,
            "actual": 1,
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_editable_surface_duplicate_drift(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    block = payload["rounds"][0]["editable_surfaces"]
    surface = dict(block["surfaces"][0])
    surface["kind"] = "tool"
    block["surfaces"].append(surface)
    block["surface_count"] = len(block["surfaces"])
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["editable_surface_duplicate_violations"] == [
        {
            "round_index": 0,
            "expected": 0,
            "actual": 1,
        }
    ]


def test_capture_manifest_diff_reports_proposer_context_missing_task_id(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    context = artifacts / "proposer_context_manifest.json"
    payload = json.loads(context.read_text(encoding="utf-8"))
    summary = payload["rounds"][0]["passing_behavior_summaries"]["summaries"][0]
    summary["task_ids"] = [task_id for task_id in summary["task_ids"] if task_id != "tb-held-in-31"]
    summary["task_id_set_sha256"] = _task_id_set_hash(summary["task_ids"])
    context.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "proposer-context-evidence-derivation")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["coverage_violations"][0]["missing_task_ids"] == ["tb-held-in-31"]


def test_capture_manifest_diff_skips_proposer_context_derivation_when_artifact_absent(tmp_path: Path) -> None:
    proposer_classes = {"proposer_llm_request_log", "proposer_context_manifest"}
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _remove_proposal_validation_round_traffic_fields(artifacts)
    for artifact_class in proposer_classes:
        (artifacts / f"{artifact_class}.json").unlink()
    bundle = _write_reproduction_bundle(tmp_path, artifacts, exclude=proposer_classes)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    _remove_manifest_entries(manifest, proposer_classes)
    _remove_manifest_proposal_validation_round_traffic_fields(manifest)
    requirements = tuple(
        requirement
        for requirement in _requirements()
        if requirement.required_artifact_class not in proposer_classes
    )

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        requirements,
        bundle_signature_path=bundle_signature,
    )

    assert report.ok is True
    assert all(finding.category != "proposer-context-evidence-derivation" for finding in report.findings)


def test_capture_manifest_diff_binds_planned_realized_audit_image_digest(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, AUDIT_IMAGE_DIGEST)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(
        tmp_path,
        bundle_id="bundle",
        audit_image_digest=AUDIT_IMAGE_DIGEST,
    )

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "audit-image-binding")

    assert report.ok is True
    assert finding.status == "pass"
    assert finding.metadata is not None
    assert finding.metadata["expected"] == [AUDIT_IMAGE_DIGEST]
    assert finding.metadata["actual"] == [AUDIT_IMAGE_DIGEST]
    assert finding.metadata["trust_image_digests"] == [AUDIT_IMAGE_DIGEST]


def test_capture_manifest_diff_binds_audit_image_child_digests(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, CHILD_AUDIT_IMAGE_DIGEST)
    _rewrite_container_image_trust_children(
        artifacts,
        [CHILD_AUDIT_IMAGE_DIGEST, OTHER_CHILD_AUDIT_IMAGE_DIGEST],
    )
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(
        tmp_path,
        bundle_id="bundle",
        audit_image_digest=CHILD_AUDIT_IMAGE_DIGEST,
    )

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "audit-image-binding")

    assert report.ok is True
    assert finding.status == "pass"
    assert finding.metadata is not None
    assert finding.metadata["trust_image_binding_mode"] == "child-digests"
    assert finding.metadata["trust_child_digests"] == [
        CHILD_AUDIT_IMAGE_DIGEST,
        OTHER_CHILD_AUDIT_IMAGE_DIGEST,
    ]
    assert finding.metadata["missing_from_trust_children"] == []


def test_capture_manifest_diff_reports_audit_image_child_digest_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, CHILD_AUDIT_IMAGE_DIGEST)
    _rewrite_container_image_trust_children(artifacts, [OTHER_CHILD_AUDIT_IMAGE_DIGEST])
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(
        tmp_path,
        bundle_id="bundle",
        audit_image_digest=CHILD_AUDIT_IMAGE_DIGEST,
    )

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "audit-image-binding")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["trust_image_binding_mode"] == "child-digests"
    assert finding.metadata["missing_from_trust_children"] == [CHILD_AUDIT_IMAGE_DIGEST]


def test_capture_manifest_diff_reports_audit_image_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    _rewrite_audit_image_digest(artifacts, OTHER_AUDIT_IMAGE_DIGEST)
    _rewrite_container_image_trust_digest(artifacts, OTHER_AUDIT_IMAGE_DIGEST)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(
        tmp_path,
        bundle_id="bundle",
        audit_image_digest=AUDIT_IMAGE_DIGEST,
    )

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        _requirements(),
        bundle_signature_path=bundle_signature,
    )
    finding = next(finding for finding in report.findings if finding.category == "audit-image-binding")

    assert report.ok is False
    assert finding.status == "fail"
    assert finding.metadata is not None
    assert finding.metadata["expected"] == [AUDIT_IMAGE_DIGEST]
    assert finding.metadata["actual"] == [OTHER_AUDIT_IMAGE_DIGEST]
    assert finding.metadata["trust_image_digests"] == [OTHER_AUDIT_IMAGE_DIGEST]


def test_capture_manifest_diff_skips_network_control_binding_when_artifact_absent(tmp_path: Path) -> None:
    artifact_class = "network_resource_controls_attestation"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_payload = json.loads(bundle.read_text(encoding="utf-8"))
    bundle_payload["entries"] = [
        entry for entry in bundle_payload["entries"] if entry["required_artifact_class"] != artifact_class
    ]
    bundle.write_text(stable_json_dumps(bundle_payload) + "\n", encoding="utf-8")
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle", omit_class=artifact_class)
    requirements = tuple(
        requirement for requirement in _requirements() if requirement.required_artifact_class != artifact_class
    )

    report = diff_capture_manifest_to_bundle(
        manifest,
        bundle,
        requirements,
        bundle_signature_path=bundle_signature,
    )

    assert report.ok is True
    assert all(finding.category != "network-control-binding" for finding in report.findings)


def test_capture_manifest_scripts_and_cli_write_reports(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_class_shaped_artifacts(artifacts)
    bundle = _write_reproduction_bundle(tmp_path, artifacts)
    bundle_signature, _public_key = _write_bundle_signature(tmp_path, bundle)
    manifest = _write_capture_manifest(tmp_path, bundle_id="bundle")
    verify_out = tmp_path / "verify.json"
    diff_out = tmp_path / "diff.json"

    verify = _run_verify("--manifest", str(manifest), "--out", str(verify_out))
    diff = _run_diff(
        "--manifest",
        str(manifest),
        "--bundle",
        str(bundle),
        "--bundle-signature",
        str(bundle_signature),
        "--out",
        str(diff_out),
    )
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "self_harness.cli",
            "capture-manifest",
            "diff",
            "--manifest",
            str(manifest),
            "--bundle",
            str(bundle),
            "--bundle-signature",
            str(bundle_signature),
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert verify.returncode == 0
    assert diff.returncode == 0
    assert cli.returncode == 0
    assert json.loads(verify_out.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(diff_out.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(cli.stdout)["ok"] is True


def _requirements():
    return load_reproduction_requirements(REPO_ROOT / REQUIREMENTS)


def _write_capture_manifest(
    tmp_path: Path,
    *,
    name: str = "capture",
    bundle_id: str = "bundle",
    omit_class: str | None = None,
    source_provider: str = "fixture",
    signing_key_id: str = "bundle-test",
    run_id: str = "fixture-capture-run-p72",
    audit_image_digest: str | None = None,
) -> Path:
    entries: list[dict[str, object]] = []
    for artifact_class, payload in sorted(_class_shaped_payloads().items()):
        if artifact_class == omit_class:
            continue
        if artifact_class == "live_harbor_audit" and audit_image_digest is not None:
            for row in payload["trial_artifacts"]:
                row["image_digest"] = audit_image_digest
        entries.append(
            {
                "required_artifact_class": artifact_class,
                "planned_source": {
                    "provider": source_provider,
                    "captured_after": "2026-06-23T00:00:00Z",
                    "captured_before": "2026-06-25T00:00:00Z",
                    "operator_label": "self-harness-tests",
                },
                "planned_artifact": payload,
                "notes": "operator capture plan fixture",
            }
        )
    manifest = tmp_path / f"{name}.json"
    manifest.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "manifest_id": name,
                "bundle_id": bundle_id,
                "operator_label": "self-harness-tests",
                "created_at": "2026-06-24T00:00:00Z",
                "planned_run": {
                    "run_id": run_id,
                    "mode": "live",
                    "benchmark_protocol": "terminal-bench@2.0",
                    "model_backends": ["minimax", "qwen", "glm"],
                    "evaluator": "terminal-bench-verifier",
                    "tool_budget": {"max_tokens": 8192, "max_tool_calls": 100},
                    "outbound_bandwidth_cap_bps": 2_000_000,
                    "mirrored_resources": ["https://resources.example/terminal-bench"],
                },
                "signing_custody": {"provider": "local-fixture", "key_id": signing_key_id},
                "entries": entries,
                "reproduction_claimed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _remove_manifest_entries(manifest: Path, artifact_classes: set[str]) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"] = [
        entry
        for entry in payload["entries"]
        if entry["required_artifact_class"] not in artifact_classes
    ]
    manifest.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _make_manifest_proposal_validation_round_multi_commit(
    manifest: Path,
    *,
    round_index: int,
) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    validation_entry = next(
        entry
        for entry in payload["entries"]
        if entry["required_artifact_class"] == "proposal_validation_manifest"
    )
    round_row = validation_entry["planned_artifact"]["rounds"][round_index]
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

    context_entry = next(
        entry
        for entry in payload["entries"]
        if entry["required_artifact_class"] == "proposer_context_manifest"
    )
    surfaces = context_entry["planned_artifact"]["rounds"][round_index]["editable_surfaces"]
    surfaces["surfaces"].append({"kind": "policy", "name": "tool_policy", "sha256": "9" * 64})
    surfaces["surface_count"] = len(surfaces["surfaces"])

    proposer_entry = next(
        entry
        for entry in payload["entries"]
        if entry["required_artifact_class"] == "proposer_llm_request_log"
    )
    proposer_entry["planned_artifact"]["rounds"][round_index]["committed_proposals"] = 2
    manifest.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _remove_manifest_proposal_validation_round_traffic_fields(manifest: Path) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        if entry["required_artifact_class"] != "proposal_validation_manifest":
            continue
        for row in entry["planned_artifact"]["rounds"]:
            row.pop("proposer_round_request_sha256", None)
            row.pop("proposer_round_response_sha256", None)
    manifest.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _task_id_set_hash(task_ids: list[str]) -> str:
    return sha256((stable_json_dumps({"task_ids": sorted(task_ids)}) + "\n").encode("utf-8")).hexdigest()


def _causal_status_hash(causal_status: str) -> str:
    return sha256((stable_json_dumps({"causal_status": causal_status}) + "\n").encode("utf-8")).hexdigest()


def _evidence_hash(key: str, values: object) -> str:
    return sha256((stable_json_dumps({key: values}) + "\n").encode("utf-8")).hexdigest()


def _run_verify(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_diff(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DIFF_SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_sign_manifest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SIGN_SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
