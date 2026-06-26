from pathlib import Path

from self_harness.attestations import SigstorePythonVerifier, load_attestation_trust_root


def test_sigstore_python_verifier_accepts_injected_backend_without_sigstore_dependency(tmp_path: Path) -> None:
    attestation = tmp_path / "attestation.json"
    material = tmp_path / "material.whl"
    trust_root_json = tmp_path / "trust-root.json"
    root = tmp_path / "fulcio.pem"
    rekor = tmp_path / "rekor.pub"
    attestation.write_text("{}", encoding="utf-8")
    material.write_text("wheel", encoding="utf-8")
    root.write_text("root", encoding="utf-8")
    rekor.write_text("rekor", encoding="utf-8")
    trust_root_json.write_text(
        (
            '{"allowed_subject_alternative_names":["https://example.invalid/release"],'
            '"expected_certificate_issuer":"CN=issuer",'
            '"fulcio_certificate_paths":["fulcio.pem"],'
            '"rekor_public_key_path":"rekor.pub",'
            '"schema_version":"1.0"}'
        ),
        encoding="utf-8",
    )
    trust_root = load_attestation_trust_root(trust_root_json)
    calls = []

    def verifier(attestation_path, material_path, trust_root_value):
        calls.append((attestation_path, material_path, trust_root_value.path))
        return True

    result = SigstorePythonVerifier(verifier).verify(
        attestation_path=attestation,
        material_path=material,
        trust_root=trust_root,
    )

    assert result is True
    assert calls == [(attestation, material, trust_root_json)]
