import json
import os
import subprocess
import sys
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from self_harness.corpus_signing import generate_keypair
from self_harness.operator_policy_binding import (
    policy_binding_report_to_jsonable,
    verify_policy_binding,
)
from self_harness.operator_promotion import sign_promotion_manifest
from self_harness.types import stable_json_dumps

FIXTURE_BUNDLE = Path("tests/fixtures/operator_bundle/valid.json")
FIXTURE_PROMOTION = Path("tests/fixtures/operator_promotion/valid.json")
BINDING_SCRIPT = Path("scripts/operator_policy_binding_verify.py")


def test_operator_policy_binding_accepts_aligned_fixtures() -> None:
    report = verify_policy_binding(FIXTURE_BUNDLE, FIXTURE_PROMOTION, today=date(2026, 6, 24))
    payload = policy_binding_report_to_jsonable(report)

    assert report.ok is True
    assert payload["ok"] is True
    assert payload["reproduction_claimed"] is False
    assert "not benchmark reproduction evidence" in report.boundary
    assert {check.status for check in report.checks} == {"pass"}
    assert _check(report, "binding_image_policy").status == "pass"
    assert _check(report, "binding_trusted_public_key_0").status == "pass"


def test_operator_policy_binding_fails_for_missing_active_policy(tmp_path: Path) -> None:
    bundle, promotion = _copy_fixture_pair(tmp_path)
    data = _read_json(promotion)
    data["entries"] = [entry for entry in data["entries"] if entry["kind"] != "image_policy"]
    _write_json(promotion, data)

    report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))

    assert report.ok is False
    assert _check(report, "binding_image_policy").status == "fail"
    assert "missing from active promotion entries" in _check(report, "binding_image_policy").detail


def test_operator_policy_binding_fails_for_bundle_path_divergence(tmp_path: Path) -> None:
    bundle, promotion = _copy_fixture_pair(tmp_path)
    divergent_policy = tmp_path / "operator_bundle" / "image_policy_divergent.json"
    divergent_policy.write_text('{"policy_version":"1","entries":[]}\n', encoding="utf-8")
    bundle_data = _read_json(bundle)
    bundle_data["image_policy"] = "image_policy_divergent.json"
    _write_json(bundle, bundle_data)

    report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))

    assert report.ok is False
    assert _check(report, "binding_image_policy").status == "fail"
    assert _check(report, "active_promotion_entries_bound").status == "fail"


def test_operator_policy_binding_fails_for_stale_promotion_digest(tmp_path: Path) -> None:
    bundle, promotion = _copy_fixture_pair(tmp_path)
    data = _read_json(promotion)
    for entry in data["entries"]:
        if entry["kind"] == "vulnerability_policy":
            entry["sha256"] = "0" * 64
            break
    _write_json(promotion, data)

    report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))

    assert report.ok is False
    assert _check(report, "promotion_manifest").status == "fail"


def test_operator_policy_binding_fails_for_extra_active_entry(tmp_path: Path) -> None:
    bundle, promotion = _copy_fixture_pair(tmp_path)
    extra = tmp_path / "extra_policy.json"
    extra.write_text('{"policy_version":"1","entries":[]}\n', encoding="utf-8")
    data = _read_json(promotion)
    data["entries"].append(_promotion_entry(promotion, extra, name="extra_image_policy", status="active"))
    _write_json(promotion, data)

    report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))

    assert report.ok is False
    extra_check = _check(report, "active_promotion_entries_bound")
    assert extra_check.status == "fail"
    assert extra_check.metadata is not None
    assert extra_check.metadata["extra_active_entries"]


def test_operator_policy_binding_ignores_extra_retired_entry(tmp_path: Path) -> None:
    bundle, promotion = _copy_fixture_pair(tmp_path)
    extra = tmp_path / "extra_policy.json"
    extra.write_text('{"policy_version":"1","entries":[]}\n', encoding="utf-8")
    data = _read_json(promotion)
    data["entries"].append(_promotion_entry(promotion, extra, name="retired_image_policy", status="retired"))
    _write_json(promotion, data)

    report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))

    assert report.ok is True
    assert _check(report, "active_promotion_entries_bound").status == "pass"


