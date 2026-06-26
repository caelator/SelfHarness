#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.adapters.terminal_bench.ingest import ingest_harbor_run  # noqa: E402
from self_harness.corpus_signing import (  # noqa: E402
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_raw_b64,
    sign_bytes,
)
from self_harness.reproduction_bundle import (  # noqa: E402
    REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
    REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
)
from self_harness.types import stable_json_dumps  # noqa: E402

CAPTURE_RUN_ID = "terminal-bench-2.0-live-audit-fixture-001"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _ensure_fixture_keypair(args.private_key, args.public_key)
    _write_harbor_trial(args.run_dir)
    ingest_harbor_run(args.run_dir, args.manifest, args.audit_dir)
    _write_live_harbor_audit(args.live_harbor_audit)
    _write_provenance(args.provenance, args.live_harbor_audit)
    _write_signature(args.provenance, args.signature, args.private_key, args.public_key)
    print(args.provenance)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an offline live-audit verification fixture.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--live-harbor-audit", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    return parser


def _ensure_fixture_keypair(private_key: Path, public_key: Path) -> None:
    private_key.parent.mkdir(parents=True, exist_ok=True)
    public_key.parent.mkdir(parents=True, exist_ok=True)
    seed = sha256(b"self-harness-live-audit-fixture-key").digest()
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise RuntimeError("cryptography is required to build the live audit verification fixture") from exc
    private = Ed25519PrivateKey.from_private_bytes(seed)
    private_key.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _write_harbor_trial(run_dir: Path) -> None:
    for attempt_index in (0, 1):
        trial = run_dir / "held-out-smoke" / str(attempt_index)
        trial.mkdir(parents=True, exist_ok=True)
        (trial / "metadata.json").write_text(
            stable_json_dumps({"task_id": "held-out-smoke"}) + "\n",
            encoding="utf-8",
        )
        (trial / "reward.json").write_text(stable_json_dumps({"reward": 1.0}) + "\n", encoding="utf-8")
        (trial / "trajectory.jsonl").write_text(
            stable_json_dumps({"kind": "assistant", "message": "finished"}) + "\n",
            encoding="utf-8",
        )


def _write_live_harbor_audit(path: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "capture_run_id": CAPTURE_RUN_ID,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _write_provenance(path: Path, live_harbor_audit: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "capture_run_id": CAPTURE_RUN_ID,
        "harbor_version": "offline-fixture",
        "captured_at": "2026-06-24T00:00:00Z",
        "operator_label": "self-harness-fixture",
        "live_harbor_audit_artifact_path": live_harbor_audit.name,
        "reproduction_claimed": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _write_signature(provenance: Path, signature: Path, private_key: Path, public_key: Path) -> None:
    provenance_bytes = provenance.read_bytes()
    private_key_bytes = private_key.read_bytes()
    public_key_material: Path | bytes = public_key if public_key.exists() else public_key.read_bytes()
    payload = {
        "schema_version": REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
        "manifest_filename": provenance.name,
        "manifest_sha256": sha256(provenance_bytes).hexdigest(),
        "signature_algorithm": REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
        "signature_b64": sign_bytes(provenance_bytes, private_key_bytes),
        "public_key_b64": public_key_raw_b64(public_key_material),
        "fingerprint": public_key_fingerprint(public_key_material),
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "key_id": "live-audit-fixture",
        "provider": "local-fixture",
    }
    signature.parent.mkdir(parents=True, exist_ok=True)
    signature.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
