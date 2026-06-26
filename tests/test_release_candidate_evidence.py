import json
import subprocess
import sys
from pathlib import Path

from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "release_candidate_evidence.py"
FIXTURES = Path("tests") / "fixtures" / "release_candidate"
READINESS_HASH = Path("tests") / "fixtures" / "canonical_audit_hash.txt"


def test_release_candidate_evidence_all_pass_fixture_matches_hash() -> None:
    completed = _run_evidence("--expected-hash", str(FIXTURES / "expected_hash.txt"))
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["decision"] == "ready"
    assert report["reproduction_claimed"] is False
    assert "not benchmark reproduction evidence" in report["boundary"]
    assert {gate["status"] for gate in report["gates"]} == {"pass"}


def test_release_candidate_evidence_default_path_includes_readiness_promotion_gate() -> None:
    completed = _run_evidence()
    report = json.loads(completed.stdout)
    gate = _gate(report, "readiness_promotion")

    assert completed.returncode == 0
    assert gate["required"] is False
    assert gate["status"] == "pass"
    assert gate["metadata"]["ok"] is True
    assert report["reproduction_claimed"] is False


def test_release_candidate_evidence_default_path_includes_reproducible_build_gate() -> None:
    completed = _run_evidence()
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproducible_build")

    assert completed.returncode == 0
    assert gate["required"] is True
    assert gate["status"] == "pass"
    assert gate["metadata"]["published_wheel_sha256"] == "b" * 64
    assert gate["metadata"]["rebuilt_wheel_sha256"] == "b" * 64
    assert report["reproduction_claimed"] is False


def test_release_candidate_evidence_fails_closed_for_missing_artifact(tmp_path: Path) -> None:
    completed = _run_evidence("--scanner-result", str(tmp_path / "missing.json"))
    report = json.loads(completed.stdout)
    scanner_gate = _gate(report, "scanner_execution")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert scanner_gate["status"] == "fail"
    assert "missing artifact" in scanner_gate["detail"]


def test_release_candidate_evidence_fails_closed_for_gate_failure(tmp_path: Path) -> None:
    failed = tmp_path / "failed-scanner.json"
    failed.write_text('{"ok":false,"schema_version":"1.0"}\n', encoding="utf-8")

    completed = _run_evidence("--scanner-result", str(failed))
    report = json.loads(completed.stdout)
    scanner_gate = _gate(report, "scanner_execution")

    assert completed.returncode == 2
    assert scanner_gate["status"] == "fail"
    assert "ok field is not true" in scanner_gate["detail"]


def test_release_candidate_evidence_fails_closed_for_audit_integrity_failure(tmp_path: Path) -> None:
    failed = tmp_path / "failed-audit-verify.json"
    failed.write_text('{"ok":false,"schema_version":"1.0"}\n', encoding="utf-8")

    completed = _run_evidence("--audit-verify-result", str(failed))
    report = json.loads(completed.stdout)
    audit_gate = _gate(report, "audit_integrity")

    assert completed.returncode == 2
    assert audit_gate["status"] == "fail"
    assert "ok field is not true" in audit_gate["detail"]


def test_release_candidate_evidence_fails_closed_for_missing_operator_policy_binding(tmp_path: Path) -> None:
    completed = _run_evidence("--operator-policy-binding-result", str(tmp_path / "missing-binding.json"))
    report = json.loads(completed.stdout)
    binding_gate = _gate(report, "operator_policy_binding")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert binding_gate["status"] == "fail"
    assert "missing artifact" in binding_gate["detail"]


def test_release_candidate_evidence_fails_closed_for_operator_policy_binding_failure(tmp_path: Path) -> None:
    failed = tmp_path / "failed-binding.json"
    failed.write_text('{"ok":false,"schema_version":"1.0"}\n', encoding="utf-8")

    completed = _run_evidence("--operator-policy-binding-result", str(failed))
    report = json.loads(completed.stdout)
    binding_gate = _gate(report, "operator_policy_binding")

    assert completed.returncode == 2
    assert binding_gate["status"] == "fail"
    assert "ok field is not true" in binding_gate["detail"]


