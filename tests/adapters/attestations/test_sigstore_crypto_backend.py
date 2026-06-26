import builtins
import json
import sys
from pathlib import Path
from types import ModuleType

from self_harness.attestations import verify_attestation
from self_harness.types import stable_json_dumps

FIXTURE_DIR = Path("tests/fixtures/attestations")
BUNDLE = FIXTURE_DIR / "sigstore_bundle.json"
TRUST_ROOT = FIXTURE_DIR / "trust_root.json"
MATERIAL = FIXTURE_DIR / "material.txt"
BUILD_SCRIPT = Path("scripts/build_structural_attestation_fixture.py")


def test_sigstore_backend_wires_bundle_policy_and_verifier(monkeypatch, tmp_path: Path) -> None:
    calls = _install_fake_sigstore(monkeypatch)
    attestation = _build_attestation(tmp_path, MATERIAL)
    trust_root = _trust_root_with_client_config(tmp_path)

    report = verify_attestation(attestation, material_path=MATERIAL, trust_root_path=trust_root, backend="sigstore")

    assert report.ok is True
    assert report.cryptographic_valid is True
    assert report.reproduction_claimed is False
    assert _check(report, "cryptographic_verification").metadata == {"cryptographic_valid": True}
    assert calls["bundle_json"] == [json.loads(attestation.read_text(encoding="utf-8"))["bundle"]]
    assert calls["trusted_config"] == ['{"trusted": true}']
    assert calls["artifact_bytes"] == [MATERIAL.read_bytes()]
    assert calls["policy_identities"] == [["https://github.com/self-harness/self-harness/.github/workflows/release.yml@refs/tags/v0.1.0"]]


def test_sigstore_backend_records_verification_failure(monkeypatch, tmp_path: Path) -> None:
    _install_fake_sigstore(monkeypatch, fail_verification=True)
    attestation = _build_attestation(tmp_path, MATERIAL)
    trust_root = _trust_root_with_client_config(tmp_path)

    report = verify_attestation(attestation, material_path=MATERIAL, trust_root_path=trust_root, backend="sigstore")

    assert report.ok is False
    assert report.cryptographic_valid is False
    crypto = _check(report, "cryptographic_verification")
    assert crypto.status == "fail"
    assert crypto.metadata == {"cryptographic_valid": False}


def test_sigstore_backend_fails_closed_without_sigstore_extra(monkeypatch, tmp_path: Path) -> None:
    _block_sigstore_import(monkeypatch)
    attestation = _build_attestation(tmp_path, MATERIAL)
    trust_root = _trust_root_with_client_config(tmp_path)

    report = verify_attestation(attestation, material_path=MATERIAL, trust_root_path=trust_root, backend="sigstore")

    assert report.ok is False
    assert report.cryptographic_valid is False
    crypto = _check(report, "cryptographic_verification")
    assert crypto.status == "fail"
    assert "requires the optional sigstore extra" in crypto.detail


def test_sigstore_backend_requires_full_sigstore_trust_config(monkeypatch, tmp_path: Path) -> None:
    _install_fake_sigstore(monkeypatch)
    attestation = _build_attestation(tmp_path, MATERIAL)
    trust_root = _trust_root_with_client_config(tmp_path, include_client_config=False)

    report = verify_attestation(attestation, material_path=MATERIAL, trust_root_path=trust_root, backend="sigstore")

    assert report.ok is False
    assert report.cryptographic_valid is False
    assert "requires sigstore_client_trust_config_path" in _check(report, "cryptographic_verification").detail


