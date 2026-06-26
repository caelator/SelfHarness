import json
import subprocess
import sys
from pathlib import Path

import pytest

from self_harness.cli import main
from self_harness.corpus_signing import generate_keypair
from self_harness.operator_promotion import (
    PromotionError,
    add_promotion_entry,
    init_promotion_manifest,
    load_promotion_manifest,
    promotion_signature_to_jsonable,
    promotion_verification_report_to_jsonable,
    set_promotion_status,
    sign_promotion_manifest,
    verify_promotion_manifest,
)
from self_harness.types import stable_json_dumps

FIXTURE_PROMOTION = Path("tests/fixtures/operator_promotion/valid.json")
EXAMPLE_SIGNER = Path("scripts/example_external_signer.py")
PREFLIGHT_SCRIPT = Path("scripts/operator_promotion_preflight.py")


def test_operator_promotion_manifest_adds_entries_and_verifies(tmp_path: Path) -> None:
    manifest_path = tmp_path / "promotion.json"
    policy = _write_policy(tmp_path / "image_policy.json", {"policy_version": "1", "entries": []})

    manifest = init_promotion_manifest(manifest_path)
    assert manifest.entries == ()

    manifest = add_promotion_entry(
        manifest_path,
        name="image_policy",
        kind="image_policy",
        file_path=policy,
        status="draft",
    )
    loaded = load_promotion_manifest(manifest_path)
    report = verify_promotion_manifest(manifest_path)

    assert loaded.entries == manifest.entries
    assert loaded.entries[0].path == "image_policy.json"
    assert loaded.entries[0].byte_size == len(policy.read_bytes())
    assert report.ok is True
    assert report.report_hash
    assert {check.status for check in report.checks} == {"pass"}


def test_operator_promotion_rejects_duplicate_unknown_and_missing_entries(tmp_path: Path) -> None:
    manifest_path = tmp_path / "promotion.json"
    policy = _write_policy(tmp_path / "image_policy.json", {"policy_version": "1", "entries": []})
    init_promotion_manifest(manifest_path)
    add_promotion_entry(manifest_path, name="image_policy", kind="image_policy", file_path=policy)

    with pytest.raises(PromotionError, match="already contains entry"):
        add_promotion_entry(manifest_path, name="image_policy", kind="image_policy", file_path=policy)
    with pytest.raises(PromotionError, match="unknown promotion policy kind"):
        add_promotion_entry(manifest_path, name="unknown", kind="inline_policy", file_path=policy)
    with pytest.raises(PromotionError, match="file does not exist"):
        add_promotion_entry(manifest_path, name="missing", kind="image_policy", file_path=tmp_path / "missing.json")


def test_operator_promotion_lifecycle_is_monotonic(tmp_path: Path) -> None:
    manifest_path = tmp_path / "promotion.json"
    policy = _write_policy(tmp_path / "vulnerability_policy.json", {"policy_version": "1"})
    init_promotion_manifest(manifest_path)
    add_promotion_entry(manifest_path, name="vulnerability_policy", kind="vulnerability_policy", file_path=policy)

    set_promotion_status(manifest_path, name="vulnerability_policy", status="candidate")
    manifest = set_promotion_status(manifest_path, name="vulnerability_policy", status="active")
    assert manifest.entries[0].status == "active"

    with pytest.raises(PromotionError, match="monotonic"):
        set_promotion_status(manifest_path, name="vulnerability_policy", status="candidate")
    manifest = set_promotion_status(manifest_path, name="vulnerability_policy", status="retired")
    assert manifest.entries[0].status == "retired"
    with pytest.raises(PromotionError, match="retired"):
        set_promotion_status(manifest_path, name="vulnerability_policy", status="active")


def test_operator_promotion_local_signature_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest_path = _single_file_manifest(tmp_path)
    private_path, public_path = _write_keypair(tmp_path)
    signature_path = tmp_path / "promotion.sig"

    signature = sign_promotion_manifest(
        manifest_path,
        private_key=private_path,
        out_path=signature_path,
        provider="test-local",
        key_id="test-key",
        expected_public_key=public_path,
    )
    report = verify_promotion_manifest(manifest_path, signature_path=signature_path, trusted_public_key=public_path)

    assert signature.mode == "local-private-key"
    assert promotion_signature_to_jsonable(signature)["provider"] == "test-local"
    assert report.ok is True
    assert promotion_verification_report_to_jsonable(report)["report_hash"] == report.report_hash