def test_release_candidate_evidence_accepts_optional_attestation_fixture() -> None:
    completed = _run_evidence("--attestation-result", str(FIXTURES / "attestation_result.json"))
    report = json.loads(completed.stdout)
    attestation_gate = _gate(report, "attestation")

    assert completed.returncode == 0
    assert attestation_gate["status"] == "pass"
    assert attestation_gate["metadata"]["cryptographic_valid"] is None


def test_release_candidate_evidence_fails_closed_for_optional_attestation_failure(tmp_path: Path) -> None:
    failed = tmp_path / "failed-attestation.json"
    failed.write_text('{"ok":false,"schema_version":"1.0"}\n', encoding="utf-8")

    completed = _run_evidence("--attestation-result", str(failed))
    report = json.loads(completed.stdout)
    attestation_gate = _gate(report, "attestation")

    assert completed.returncode == 2
    assert attestation_gate["status"] == "fail"
    assert "ok field is not true" in attestation_gate["detail"]


def test_release_candidate_evidence_accepts_required_readiness_matrix(tmp_path: Path) -> None:
    readiness_matrix = tmp_path / "readiness-matrix.json"
    readiness_matrix.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "live_execution_blocked": True,
                "blocked_count": 6,
                "optional_count": 2,
                "provisioned_count": 0,
                "report_hash": "a" * 64,
                "reproduction_claimed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--readiness-matrix-result", str(readiness_matrix))
    report = json.loads(completed.stdout)
    readiness_gate = _gate(report, "readiness_matrix")

    assert completed.returncode == 0
    assert readiness_gate["status"] == "pass"
    assert readiness_gate["metadata"]["live_execution_blocked"] is True
    assert readiness_gate["metadata"]["blocked_count"] == 6


def test_release_candidate_evidence_accepts_optional_readiness_promotion_result(tmp_path: Path) -> None:
    promotion = tmp_path / "readiness-promotion.json"
    promotion.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "admitted_transitions": [{"dependency": "Docker daemon"}],
                "rejected_transitions": [],
                "advisory_transitions": [],
                "unchanged_count": 10,
                "report_hash": "c" * 64,
                "reproduction_claimed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--readiness-promotion-result", str(promotion))
    report = json.loads(completed.stdout)
    gate = _gate(report, "readiness_promotion")

    assert completed.returncode == 0
    assert gate["required"] is False
    assert gate["status"] == "pass"
    assert gate["metadata"]["admitted_transitions_count"] == 1


def test_release_candidate_evidence_optional_readiness_promotion_failure_is_advisory(tmp_path: Path) -> None:
    promotion = tmp_path / "readiness-promotion.json"
    promotion.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": False,
                "admitted_transitions": [],
                "rejected_transitions": [{"dependency": "Docker daemon"}],
                "advisory_transitions": [],
                "unchanged_count": 10,
                "report_hash": "d" * 64,
                "reproduction_claimed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--readiness-promotion-result", str(promotion))
    report = json.loads(completed.stdout)
    gate = _gate(report, "readiness_promotion")

    assert completed.returncode == 0
    assert report["decision"] == "ready"
    assert gate["required"] is False
    assert gate["status"] == "advisory"


def test_release_candidate_evidence_requires_readiness_matrix_argument() -> None:
    args = _evidence_args({})
    index = args.index("--readiness-matrix-result")
    del args[index : index + 2]

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "readiness-matrix-result" in completed.stderr


def test_release_candidate_evidence_fails_closed_for_missing_readiness_matrix(tmp_path: Path) -> None:
    completed = _run_evidence("--readiness-matrix-result", str(tmp_path / "missing-readiness-matrix.json"))
    report = json.loads(completed.stdout)
    readiness_gate = _gate(report, "readiness_matrix")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert readiness_gate["status"] == "fail"
    assert "missing artifact" in readiness_gate["detail"]


