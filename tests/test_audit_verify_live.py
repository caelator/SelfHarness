import json
from hashlib import sha256
from pathlib import Path

import pytest

from self_harness._artifact_shapes import artifact_shape_error
from self_harness.adapters.terminal_bench.ingest import ingest_harbor_run
from self_harness.audit_verify_live import (
    live_audit_verification_report_to_jsonable,
    verify_live_audit_run,
)
from self_harness.cli import main
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_raw_b64,
    sign_bytes,
)
from self_harness.reproduction_bundle import (
    REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
    REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
)
from self_harness.types import stable_json_dumps

MANIFEST = Path("tests/fixtures/terminal_bench/manifest.json")


def test_live_audit_verify_emits_live_shape_for_signed_harbor_fixture(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    paths = _fixture(tmp_path)

    report = verify_live_audit_run(
        paths["audit_dir"],
        live_harbor_audit=paths["live_harbor_audit"],
        provenance=paths["provenance"],
        provenance_signature=paths["signature"],
        public_key=paths["public_key"],
        require_signature=True,
    )
    out_path = tmp_path / "audit-verify-live.json"
    out_path.write_text(stable_json_dumps(live_audit_verification_report_to_jsonable(report)) + "\n", encoding="utf-8")

    assert report.ok is True
    assert report.mode == "live"
    assert report.reproduction_claimed is False
    assert report.held_out_leakage is False
    assert report.proposer_evidence_inspected is True
    assert report.changed_surfaces_recorded is True
    assert report.evaluation_repeats_recorded is True
    assert report.rejected_reasons_recorded is True
    assert artifact_shape_error("audit_verify_report", out_path) is None
    assert _check(report, "replay_audit_verify")["status"] == "pass"
    assert _check(report, "provenance_signature")["status"] == "pass"
    assert _check(report, "provenance_capture_run_binding")["status"] == "pass"
    assert _check(report, "live_harbor_audit_task_binding")["status"] == "pass"


def test_live_audit_verify_blocks_missing_signature(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    report = verify_live_audit_run(
        paths["audit_dir"],
        live_harbor_audit=paths["live_harbor_audit"],
        provenance=paths["provenance"],
        require_signature=True,
    )

    assert report.ok is False
    assert report.mode == "live_blocked"
    assert _check(report, "provenance_signature")["status"] == "fail"


def test_live_audit_verify_blocks_tampered_provenance(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    paths = _fixture(tmp_path)
    provenance = json.loads(paths["provenance"].read_text(encoding="utf-8"))
    provenance["operator_label"] = "tampered"
    paths["provenance"].write_text(stable_json_dumps(provenance) + "\n", encoding="utf-8")

    report = verify_live_audit_run(
        paths["audit_dir"],
        live_harbor_audit=paths["live_harbor_audit"],
        provenance=paths["provenance"],
        provenance_signature=paths["signature"],
        public_key=paths["public_key"],
        require_signature=True,
    )

    assert report.ok is False
    assert report.mode == "live_blocked"
    assert _check(report, "provenance_signature")["status"] == "fail"


def test_live_audit_verify_blocks_non_live_harbor_artifact(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    live_payload = json.loads(paths["live_harbor_audit"].read_text(encoding="utf-8"))
    live_payload["mode"] = "replay"
    paths["live_harbor_audit"].write_text(stable_json_dumps(live_payload) + "\n", encoding="utf-8")

    report = verify_live_audit_run(
        paths["audit_dir"],
        live_harbor_audit=paths["live_harbor_audit"],
        provenance=paths["provenance"],
        provenance_signature=paths["signature"],
        public_key=paths["public_key"],
        require_signature=True,
    )

    assert report.ok is False
    assert report.mode == "live_blocked"
    assert _check(report, "live_harbor_audit_shape")["status"] == "fail"


def test_live_audit_verify_blocks_task_id_mismatch(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    live_payload = json.loads(paths["live_harbor_audit"].read_text(encoding="utf-8"))
    live_payload["trial_artifacts"][0]["task_id"] = "other-task"
    paths["live_harbor_audit"].write_text(stable_json_dumps(live_payload) + "\n", encoding="utf-8")

    report = verify_live_audit_run(
        paths["audit_dir"],
        live_harbor_audit=paths["live_harbor_audit"],
        provenance=paths["provenance"],
        provenance_signature=paths["signature"],
        public_key=paths["public_key"],
        require_signature=True,
    )

    assert report.ok is False
    assert report.mode == "live_blocked"
    assert _check(report, "live_harbor_audit_task_binding")["status"] == "fail"


def test_live_audit_verify_blocks_capture_run_id_mismatch(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    live_payload = json.loads(paths["live_harbor_audit"].read_text(encoding="utf-8"))
    live_payload["capture_run_id"] = "other-capture-run"
    paths["live_harbor_audit"].write_text(stable_json_dumps(live_payload) + "\n", encoding="utf-8")

    report = verify_live_audit_run(
        paths["audit_dir"],
        live_harbor_audit=paths["live_harbor_audit"],
        provenance=paths["provenance"],
        provenance_signature=paths["signature"],
        public_key=paths["public_key"],
        require_signature=True,
    )

    assert report.ok is False
    assert report.mode == "live_blocked"
    assert _check(report, "provenance_capture_run_binding")["status"] == "fail"


def test_live_audit_verify_cli_writes_report_and_exit_codes(tmp_path: Path, capsys) -> None:
    pytest.importorskip("cryptography")
    paths = _fixture(tmp_path)
    out_path = tmp_path / "cli-report.json"

    code = main(
        [
            "audit-verify-live",
            "--audit-dir",
            str(paths["audit_dir"]),
            "--live-harbor-audit",
            str(paths["live_harbor_audit"]),
            "--provenance",
            str(paths["provenance"]),
            "--provenance-signature",
            str(paths["signature"]),
            "--public-key",
            str(paths["public_key"]),
            "--require-signature",
            "--json",
            "--out",
            str(out_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["mode"] == "live"
    assert json.loads(out_path.read_text(encoding="utf-8"))["report_hash"] == output["report_hash"]

    code = main(
        [
            "audit-verify-live",
            "--audit-dir",
            str(paths["audit_dir"]),
            "--live-harbor-audit",
            str(paths["live_harbor_audit"]),
            "--provenance",
            str(paths["provenance"]),
            "--require-signature",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["mode"] == "live_blocked"


def _fixture(tmp_path: Path) -> dict[str, Path]:
    private_key, public_key = _write_keypair(tmp_path)
    run_dir = tmp_path / "harbor-run"
    audit_dir = tmp_path / "audit"
    live_harbor_audit = tmp_path / "live_harbor_audit.json"
    provenance = tmp_path / "live-audit-provenance.json"
    signature = tmp_path / "live-audit-provenance.sig"
    _write_trial(run_dir, "held-out-smoke", 0, reward=1.0)
    _write_trial(run_dir, "held-out-smoke", 1, reward=1.0)
    ingest_harbor_run(run_dir, MANIFEST, audit_dir)
    _write_live_harbor_audit(live_harbor_audit)
    _write_provenance(provenance, live_harbor_audit)
    _write_signature(provenance, signature, private_key, public_key)
    return {
        "private_key": private_key,
        "public_key": public_key,
        "run_dir": run_dir,
        "audit_dir": audit_dir,
        "live_harbor_audit": live_harbor_audit,
        "provenance": provenance,
        "signature": signature,
    }


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    private_path = tmp_path / "fixture.ed25519"
    public_path = tmp_path / "fixture.ed25519.pub"
    private_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def _write_trial(root: Path, task_id: str, attempt: int, *, reward: float) -> None:
    trial = root / task_id / str(attempt)
    trial.mkdir(parents=True)
    trial.joinpath("metadata.json").write_text(json.dumps({"task_id": task_id}), encoding="utf-8")
    trial.joinpath("reward.json").write_text(json.dumps({"reward": reward}), encoding="utf-8")
    trial.joinpath("trajectory.jsonl").write_text(
        json.dumps({"kind": "assistant", "message": "finished"}) + "\n",
        encoding="utf-8",
    )


def _write_live_harbor_audit(path: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "capture_run_id": "terminal-bench-2.0-live-audit-fixture-001",
        "trial_artifacts": [
            {
                "task_id": "held-out-smoke",
                "captured": True,
                "verifier_outcome": "pass",
                "attempts": [
                    {"attempt_index": 0, "pass": True, "terminal_cause": None},
                    {"attempt_index": 1, "pass": True, "terminal_cause": None},
                ],
            }
        ],
        "fixed_protocol_sha256": "a" * 64,
        "reproduction_claimed": False,
    }
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _write_provenance(path: Path, live_harbor_audit: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "capture_run_id": "terminal-bench-2.0-live-audit-fixture-001",
        "harbor_version": "offline-fixture",
        "captured_at": "2026-06-24T00:00:00Z",
        "operator_label": "self-harness-fixture",
        "live_harbor_audit_artifact_path": live_harbor_audit.name,
        "reproduction_claimed": False,
    }
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _write_signature(provenance: Path, signature: Path, private_key: Path, public_key: Path) -> None:
    provenance_bytes = provenance.read_bytes()
    payload = {
        "schema_version": REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
        "manifest_filename": provenance.name,
        "manifest_sha256": sha256(provenance_bytes).hexdigest(),
        "signature_algorithm": REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
        "signature_b64": sign_bytes(provenance_bytes, private_key.read_bytes()),
        "public_key_b64": public_key_raw_b64(public_key),
        "fingerprint": public_key_fingerprint(public_key),
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "key_id": "live-audit-fixture",
        "provider": "local-fixture",
    }
    signature.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _check(report, name: str) -> dict[str, object]:
    for check in live_audit_verification_report_to_jsonable(report)["checks"]:
        if check["name"] == name:
            return check
    raise AssertionError(f"missing check: {name}")
