import json
import subprocess
import sys
from pathlib import Path

from test_capture_manifest_build import _base_build_args, _run_build
from test_reproduction_readiness import FIXTURE_SIGNER, REPO_ROOT

REHEARSAL_SCRIPT = Path("scripts") / "capture_rehearsal.py"
SIGN_MANIFEST_SCRIPT = Path("scripts") / "sign_capture_manifest.py"
BUNDLE_VERIFY_SCRIPT = Path("scripts") / "reproduction_bundle_verify.py"
DIFF_SCRIPT = Path("scripts") / "capture_manifest_diff.py"
READINESS_MATRIX = Path("tests") / "fixtures" / "release_candidate" / "readiness_matrix_result.json"


def test_capture_rehearsal_replays_signed_pipeline_and_is_deterministic(tmp_path: Path) -> None:
    manifest = tmp_path / "capture-manifest.json"
    signature = tmp_path / "capture-manifest.sig"
    out_dir = tmp_path / "rehearsal"
    report_out = tmp_path / "rehearsal-report.json"
    _run_build(
        *_base_build_args(
            manifest,
            signing_provider="fixture",
            signing_key_id="fixture-key-1",
        )
    )
    _sign_manifest(manifest, signature)

    first = _run_rehearsal(manifest, signature, out_dir, report_out)
    first_payload = json.loads(report_out.read_text(encoding="utf-8"))
    second = _run_rehearsal(manifest, signature, out_dir, report_out)
    second_payload = json.loads(report_out.read_text(encoding="utf-8"))

    assert first.returncode == 0
    assert second.returncode == 0
    assert first_payload["ok"] is True
    assert first_payload["reproduction_claimed"] is False
    assert first_payload["reproduction_ready"] is False
    assert first_payload["report_hash"] == second_payload["report_hash"]
    assert {stage["status"] for stage in first_payload["stages"]} == {"pass"}
    assert {stage["name"] for stage in first_payload["stages"]} == {
        "manifest_verification",
        "planned_artifacts_materialized",
        "bundle_build",
        "bundle_signature",
        "bundle_verification",
        "manifest_bundle_diff",
        "reproduction_readiness_evaluation",
    }


def test_capture_rehearsal_outputs_bundle_that_standalone_verifiers_accept(tmp_path: Path) -> None:
    manifest = tmp_path / "capture-manifest.json"
    signature = tmp_path / "capture-manifest.sig"
    out_dir = tmp_path / "rehearsal"
    report_out = tmp_path / "rehearsal-report.json"
    _run_build(*_base_build_args(manifest, signing_provider="fixture", signing_key_id="fixture-key-1"))
    _sign_manifest(manifest, signature)
    completed = _run_rehearsal(manifest, signature, out_dir, report_out)
    payload = json.loads(report_out.read_text(encoding="utf-8"))

    bundle_verify = subprocess.run(
        [
            sys.executable,
            str(BUNDLE_VERIFY_SCRIPT),
            "--bundle",
            payload["bundle_path"],
            "--signature",
            payload["bundle_signature_path"],
            "--require-signature",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    diff = subprocess.run(
        [
            sys.executable,
            str(DIFF_SCRIPT),
            "--manifest",
            str(manifest),
            "--bundle",
            payload["bundle_path"],
            "--manifest-signature",
            str(signature),
            "--bundle-signature",
            payload["bundle_signature_path"],
            "--require-manifest-signature",
            "--require-bundle-signature",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert bundle_verify.returncode == 0
    assert diff.returncode == 0
    assert json.loads(bundle_verify.stdout)["ok"] is True
    assert json.loads(diff.stdout)["ok"] is True


def test_capture_rehearsal_reports_contract_gaps(tmp_path: Path) -> None:
    manifest = tmp_path / "capture-manifest.json"
    signature = tmp_path / "capture-manifest.sig"
    out_dir = tmp_path / "rehearsal"
    report_out = tmp_path / "rehearsal-report.json"
    _run_build(*_base_build_args(manifest, signing_provider="fixture", signing_key_id="fixture-key-1"))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"] = [
        entry for entry in payload["entries"] if entry["required_artifact_class"] != "live_harbor_audit"
    ]
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    _sign_manifest(manifest, signature)

    completed = _run_rehearsal(manifest, signature, out_dir, report_out)
    report = json.loads(report_out.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert report["ok"] is False
    assert report["stages"][0]["name"] == "manifest_verification"
    assert report["stages"][0]["status"] == "fail"


def test_capture_rehearsal_rejects_signature_mismatch(tmp_path: Path) -> None:
    manifest = tmp_path / "capture-manifest.json"
    signature = tmp_path / "capture-manifest.sig"
    out_dir = tmp_path / "rehearsal"
    report_out = tmp_path / "rehearsal-report.json"
    _run_build(*_base_build_args(manifest, signing_provider="fixture", signing_key_id="fixture-key-1"))
    _sign_manifest(manifest, signature)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["manifest_id"] = "tampered"
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    completed = _run_rehearsal(manifest, signature, out_dir, report_out)
    report = json.loads(report_out.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert report["ok"] is False
    assert report["stages"][0]["name"] == "manifest_verification"
    assert report["stages"][0]["status"] == "fail"


def _sign_manifest(manifest: Path, signature: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SIGN_MANIFEST_SCRIPT),
            "--manifest",
            str(manifest),
            "--external-signer",
            f"{sys.executable} {REPO_ROOT / FIXTURE_SIGNER}",
            "--provider",
            "fixture",
            "--out",
            str(signature),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _run_rehearsal(
    manifest: Path,
    signature: Path,
    out_dir: Path,
    report_out: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REHEARSAL_SCRIPT),
            "--manifest",
            str(manifest),
            "--manifest-signature",
            str(signature),
            "--require-manifest-signature",
            "--rehearsal-id",
            "terminal-bench-2.0-rehearsal-001",
            "--operator-label",
            "self-harness-tests",
            "--out-dir",
            str(out_dir),
            "--readiness-matrix-result",
            str(READINESS_MATRIX),
            "--bundle-external-signer",
            f"{sys.executable} {REPO_ROOT / FIXTURE_SIGNER}",
            "--bundle-signature-provider",
            "fixture",
            "--bundle-key-id",
            "fixture-key-1",
            "--require-bundle-signature",
            "--report-out",
            str(report_out),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
