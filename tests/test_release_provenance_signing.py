import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from self_harness.corpus_signing import (
    generate_keypair,
    public_key_from_private_key_pem,
    public_key_raw_b64,
)
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGN_PROVENANCE = REPO_ROOT / "scripts" / "sign_provenance.py"
VERIFY_PROVENANCE_SIGNATURE = REPO_ROOT / "scripts" / "verify_provenance_signature.py"
FIXTURE_SIGNER = REPO_ROOT / "tests" / "fixtures" / "external_signer.py"


def test_local_provenance_signature_round_trips_and_excludes_private_material(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _manifest(tmp_path)
    private_pem, public_pem = generate_keypair()
    private_key = tmp_path / "provenance.ed25519"
    public_key = tmp_path / "provenance.ed25519.pub"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(public_pem)

    signature = _run_sign(tmp_path, "--manifest", str(manifest), "--private-key", str(private_key)).stdout.strip()
    _run_verify("--manifest", str(manifest), "--signature", signature, "--public-key", str(public_key))
    sidecar_text = Path(signature).read_text(encoding="utf-8")
    sidecar = json.loads(sidecar_text)

    assert sidecar["schema_version"] == 1
    assert sidecar["manifest_filename"] == manifest.name
    assert sidecar["signature_algorithm"] == "ed25519"
    assert sidecar["provider"] == "local-pem"
    assert sidecar["public_key_b64"] == public_key_raw_b64(public_pem)
    assert "PRIVATE KEY" not in sidecar_text


def test_encrypted_provenance_key_uses_passphrase_env_without_leaking_secret(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    secret = "correct-horse-battery"
    manifest = _manifest(tmp_path)
    private_pem, public_pem = generate_keypair(passphrase=secret)
    private_key = tmp_path / "provenance.ed25519"
    public_key = tmp_path / "provenance.ed25519.pub"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(public_pem)

    completed = _run_sign(
        tmp_path,
        "--manifest",
        str(manifest),
        "--private-key",
        str(private_key),
        "--passphrase-env",
        "PROVENANCE_KEY_PASSPHRASE",
        env={**os.environ, "PROVENANCE_KEY_PASSPHRASE": secret},
    )

    signature = completed.stdout.strip()
    _run_verify("--manifest", str(manifest), "--signature", signature, "--public-key", str(public_key))
    assert secret not in completed.stdout
    assert secret not in completed.stderr
    assert secret not in Path(signature).read_text(encoding="utf-8")


def test_external_provenance_signature_round_trips(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _manifest(tmp_path)

    signature = _run_sign(
        tmp_path,
        "--manifest",
        str(manifest),
        "--external-signer",
        f"{sys.executable} {FIXTURE_SIGNER}",
        "--provider",
        "fixture",
    ).stdout.strip()
    sidecar = json.loads(Path(signature).read_text(encoding="utf-8"))

    _run_verify("--manifest", str(manifest), "--signature", signature)
    assert sidecar["provider"] == "fixture"
    assert sidecar["key_id"] == "fixture-key-1"


def test_provenance_signature_rejects_manifest_tampering(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _manifest(tmp_path)
    private_pem, public_pem = generate_keypair()
    private_key = tmp_path / "provenance.ed25519"
    public_key = tmp_path / "provenance.ed25519.pub"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(public_pem)
    signature = _run_sign(tmp_path, "--manifest", str(manifest), "--private-key", str(private_key)).stdout.strip()

    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    completed = _run_verify(
        "--manifest",
        str(manifest),
        "--signature",
        signature,
        "--public-key",
        str(public_key),
        check=False,
    )

    assert completed.returncode != 0
    assert "manifest_sha256 mismatch" in completed.stderr or "manifest_sha256 mismatch" in completed.stdout


def test_provenance_signing_rejects_mismatched_local_public_key(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _manifest(tmp_path)
    private_pem, _public_pem = generate_keypair()
    _other_private_pem, other_public_pem = generate_keypair()
    private_key = tmp_path / "provenance.ed25519"
    public_key = tmp_path / "wrong.ed25519.pub"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(other_public_pem)

    completed = _run_sign(
        tmp_path,
        "--manifest",
        str(manifest),
        "--private-key",
        str(private_key),
        "--public-key",
        str(public_key),
        check=False,
    )

    assert completed.returncode != 0
    assert "signature verification failed" in completed.stderr or "signature verification failed" in completed.stdout


def test_provenance_signature_rejects_signature_tampering(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _manifest(tmp_path)
    private_pem, public_pem = generate_keypair()
    private_key = tmp_path / "provenance.ed25519"
    public_key = tmp_path / "provenance.ed25519.pub"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(public_pem)
    signature = Path(_run_sign(tmp_path, "--manifest", str(manifest), "--private-key", str(private_key)).stdout.strip())
    sidecar = json.loads(signature.read_text(encoding="utf-8"))
    sidecar["signature_b64"] = base64.b64encode(b"\0" * 64).decode("ascii")
    signature.write_text(stable_json_dumps(sidecar) + "\n", encoding="utf-8")

    completed = _run_verify(
        "--manifest",
        str(manifest),
        "--signature",
        str(signature),
        "--public-key",
        str(public_key),
        check=False,
    )

    assert completed.returncode != 0
    assert "signature verification failed" in completed.stderr or "signature verification failed" in completed.stdout


def test_provenance_signature_rejects_schema_mismatch(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = _manifest(tmp_path)
    private_pem, _public_pem = generate_keypair()
    private_key = tmp_path / "provenance.ed25519"
    private_key.write_bytes(private_pem)
    signature = Path(_run_sign(tmp_path, "--manifest", str(manifest), "--private-key", str(private_key)).stdout.strip())
    sidecar = json.loads(signature.read_text(encoding="utf-8"))
    sidecar["schema_version"] = 9
    signature.write_text(stable_json_dumps(sidecar) + "\n", encoding="utf-8")

    completed = _run_verify("--manifest", str(manifest), "--signature", str(signature), check=False)

    assert completed.returncode != 0
    assert "schema_version" in completed.stderr or "schema_version" in completed.stdout


def test_release_smoke_verifies_sibling_signature_when_present(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    from scripts.release_smoke import _verify_provenance_signature

    manifest = _manifest(tmp_path)
    private_pem, public_pem = generate_keypair()
    private_key = tmp_path / "provenance.ed25519"
    public_key = tmp_path / "provenance.ed25519.pub"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(public_pem)
    _run_sign(tmp_path, "--manifest", str(manifest), "--private-key", str(private_key))

    _verify_provenance_signature(REPO_ROOT, manifest, None, str(public_key))


def test_public_key_can_be_derived_from_private_key_for_local_sidecars() -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()

    assert public_key_raw_b64(public_key_from_private_key_pem(private_pem)) == public_key_raw_b64(public_pem)


def _manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "self-harness-0.1.0-provenance.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "package_name": "self-harness",
                "artifacts": [{"kind": "wheel", "filename": "example.whl", "sha256": "0" * 64}],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _run_sign(
    tmp_path: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(sys.executable, str(SIGN_PROVENANCE), *args, env=env, cwd=tmp_path, check=check)


def _run_verify(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(sys.executable, str(VERIFY_PROVENANCE_SIGNATURE), *args, check=check)


def _run(
    *command: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    if env is not None:
        full_env.update(env)
    return subprocess.run(command, cwd=cwd, env=full_env, text=True, capture_output=True, check=check)
