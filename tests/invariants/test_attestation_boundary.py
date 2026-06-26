from pathlib import Path

from self_harness.attestations import ATTESTATION_BOUNDARY, verify_attestation

FIXTURE_DIR = Path("tests/fixtures/attestations")


def test_structural_attestation_reports_do_not_claim_reproduction(tmp_path: Path) -> None:
    material = FIXTURE_DIR / "material.txt"
    bundle = tmp_path / "attestation.json"
    bundle.write_text(
        (
            '{"_type":"https://docs.pypi.org/attestations/publish/v1",'
            '"materials":[{"digest":{"sha256":"0000000000000000000000000000000000000000000000000000000000000000"}}],'
            '"claim":{},'
            '"bundle":{"certificate_chain_pem":[],"signature_b64":"AA==","tlog_entries":[]}}'
        ),
        encoding="utf-8",
    )

    report = verify_attestation(
        bundle,
        material_path=material,
        trust_root_path=FIXTURE_DIR / "trust_root.json",
        backend="structural",
    )

    assert report.reproduction_claimed is False
    assert report.cryptographic_valid is None
    assert "not benchmark reproduction evidence" in report.boundary
    assert "not benchmark reproduction evidence" in ATTESTATION_BOUNDARY
