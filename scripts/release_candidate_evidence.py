from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.types import stable_json_dumps  # noqa: E402

RELEASE_CANDIDATE_EVIDENCE_SCHEMA_VERSION = "1.0"
BOUNDARY = (
    "release/operator evidence aggregation only; consumes existing offline gate artifacts, "
    "does not run Harbor, Docker, registries, scanners, PyPI, Sigstore, or cloud providers, "
    "and is not benchmark reproduction evidence"
)


@dataclass(frozen=True)
class Gate:
    name: str
    status: str
    detail: str
    required: bool
    path: str | None = None
    metadata: dict[str, object] | None = None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    gates = [
        _readiness_hash_gate(args.readiness_hash),
        _audit_verify_gate(args.audit_verify_result),
        _json_ok_gate("vulnerability_policy", args.vuln_report),
        _json_ok_gate("scanner_execution", args.scanner_result),
        _json_ok_gate("scanner_db_update", args.scanner_db_update_result),
        _json_ok_gate("harbor_discovery", args.harbor_discovery_result),
        _json_ok_gate("operator_preflight", args.operator_preflight_result),
        _json_ok_gate("operator_promotion", args.operator_promotion_result),
        _operator_policy_binding_gate(args.operator_policy_binding_result),
        _provenance_gate(args.provenance),
        _reproducible_build_gate(args.reproducible_build_result),
        _signature_gate(args.provenance_signature, required=args.require_provenance_signature),
    ]
    gates.append(_readiness_matrix_gate(args.readiness_matrix_result))
    gates.append(_readiness_drift_gate(args.readiness_drift_result))
    if args.readiness_promotion_result is not None:
        gates.append(_readiness_promotion_gate(args.readiness_promotion_result))
    if args.reproduction_readiness_result is not None or args.require_reproduction_readiness:
        gates.append(
            _reproduction_readiness_gate(
                args.reproduction_readiness_result,
                require_ready=args.require_reproduction_readiness,
            )
        )
    if args.reproduction_bundle_result is not None or args.require_reproduction_readiness:
        gates.append(
            _reproduction_bundle_gate(
                args.reproduction_bundle_result,
                required=args.require_reproduction_readiness,
            )
        )
    if args.attestation_result is not None:
        gates.append(_attestation_gate(args.attestation_result))
    reproduction_violation = next(
        (gate for gate in gates if gate.status == "fail" and gate.name == "reproduction_claim"),
        None,
    )
    if reproduction_violation is None:
        gates.append(_reproduction_claim_gate(args))
    ok = all(gate.status == "pass" for gate in gates if gate.required)
    evidence = {
        "schema_version": RELEASE_CANDIDATE_EVIDENCE_SCHEMA_VERSION,
        "decision": "ready" if ok else "blocked",
        "ok": ok,
        "reproduction_claimed": False,
        "gates": [_gate_to_jsonable(gate) for gate in gates],
        "boundary": BOUNDARY,
    }
    evidence_hash = sha256((stable_json_dumps(evidence) + "\n").encode("utf-8")).hexdigest()
    evidence["evidence_sha256"] = evidence_hash
    output = stable_json_dumps(evidence) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    if args.expected_hash is not None:
        expected = args.expected_hash.read_text(encoding="utf-8").strip()
        if evidence_hash != expected:
            print(output, end="")
            print(
                f"release candidate evidence hash mismatch: expected {expected}, got {evidence_hash}",
                file=sys.stderr,
            )
            return 2
    print(output, end="")
    return 0 if ok else 2


def _readiness_hash_gate(path: Path) -> Gate:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return _fail("readiness_hash", str(exc), path)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return _fail("readiness_hash", "readiness hash must be 64 lowercase hex characters", path)
    return _pass("readiness_hash", "present", path, metadata={"sha256": value})