def test_operator_policy_binding_fails_for_malformed_bundle_or_promotion(tmp_path: Path) -> None:
    bundle, promotion = _copy_fixture_pair(tmp_path)
    bundle.write_text("{", encoding="utf-8")

    bundle_report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))
    assert bundle_report.ok is False
    assert _check(bundle_report, "operator_bundle").status == "fail"

    bundle, promotion = _copy_fixture_pair(tmp_path / "second")
    promotion.write_text("{", encoding="utf-8")
    promotion_report = verify_policy_binding(bundle, promotion, today=date(2026, 6, 24))
    assert promotion_report.ok is False
    assert _check(promotion_report, "promotion_manifest").status == "fail"


def test_operator_policy_binding_signature_round_trip_and_tamper(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    bundle, promotion = _copy_fixture_pair(tmp_path)
    private_path, public_path = _write_keypair(tmp_path)
    signature_path = tmp_path / "promotion.sig"
    sign_promotion_manifest(promotion, private_key=private_path, out_path=signature_path)

    signed_report = verify_policy_binding(
        bundle,
        promotion,
        signature_path=signature_path,
        trusted_public_key=public_path,
        today=date(2026, 6, 24),
    )
    assert signed_report.ok is True

    signature_data = _read_json(signature_path)
    signature_data["signature_b64"] = "AA=="
    _write_json(signature_path, signature_data)
    tampered_report = verify_policy_binding(
        bundle,
        promotion,
        signature_path=signature_path,
        trusted_public_key=public_path,
        today=date(2026, 6, 24),
    )
    assert tampered_report.ok is False
    assert _check(tampered_report, "promotion_manifest").status == "fail"


def test_operator_policy_binding_script_writes_result(tmp_path: Path) -> None:
    result = tmp_path / "binding.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(BINDING_SCRIPT),
            "--bundle",
            str(FIXTURE_BUNDLE),
            "--promotion",
            str(FIXTURE_PROMOTION),
            "--today",
            "2026-06-24",
            "--result-out",
            str(result),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert result.exists()
    assert json.loads(result.read_text(encoding="utf-8")) == payload


def _copy_fixture_pair(tmp_path: Path) -> tuple[Path, Path]:
    bundle_dir = tmp_path / "operator_bundle"
    vuln_dir = tmp_path / "vuln"
    promotion_dir = tmp_path / "operator_promotion"
    bundle_dir.mkdir(parents=True)
    vuln_dir.mkdir(parents=True)
    promotion_dir.mkdir(parents=True)
    for source in Path("tests/fixtures/operator_bundle").iterdir():
        if source.is_file():
            (bundle_dir / source.name).write_bytes(source.read_bytes())
    for name in ["freshness_policy.json", "scanner_db_freshness_policy.json"]:
        source = Path("tests/fixtures/vuln") / name
        (vuln_dir / source.name).write_bytes(source.read_bytes())
    promotion = promotion_dir / "valid.json"
    promotion.write_text(FIXTURE_PROMOTION.read_text(encoding="utf-8"), encoding="utf-8")
    return bundle_dir / "valid.json", promotion


def _promotion_entry(manifest_path: Path, file_path: Path, *, name: str, status: str) -> dict[str, object]:
    data = file_path.read_bytes()
    return {
        "name": name,
        "kind": "image_policy",
        "path": Path(os.path.relpath(file_path, manifest_path.parent)).as_posix(),
        "sha256": sha256(data).hexdigest(),
        "byte_size": len(data),
        "status": status,
    }


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    private_pem, public_pem = generate_keypair()
    private_path = tmp_path / "promotion.ed25519"
    public_path = tmp_path / "promotion.ed25519.pub"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    return private_path, public_path


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(stable_json_dumps(data) + "\n", encoding="utf-8")


def _check(report, name: str):
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"missing check: {name}")