def test_release_candidate_evidence_fails_closed_for_malformed_readiness_matrix(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed-readiness-matrix.json"
    malformed.write_text("{", encoding="utf-8")

    completed = _run_evidence("--readiness-matrix-result", str(malformed))
    report = json.loads(completed.stdout)
    readiness_gate = _gate(report, "readiness_matrix")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert readiness_gate["status"] == "fail"
    assert "invalid JSON" in readiness_gate["detail"]


def test_release_candidate_evidence_requires_readiness_drift_argument() -> None:
    args = _evidence_args({})
    index = args.index("--readiness-drift-result")
    del args[index : index + 2]

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "readiness-drift-result" in completed.stderr


def test_release_candidate_evidence_requires_reproducible_build_argument() -> None:
    args = _evidence_args({})
    index = args.index("--reproducible-build-result")
    del args[index : index + 2]

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "reproducible-build-result" in completed.stderr


def test_release_candidate_evidence_fails_closed_for_reproducible_build_failure(tmp_path: Path) -> None:
    failed = tmp_path / "failed-reproducible-build.json"
    failed.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "artifact_class": "reproducible_build",
                "ok": False,
                "report_hash": "e" * 64,
                "reproduction_claimed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--reproducible-build-result", str(failed))
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproducible_build")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert gate["status"] == "fail"
    assert "ok field is not true" in gate["detail"]


def test_release_candidate_evidence_fails_closed_for_missing_readiness_drift(tmp_path: Path) -> None:
    completed = _run_evidence("--readiness-drift-result", str(tmp_path / "missing-readiness-drift.json"))
    report = json.loads(completed.stdout)
    drift_gate = _gate(report, "readiness_drift")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert drift_gate["status"] == "fail"
    assert "missing artifact" in drift_gate["detail"]


def test_release_candidate_evidence_fails_closed_for_readiness_matrix_reproduction_claim(tmp_path: Path) -> None:
    readiness_matrix = tmp_path / "readiness-matrix.json"
    readiness_matrix.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "live_execution_blocked": False,
                "report_hash": "b" * 64,
                "reproduction_claimed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--readiness-matrix-result", str(readiness_matrix))
    report = json.loads(completed.stdout)
    readiness_gate = _gate(report, "readiness_matrix")

    assert completed.returncode == 2
    assert readiness_gate["status"] == "fail"
    assert "reproduction" in readiness_gate["detail"]


def test_release_candidate_evidence_rejects_malformed_json(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")

    completed = _run_evidence("--operator-preflight-result", str(malformed))
    report = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert _gate(report, "operator_preflight")["status"] == "fail"


def test_release_candidate_evidence_rejects_reproduction_claim(tmp_path: Path) -> None:
    claimed = tmp_path / "claimed.json"
    claimed.write_text(stable_json_dumps({"ok": True, "reproduction_claimed": True}) + "\n", encoding="utf-8")

    completed = _run_evidence("--harbor-discovery-result", str(claimed))
    report = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert _gate(report, "harbor_discovery")["status"] == "fail"
    assert "reproduction" in _gate(report, "harbor_discovery")["detail"]


def test_release_candidate_evidence_accepts_advisory_reproduction_readiness_fixture() -> None:
    completed = _run_evidence(
        "--reproduction-readiness-result",
        str(FIXTURES / "reproduction_readiness_result.json"),
    )
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproduction_readiness")

    assert completed.returncode == 0
    assert report["decision"] == "ready"
    assert gate["status"] == "pass"
    assert gate["metadata"]["reproduction_ready"] is False
    assert "advisory" in gate["detail"]


def test_release_candidate_evidence_hard_gate_blocks_when_reproduction_not_ready() -> None:
    args = _evidence_args({})
    args.extend(
        [
            "--reproduction-readiness-result",
            str(FIXTURES / "reproduction_readiness_result.json"),
        ]
    )
    args.append("--require-reproduction-readiness")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproduction_readiness")
    bundle_gate = _gate(report, "reproduction_bundle")

    assert completed.returncode == 2
    assert report["decision"] == "blocked"
    assert gate["status"] == "fail"
    assert gate["metadata"]["reproduction_ready"] is False
    assert bundle_gate["status"] == "fail"
    assert "required" in bundle_gate["detail"]


def test_release_candidate_evidence_accepts_reproduction_bundle_result(tmp_path: Path) -> None:
    bundle = tmp_path / "reproduction-bundle.json"
    bundle.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "bundle_id": "test-bundle",
                "bundle_sha256": "b" * 64,
                "report_hash": "a" * 64,
                "reproduction_claimed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--reproduction-bundle-result", str(bundle))
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproduction_bundle")

    assert completed.returncode == 0
    assert gate["status"] == "pass"
    assert gate["metadata"]["bundle_id"] == "test-bundle"