def _json_ok_gate(name: str, path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail(name, error, path)
    if _contains_reproduction_claim(data):
        return _fail(name, "gate artifact unexpectedly claims benchmark reproduction", path)
    if data.get("ok") is not True:
        return _fail(name, "gate artifact ok field is not true", path, metadata={"ok": _json_scalar(data.get("ok"))})
    return _pass(name, "ok", path)


def _provenance_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("provenance_manifest", error, path)
    if data.get("schema_version") != "1.0":
        return _fail("provenance_manifest", "unsupported provenance manifest schema_version", path)
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return _fail("provenance_manifest", "provenance manifest must include artifacts", path)
    if _contains_reproduction_claim(data):
        return _fail("provenance_manifest", "provenance manifest unexpectedly claims benchmark reproduction", path)
    return _pass("provenance_manifest", "present", path, metadata={"artifact_count": len(artifacts)})


def _signature_gate(path: Path | None, *, required: bool) -> Gate:
    if path is None:
        return Gate(
            name="provenance_signature",
            status="fail" if required else "skipped",
            detail="provenance signature sidecar not supplied",
            required=required,
        )
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("provenance_signature", error, path)
    if data.get("schema_version") != 1:
        return _fail("provenance_signature", "unsupported provenance signature schema_version", path)
    return _pass("provenance_signature", "present", path)


def _audit_verify_gate(path: Path | None) -> Gate:
    if path is None:
        return _fail("audit_integrity", "audit verification result not supplied", None)
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("audit_integrity", error, path)
    if _contains_reproduction_claim(data):
        return _fail("audit_integrity", "audit verification unexpectedly claims benchmark reproduction", path)
    if data.get("ok") is not True:
        return _fail("audit_integrity", "audit verification ok field is not true", path)
    report_hash = data.get("report_hash")
    metadata = {"report_hash": report_hash} if isinstance(report_hash, str) else None
    return _pass("audit_integrity", "ok", path, metadata=metadata)


def _operator_policy_binding_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("operator_policy_binding", error, path)
    if _contains_reproduction_claim(data):
        return _fail("operator_policy_binding", "binding report unexpectedly claims benchmark reproduction", path)
    if data.get("ok") is not True:
        return _fail(
            "operator_policy_binding",
            "gate artifact ok field is not true",
            path,
            metadata={"ok": _json_scalar(data.get("ok"))},
        )
    report_hash = data.get("report_hash")
    metadata = {"report_hash": report_hash} if isinstance(report_hash, str) else None
    return _pass("operator_policy_binding", "ok", path, metadata=metadata)


def _attestation_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("attestation", error, path)
    if _contains_reproduction_claim(data):
        return _fail("attestation", "attestation report unexpectedly claims benchmark reproduction", path)
    if data.get("ok") is not True:
        return _fail(
            "attestation",
            "gate artifact ok field is not true",
            path,
            metadata={"ok": _json_scalar(data.get("ok"))},
        )
    metadata: dict[str, object] = {"cryptographic_valid": _json_scalar(data.get("cryptographic_valid"))}
    report_hash = data.get("report_hash")
    if isinstance(report_hash, str):
        metadata["report_hash"] = report_hash
    return _pass("attestation", "ok", path, metadata=metadata)


def _readiness_matrix_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("readiness_matrix", error, path)
    if _contains_reproduction_claim(data):
        return _fail("readiness_matrix", "readiness matrix unexpectedly claims benchmark reproduction", path)
    if data.get("schema_version") != "1.0":
        return _fail("readiness_matrix", "unsupported readiness matrix schema_version", path)
    if data.get("ok") is not True:
        return _fail(
            "readiness_matrix",
            "gate artifact ok field is not true",
            path,
            metadata={"ok": _json_scalar(data.get("ok"))},
        )
    live_execution_blocked = data.get("live_execution_blocked")
    if not isinstance(live_execution_blocked, bool):
        return _fail("readiness_matrix", "live_execution_blocked must be a boolean", path)
    report_hash = data.get("report_hash")
    if not _is_lower_hex_sha256(report_hash):
        return _fail("readiness_matrix", "report_hash must be 64 lowercase hex characters", path)
    metadata: dict[str, object] = {
        "live_execution_blocked": live_execution_blocked,
        "report_hash": report_hash,
    }
    for key in ("blocked_count", "optional_count", "provisioned_count"):
        value = data.get(key)
        if isinstance(value, int):
            metadata[key] = value
    return _pass("readiness_matrix", "ok", path, metadata=metadata)


def _readiness_drift_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("readiness_drift", error, path)
    if _contains_reproduction_claim(data):
        return _fail("readiness_drift", "readiness drift report unexpectedly claims benchmark reproduction", path)
    if data.get("schema_version") != "1.0":
        return _fail("readiness_drift", "unsupported readiness drift schema_version", path)
    if data.get("ok") is not True:
        return _fail(
            "readiness_drift",
            "gate artifact ok field is not true",
            path,
            metadata={"ok": _json_scalar(data.get("ok"))},
        )
    report_hash = data.get("report_hash")
    if not _is_lower_hex_sha256(report_hash):
        return _fail("readiness_drift", "report_hash must be 64 lowercase hex characters", path)
    checks = data.get("checks")
    metadata: dict[str, object] = {"report_hash": report_hash}
    if isinstance(checks, list):
        metadata["check_count"] = len(checks)
    return _pass("readiness_drift", "ok", path, metadata=metadata)


def _readiness_promotion_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return Gate(
            name="readiness_promotion",
            status="fail",
            detail=error,
            required=False,
            path=str(path),
        )
    if _contains_reproduction_claim(data):
        return Gate(
            name="readiness_promotion",
            status="fail",
            detail="readiness promotion report unexpectedly claims benchmark reproduction",
            required=False,
            path=str(path),
        )
    if data.get("schema_version") != "1.0":
        return Gate(
            name="readiness_promotion",
            status="fail",
            detail="unsupported readiness promotion schema_version",
            required=False,
            path=str(path),
        )
    report_hash = data.get("report_hash")
    if not _is_lower_hex_sha256(report_hash):
        return Gate(
            name="readiness_promotion",
            status="fail",
            detail="report_hash must be 64 lowercase hex characters",
            required=False,
            path=str(path),
        )
    metadata: dict[str, object] = {"report_hash": report_hash, "ok": _json_scalar(data.get("ok"))}
    for key in ("admitted_transitions", "rejected_transitions", "advisory_transitions"):
        value = data.get(key)
        if isinstance(value, list):
            metadata[f"{key}_count"] = len(value)
    unchanged_count = data.get("unchanged_count")
    if isinstance(unchanged_count, int):
        metadata["unchanged_count"] = unchanged_count
    return Gate(
        name="readiness_promotion",
        status="pass" if data.get("ok") is True else "advisory",
        detail="ok" if data.get("ok") is True else "promotion report has rejected transitions; advisory only",
        required=False,
        path=str(path),
        metadata=metadata,
    )


def _reproduction_readiness_gate(path: Path | None, *, require_ready: bool) -> Gate:
    if path is None:
        return _fail("reproduction_readiness", "reproduction readiness result not supplied", None)
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("reproduction_readiness", error, path)
    if _contains_reproduction_claim(data):
        return _fail(
            "reproduction_readiness",
            "reproduction readiness report unexpectedly claims benchmark reproduction",
            path,
        )
    if data.get("schema_version") != "1.0":
        return _fail("reproduction_readiness", "unsupported reproduction readiness schema_version", path)
    if data.get("ok") is not True:
        return _fail(
            "reproduction_readiness",
            "reproduction readiness ok field is not true",
            path,
            metadata={"ok": _json_scalar(data.get("ok"))},
        )
    report_hash = data.get("report_hash")
    if not _is_lower_hex_sha256(report_hash):
        return _fail("reproduction_readiness", "report_hash must be 64 lowercase hex characters", path)
    reproduction_ready = data.get("reproduction_ready")
    if not isinstance(reproduction_ready, bool):
        return _fail("reproduction_readiness", "reproduction_ready must be a boolean", path)
    checks = data.get("checks")
    metadata: dict[str, object] = {
        "report_hash": report_hash,
        "reproduction_ready": reproduction_ready,
    }
    if isinstance(checks, list):
        metadata["check_count"] = len(checks)
    if require_ready and not reproduction_ready:
        return _fail(
            "reproduction_readiness",
            "benchmark reproduction readiness is not satisfied",
            path,
            metadata=metadata,
        )
    return _pass(
        "reproduction_readiness",
        "ready" if reproduction_ready else "not ready; advisory for non-reproduction release evidence",
        path,
        metadata=metadata,
    )


def _reproduction_bundle_gate(path: Path | None, *, required: bool) -> Gate:
    if path is None:
        if required:
            return _fail("reproduction_bundle", "reproduction bundle result is required", None)
        return Gate(
            name="reproduction_bundle",
            status="skipped",
            detail="reproduction bundle result not supplied",
            required=False,
        )
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("reproduction_bundle", error, path)
    if _contains_reproduction_claim(data):
        return _fail("reproduction_bundle", "reproduction bundle unexpectedly claims benchmark reproduction", path)
    if data.get("schema_version") != "1.0":
        return _fail("reproduction_bundle", "unsupported reproduction bundle schema_version", path)
    if data.get("ok") is not True:
        return _fail("reproduction_bundle", "ok field is not true", path)
    report_hash = data.get("report_hash")
    if not _is_lower_hex_sha256(report_hash):
        return _fail("reproduction_bundle", "report_hash must be 64 lowercase hex characters", path)
    metadata = {
        "bundle_id": data.get("bundle_id"),
        "bundle_sha256": data.get("bundle_sha256"),
        "report_hash": report_hash,
    }
    return _pass("reproduction_bundle", "ok", path, metadata=metadata)


def _reproducible_build_gate(path: Path) -> Gate:
    data, error = _load_json_object(path)
    if error is not None:
        return _fail("reproducible_build", error, path)
    if _contains_reproduction_claim(data):
        return _fail("reproducible_build", "reproducible build report unexpectedly claims reproduction", path)
    if data.get("schema_version") != "1.0":
        return _fail("reproducible_build", "unsupported reproducible build schema_version", path)
    if data.get("artifact_class") != "reproducible_build":
        return _fail("reproducible_build", "unsupported reproducible build artifact_class", path)
    if data.get("ok") is not True:
        return _fail(
            "reproducible_build",
            "gate artifact ok field is not true",
            path,
            metadata={"ok": _json_scalar(data.get("ok"))},
        )
    report_hash = data.get("report_hash")
    if not _is_lower_hex_sha256(report_hash):
        return _fail("reproducible_build", "report_hash must be 64 lowercase hex characters", path)
    metadata: dict[str, object] = {"report_hash": report_hash}
    for label in ("sdist", "published_wheel", "rebuilt_wheel"):
        artifact = data.get(label)
        if isinstance(artifact, dict):
            digest = artifact.get("sha256")
            if _is_lower_hex_sha256(digest):
                metadata[f"{label}_sha256"] = digest
    return _pass("reproducible_build", "ok", path, metadata=metadata)


def _reproduction_claim_gate(args: argparse.Namespace) -> Gate:
    for path in (
        args.vuln_report,
        args.scanner_result,
        args.scanner_db_update_result,
        args.harbor_discovery_result,
        args.operator_preflight_result,
        args.operator_promotion_result,
        args.operator_policy_binding_result,
        args.readiness_matrix_result,
        args.readiness_drift_result,
        args.readiness_promotion_result,
        args.attestation_result,
        args.audit_verify_result,
        args.reproduction_readiness_result,
        args.reproducible_build_result,
        args.provenance,
    ):
        if path is None:
            continue
        data, error = _load_json_object(path)
        if error is not None:
            continue
        if _contains_reproduction_claim(data):
            return _fail("reproduction_claim", f"artifact claims benchmark reproduction: {path}", path)
    return _pass("reproduction_claim", "no artifact claims benchmark reproduction", None)


def _load_json_object(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"missing artifact: {path}"
    except json.JSONDecodeError:
        return {}, f"invalid JSON artifact: {path}"
    if not isinstance(data, dict):
        return {}, f"artifact must be a JSON object: {path}"
    return data, None


def _contains_reproduction_claim(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("reproduction_claimed") is True:
            return True
        return any(_contains_reproduction_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_reproduction_claim(item) for item in value)
    return False


def _pass(name: str, detail: str, path: Path | None, *, metadata: dict[str, object] | None = None) -> Gate:
    return Gate(
        name=name,
        status="pass",
        detail=detail,
        required=True,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _fail(name: str, detail: str, path: Path | None, *, metadata: dict[str, object] | None = None) -> Gate:
    return Gate(
        name=name,
        status="fail",
        detail=detail,
        required=True,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _gate_to_jsonable(gate: Gate) -> dict[str, object]:
    return {
        "name": gate.name,
        "status": gate.status,
        "detail": gate.detail,
        "required": gate.required,
        "path": gate.path,
        "metadata": gate.metadata,
    }


def _json_scalar(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _is_lower_hex_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate release-candidate gate evidence without running live tools."
    )
    parser.add_argument("--readiness-hash", type=Path, required=True)
    parser.add_argument("--vuln-report", type=Path, required=True)
    parser.add_argument("--scanner-result", type=Path, required=True)
    parser.add_argument("--scanner-db-update-result", type=Path, required=True)
    parser.add_argument("--harbor-discovery-result", type=Path, required=True)
    parser.add_argument("--operator-preflight-result", type=Path, required=True)
    parser.add_argument("--operator-promotion-result", type=Path, required=True)
    parser.add_argument("--operator-policy-binding-result", type=Path, required=True)
    parser.add_argument("--readiness-matrix-result", type=Path, required=True)
    parser.add_argument("--readiness-drift-result", type=Path, required=True)
    parser.add_argument("--readiness-promotion-result", type=Path)
    parser.add_argument("--reproducible-build-result", type=Path, required=True)
    parser.add_argument("--reproduction-readiness-result", type=Path)
    parser.add_argument("--reproduction-bundle-result", type=Path)
    parser.add_argument("--require-reproduction-readiness", action="store_true")
    parser.add_argument("--attestation-result", type=Path)
    parser.add_argument("--audit-verify-result", type=Path)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--provenance-signature", type=Path)
    parser.add_argument("--require-provenance-signature", action="store_true")
    parser.add_argument("--expected-hash", type=Path)
    parser.add_argument("--out", type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