def test_operator_promotion_verification_fails_for_tampered_file_and_signature(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest_path = _single_file_manifest(tmp_path)
    private_path, public_path = _write_keypair(tmp_path)
    signature_path = tmp_path / "promotion.sig"
    sign_promotion_manifest(manifest_path, private_key=private_path, out_path=signature_path)

    (tmp_path / "image_policy.json").write_text('{"policy_version":"2"}\n', encoding="utf-8")
    tampered_file_report = verify_promotion_manifest(
        manifest_path,
        signature_path=signature_path,
        trusted_public_key=public_path,
    )
    assert tampered_file_report.ok is False
    assert _check(tampered_file_report, "manifest_entry_files").status == "fail"

    data = json.loads(signature_path.read_text(encoding="utf-8"))
    data["signature_b64"] = "AA=="
    signature_path.write_text(stable_json_dumps(data) + "\n", encoding="utf-8")
    bad_signature_report = verify_promotion_manifest(
        manifest_path,
        signature_path=signature_path,
        trusted_public_key=public_path,
    )
    assert bad_signature_report.ok is False
    assert _check(bad_signature_report, "promotion_signature").status == "fail"


def test_operator_promotion_external_signer_cli_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("cryptography")
    manifest_path = _single_file_manifest(tmp_path)
    private_path, public_path = _write_keypair(tmp_path)
    signature_path = tmp_path / "promotion.sig"
    monkeypatch.setenv("SELF_HARNESS_EXAMPLE_SIGNER_KEY", str(private_path))
    monkeypatch.setenv("SELF_HARNESS_EXAMPLE_SIGNER_PROVIDER", "promotion-test")
    monkeypatch.setenv("SELF_HARNESS_EXAMPLE_SIGNER_KEY_ID", "promotion-test-key")

    code = main(
        [
            "operator-promotion",
            "sign",
            "--manifest",
            str(manifest_path),
            "--external-signer",
            f"{sys.executable} {EXAMPLE_SIGNER}",
            "--provider",
            "promotion-test",
            "--key-id",
            "promotion-test-key",
            "--out",
            str(signature_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    verify_code = main(
        [
            "operator-promotion",
            "verify",
            "--manifest",
            str(manifest_path),
            "--signature",
            str(signature_path),
            "--trusted-public-key",
            str(public_path),
            "--json",
        ]
    )
    verify_output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["signature"]["mode"] == "external-signer"
    assert verify_code == 0
    assert verify_output["ok"] is True


def test_operator_promotion_preflight_script_accepts_fixture(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_path, public_path = _write_keypair(tmp_path)
    signature_path = tmp_path / "valid.sig"
    sign_promotion_manifest(FIXTURE_PROMOTION, private_key=private_path, out_path=signature_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(PREFLIGHT_SCRIPT),
            "--promotion",
            str(FIXTURE_PROMOTION),
            "--signature",
            str(signature_path),
            "--trusted-public-key",
            str(public_path),
            "--result-out",
            str(tmp_path / "preflight.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["reproduction_claimed"] is False
    assert "not benchmark reproduction evidence" in payload["boundary"]


def _single_file_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "promotion.json"
    policy = _write_policy(tmp_path / "image_policy.json", {"policy_version": "1", "entries": []})
    init_promotion_manifest(manifest_path)
    add_promotion_entry(
        manifest_path,
        name="image_policy",
        kind="image_policy",
        file_path=policy,
        status="active",
    )
    return manifest_path


def _write_policy(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    return path


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    private_pem, public_pem = generate_keypair()
    private_path = tmp_path / "promotion.ed25519"
    public_path = tmp_path / "promotion.ed25519.pub"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    return private_path, public_path


def _check(report, name: str):
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"missing check: {name}")