def test_release_candidate_evidence_rejects_malformed_reproduction_readiness(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed-reproduction-readiness.json"
    malformed.write_text("{", encoding="utf-8")

    completed = _run_evidence("--reproduction-readiness-result", str(malformed))
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproduction_readiness")

    assert completed.returncode == 2
    assert gate["status"] == "fail"
    assert "invalid JSON" in gate["detail"]


def test_release_candidate_evidence_rejects_reproduction_readiness_claim(tmp_path: Path) -> None:
    claimed = tmp_path / "claimed-reproduction-readiness.json"
    claimed.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "reproduction_ready": False,
                "report_hash": "a" * 64,
                "reproduction_claimed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_evidence("--reproduction-readiness-result", str(claimed))
    report = json.loads(completed.stdout)
    gate = _gate(report, "reproduction_readiness")

    assert completed.returncode == 2
    assert gate["status"] == "fail"
    assert "reproduction" in gate["detail"]


def _run_evidence(*extra_args: str) -> subprocess.CompletedProcess[str]:
    overrides = dict(zip(extra_args[::2], extra_args[1::2], strict=False))
    args = _evidence_args(overrides)
    if "--expected-hash" in overrides:
        args.extend(["--expected-hash", overrides["--expected-hash"]])
    if "--attestation-result" in overrides:
        args.extend(["--attestation-result", overrides["--attestation-result"]])
    if "--readiness-promotion-result" in overrides:
        args.extend(["--readiness-promotion-result", overrides["--readiness-promotion-result"]])
    if "--reproducible-build-result" in overrides:
        args.extend(["--reproducible-build-result", overrides["--reproducible-build-result"]])
    if "--reproduction-readiness-result" in overrides:
        args.extend(["--reproduction-readiness-result", overrides["--reproduction-readiness-result"]])
    if "--reproduction-bundle-result" in overrides:
        args.extend(["--reproduction-bundle-result", overrides["--reproduction-bundle-result"]])
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _evidence_args(overrides: dict[str, str]) -> list[str]:
    return [
        "--readiness-hash",
        str(READINESS_HASH),
        "--vuln-report",
        overrides.get("--vuln-report", str(FIXTURES / "vuln_report.json")),
        "--scanner-result",
        overrides.get("--scanner-result", str(FIXTURES / "scanner_result.json")),
        "--scanner-db-update-result",
        overrides.get("--scanner-db-update-result", str(FIXTURES / "scanner_db_update_result.json")),
        "--harbor-discovery-result",
        overrides.get("--harbor-discovery-result", str(FIXTURES / "harbor_discovery_result.json")),
        "--operator-preflight-result",
        overrides.get("--operator-preflight-result", str(FIXTURES / "operator_preflight_result.json")),
        "--operator-promotion-result",
        overrides.get("--operator-promotion-result", str(FIXTURES / "operator_promotion_result.json")),
        "--operator-policy-binding-result",
        overrides.get("--operator-policy-binding-result", str(FIXTURES / "operator_policy_binding_result.json")),
        "--readiness-matrix-result",
        overrides.get("--readiness-matrix-result", str(FIXTURES / "readiness_matrix_result.json")),
        "--readiness-drift-result",
        overrides.get("--readiness-drift-result", str(FIXTURES / "readiness_drift_result.json")),
        "--readiness-promotion-result",
        overrides.get("--readiness-promotion-result", str(FIXTURES / "readiness_promotion_result.json")),
        "--reproducible-build-result",
        overrides.get("--reproducible-build-result", str(FIXTURES / "reproducible_build_result.json")),
        "--audit-verify-result",
        overrides.get("--audit-verify-result", str(FIXTURES / "audit_verify_result.json")),
        "--provenance",
        overrides.get("--provenance", str(FIXTURES / "provenance.json")),
        "--provenance-signature",
        overrides.get("--provenance-signature", str(FIXTURES / "provenance.sig")),
    ]


def _gate(report: dict[str, object], name: str) -> dict[str, object]:
    for gate in report["gates"]:
        if gate["name"] == name:
            return gate
    raise AssertionError(f"missing gate: {name}")
