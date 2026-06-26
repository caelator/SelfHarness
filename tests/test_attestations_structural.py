import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from self_harness.attestations import (
    SigstorePythonVerifier,
    attestation_report_to_jsonable,
    verify_attestation,
)
from self_harness.types import stable_json_dumps

FIXTURE_DIR = Path("tests/fixtures/attestations")
BUNDLE = FIXTURE_DIR / "sigstore_bundle.json"
TRUST_ROOT = FIXTURE_DIR / "trust_root.json"
MATERIAL = FIXTURE_DIR / "material.txt"
BUILD_SCRIPT = Path("scripts/build_structural_attestation_fixture.py")
VERIFY_SCRIPT = Path("scripts/verify_attestation.py")


def test_structural_attestation_accepts_fixture(tmp_path: Path) -> None:
    attestation = _build_attestation(tmp_path, MATERIAL)
    report = verify_attestation(attestation, material_path=MATERIAL, trust_root_path=TRUST_ROOT)
    payload = attestation_report_to_jsonable(report)

    assert report.ok is True
    assert report.cryptographic_valid is None
    assert payload["reproduction_claimed"] is False
    assert "not benchmark reproduction evidence" in report.boundary
    assert _check(report, "material_bound").status == "pass"
    assert _check(report, "certificate_identity").status == "pass"
    assert _check(report, "cryptographic_verification").metadata == {"cryptographic_valid": None}


def test_structural_attestation_fails_for_tampered_material(tmp_path: Path) -> None:
    material = tmp_path / "material.txt"
    material.write_text(MATERIAL.read_text(encoding="utf-8"), encoding="utf-8")
    attestation = _build_attestation(tmp_path, material)
    material.write_text("tampered\n", encoding="utf-8")

    report = verify_attestation(attestation, material_path=material, trust_root_path=TRUST_ROOT)

    assert report.ok is False
    assert _check(report, "material_bound").status == "fail"


def test_structural_attestation_fails_for_missing_signature(tmp_path: Path) -> None:
    material = tmp_path / "material.txt"
    material.write_text("material\n", encoding="utf-8")
    attestation = _build_attestation(tmp_path, material)
    data = _read_json(attestation)
    del data["bundle"]["signature_b64"]
    _write_json(attestation, data)

    report = verify_attestation(attestation, material_path=material, trust_root_path=TRUST_ROOT)

    assert report.ok is False
    assert _check(report, "attestation_schema").status == "fail"


def test_structural_attestation_fails_for_missing_cert_chain_tlog_wrong_san_or_wrong_issuer(tmp_path: Path) -> None:
    material = tmp_path / "material.txt"
    material.write_text("material\n", encoding="utf-8")
    attestation = _build_attestation(tmp_path, material)

    missing_chain = _copy_json(attestation, tmp_path / "missing-chain.json")
    data = _read_json(missing_chain)
    data["bundle"]["certificate_chain_pem"] = []
    _write_json(missing_chain, data)
    chain_report = verify_attestation(missing_chain, material_path=material, trust_root_path=TRUST_ROOT)
    assert chain_report.ok is False
    assert _check(chain_report, "certificate_chain_present").status == "fail"

    missing_tlog = _copy_json(attestation, tmp_path / "missing-tlog.json")
    data = _read_json(missing_tlog)
    data["bundle"]["tlog_entries"] = []
    _write_json(missing_tlog, data)
    tlog_report = verify_attestation(missing_tlog, material_path=material, trust_root_path=TRUST_ROOT)
    assert tlog_report.ok is False
    assert _check(tlog_report, "tlog_entries_present").status == "fail"

    wrong_san_root = _copy_json(TRUST_ROOT, tmp_path / "wrong-san-root.json")
    data = _read_json(wrong_san_root)
    _use_fixture_trust_material(data)
    data["allowed_subject_alternative_names"] = ["https://example.invalid/wrong"]
    _write_json(wrong_san_root, data)
    san_report = verify_attestation(attestation, material_path=material, trust_root_path=wrong_san_root)
    assert san_report.ok is False
    assert _check(san_report, "certificate_identity").status == "fail"

    wrong_issuer_root = _copy_json(TRUST_ROOT, tmp_path / "wrong-issuer-root.json")
    data = _read_json(wrong_issuer_root)
    _use_fixture_trust_material(data)
    data["expected_certificate_issuer"] = "CN=Wrong"
    _write_json(wrong_issuer_root, data)
    issuer_report = verify_attestation(attestation, material_path=material, trust_root_path=wrong_issuer_root)
    assert issuer_report.ok is False
    assert _check(issuer_report, "certificate_issuer").status == "fail"