def _install_fake_sigstore(monkeypatch, *, fail_verification: bool = False) -> dict[str, list[object]]:
    calls: dict[str, list[object]] = {
        "artifact_bytes": [],
        "bundle_json": [],
        "policy_identities": [],
        "trusted_config": [],
    }

    class FakeVerificationError(Exception):
        pass

    class FakeError(Exception):
        pass

    class FakeBundle:
        @classmethod
        def from_json(cls, raw: str):
            calls["bundle_json"].append(json.loads(raw))
            return cls()

    class FakeClientTrustConfig:
        @classmethod
        def from_json(cls, raw: str):
            calls["trusted_config"].append(raw)
            return cls()

    class FakeTrustedRoot:
        @classmethod
        def from_file(cls, _path: str):
            return cls()

    class FakeVerifier:
        @classmethod
        def _from_trust_config(cls, _trust_config):
            return cls()

        def verify_artifact(self, artifact_bytes: bytes, _bundle, policy) -> None:
            calls["artifact_bytes"].append(artifact_bytes)
            calls["policy_identities"].append([child.identity for child in policy.children])
            if fail_verification:
                raise FakeVerificationError("signature mismatch")

    class FakeIdentity:
        def __init__(self, *, identity: str) -> None:
            self.identity = identity

    class FakeAnyOf:
        def __init__(self, children) -> None:
            self.children = children

    models = ModuleType("sigstore.models")
    models.Bundle = FakeBundle
    verify = ModuleType("sigstore.verify")
    verify.Verifier = FakeVerifier
    policy = ModuleType("sigstore.verify.policy")
    policy.AnyOf = FakeAnyOf
    policy.Identity = FakeIdentity
    errors = ModuleType("sigstore.errors")
    errors.Error = FakeError
    errors.VerificationError = FakeVerificationError
    trust = ModuleType("sigstore._internal.trust")
    trust.ClientTrustConfig = FakeClientTrustConfig
    trust.TrustedRoot = FakeTrustedRoot
    rekor = ModuleType("sigstore._internal.rekor.client")
    rekor.RekorClient = lambda _url: object()

    sigstore = ModuleType("sigstore")
    sigstore.__path__ = []
    internal = ModuleType("sigstore._internal")
    internal.__path__ = []
    rekor_package = ModuleType("sigstore._internal.rekor")
    rekor_package.__path__ = []
    sigstore.models = models
    sigstore.verify = verify
    sigstore.errors = errors
    internal.trust = trust
    internal.rekor = rekor_package
    rekor_package.client = rekor

    monkeypatch.setitem(sys.modules, "sigstore", sigstore)
    monkeypatch.setitem(sys.modules, "sigstore.models", models)
    monkeypatch.setitem(sys.modules, "sigstore.verify", verify)
    monkeypatch.setitem(sys.modules, "sigstore.verify.policy", policy)
    monkeypatch.setitem(sys.modules, "sigstore.errors", errors)
    monkeypatch.setitem(sys.modules, "sigstore._internal", internal)
    monkeypatch.setitem(sys.modules, "sigstore._internal.trust", trust)
    monkeypatch.setitem(sys.modules, "sigstore._internal.rekor", rekor_package)
    monkeypatch.setitem(sys.modules, "sigstore._internal.rekor.client", rekor)
    return calls


def _block_sigstore_import(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("sigstore"):
            raise ImportError("blocked sigstore import")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)


def _trust_root_with_client_config(tmp_path: Path, *, include_client_config: bool = True) -> Path:
    data = json.loads(TRUST_ROOT.read_text(encoding="utf-8"))
    data["fulcio_certificate_paths"] = [str((FIXTURE_DIR / "fulcio_root.pem").resolve())]
    data["rekor_public_key_path"] = str((FIXTURE_DIR / "rekor.pub").resolve())
    if include_client_config:
        client_config = tmp_path / "client-trust.json"
        client_config.write_text('{"trusted": true}', encoding="utf-8")
        data["sigstore_client_trust_config_path"] = "client-trust.json"
    trust_root = tmp_path / "trust-root.json"
    trust_root.write_text(stable_json_dumps(data) + "\n", encoding="utf-8")
    return trust_root


def _build_attestation(tmp_path: Path, material: Path) -> Path:
    out = tmp_path / "attestation.json"
    import subprocess

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


def _check(report, name: str):
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"missing check: {name}")