def test_structural_attestation_fails_for_malformed_json_or_missing_trust_root_file(tmp_path: Path) -> None:
    material = tmp_path / "material.txt"
    material.write_text("material\n", encoding="utf-8")
    malformed = tmp_path / "attestation.json"
    malformed.write_text("{", encoding="utf-8")

    malformed_report = verify_attestation(malformed, material_path=material, trust_root_path=TRUST_ROOT)
    assert malformed_report.ok is False
    assert _check(malformed_report, "attestation_schema").status == "fail"

    missing_root = _copy_json(TRUST_ROOT, tmp_path / "missing-root.json")
    data = _read_json(missing_root)
    data["rekor_public_key_path"] = "missing.pub"
    _write_json(missing_root, data)
    attestation = _build_attestation(tmp_path, material)
    root_report = verify_attestation(attestation, material_path=material, trust_root_path=missing_root)
    assert root_report.ok is False
    assert _check(root_report, "trust_root").status == "fail"


def test_verify_attestation_script_and_cli_write_reports(tmp_path: Path) -> None:
    attestation = _build_attestation(tmp_path, MATERIAL)
    script_out = tmp_path / "script-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--bundle",
            str(attestation),
            "--material",
            str(MATERIAL),
            "--trust-root",
            str(TRUST_ROOT),
            "--backend",
            "structural",
            "--out",
            str(script_out),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True
    assert script_out.exists()

    cli_out = tmp_path / "cli-report.json"
    cli_completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "self_harness.cli",
            "verify-attestation",
            "--bundle",
            str(attestation),
            "--material",
            str(MATERIAL),
            "--trust-root",
            str(TRUST_ROOT),
            "--out",
            str(cli_out),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert cli_completed.returncode == 0
    assert cli_completed.stdout == ""
    assert json.loads(cli_out.read_text(encoding="utf-8"))["cryptographic_valid"] is None


def test_sigstore_backend_contract_uses_injected_verifier(tmp_path: Path) -> None:
    attestation = _build_attestation(tmp_path, MATERIAL)
    calls: list[tuple[Path, Path, Path]] = []

    def verifier(attestation_path, material_path, trust_root):
        calls.append((attestation_path, material_path, trust_root.path))
        return True

    report = verify_attestation(
        attestation,
        material_path=MATERIAL,
        trust_root_path=TRUST_ROOT,
        backend="sigstore",
        verifier=SigstorePythonVerifier(verifier),
    )

    assert report.ok is True
    assert report.backend == "sigstore"
    assert report.cryptographic_valid is True
    assert calls == [(attestation, MATERIAL, TRUST_ROOT)]


def test_build_structural_attestation_fixture_binds_material_digest(tmp_path: Path) -> None:
    material = tmp_path / "material.txt"
    material.write_text("dynamic material\n", encoding="utf-8")
    attestation = _build_attestation(tmp_path, material)
    payload = _read_json(attestation)

    assert payload["materials"][0]["digest"]["sha256"] == sha256(material.read_bytes()).hexdigest()


def _build_attestation(tmp_path: Path, material: Path) -> Path:
    out = tmp_path / "attestation.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--bundle",
            str(BUNDLE),
            "--material",
            str(material),
            "--out",
            str(out),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return out


def _copy_json(source: Path, target: Path) -> Path:
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(stable_json_dumps(data) + "\n", encoding="utf-8")


def _use_fixture_trust_material(data: dict[str, object]) -> None:
    data["fulcio_certificate_paths"] = [str((FIXTURE_DIR / "fulcio_root.pem").resolve())]
    data["rekor_public_key_path"] = str((FIXTURE_DIR / "rekor.pub").resolve())


def _check(report, name: str):
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"missing check: {name}")
