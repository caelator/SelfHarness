from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from self_harness._artifact_shapes import artifact_shape_error_from_payload
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_raw_b64,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError
from self_harness.reproduction_bundle import (
    REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
    REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
)
from self_harness.reproduction_readiness import ReproductionRequirement
from self_harness.types import stable_json_dumps

CAPTURE_MANIFEST_SCHEMA_VERSION = "1.0"
CAPTURE_MANIFEST_REPORT_SCHEMA_VERSION = "1.0"
CAPTURE_MANIFEST_BOUNDARY = (
    "operator live-evidence capture planning only; validates an intended live artifact set, "
    "planned run parameters, signing custody, and optional detached Ed25519 signature without "
    "contacting Harbor, Docker, registries, scanners, PyPI, Sigstore, model providers, or cloud "
    "services, and never claims benchmark reproduction"
)

_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "manifest_id",
        "bundle_id",
        "operator_label",
        "created_at",
        "planned_run",
        "signing_custody",
        "entries",
        "reproduction_claimed",
    }
)
_PLANNED_RUN_FIELDS = frozenset(
    {
        "run_id",
        "mode",
        "benchmark_protocol",
        "model_backends",
        "evaluator",
        "tool_budget",
        "outbound_bandwidth_cap_bps",
        "mirrored_resources",
    }
)
_ENTRY_FIELDS = frozenset({"required_artifact_class", "planned_source", "planned_artifact", "notes"})
_PLANNED_SOURCE_FIELDS = frozenset({"provider", "captured_after", "captured_before", "operator_label"})
_SIGNING_CUSTODY_FIELDS = frozenset({"provider", "key_id", "fingerprint"})
_SIGNATURE_FIELDS = frozenset(
    {
        "schema_version",
        "manifest_sha256",
        "signature_algorithm",
        "signature_b64",
        "public_key_b64",
        "fingerprint",
        "fingerprint_algorithm",
        "provider",
        "key_id",
        "manifest_filename",
    }
)
_PAPER_MODEL_BACKENDS = frozenset({"minimax", "qwen", "glm"})


class CaptureManifestError(ValueError):
    """Raised when a capture manifest is malformed."""


@dataclass(frozen=True)
class CaptureManifestEntry:
    required_artifact_class: str
    planned_source: dict[str, str]
    planned_artifact: dict[str, Any]
    notes: str | None = None


@dataclass(frozen=True)
class CaptureManifest:
    schema_version: str
    manifest_id: str
    bundle_id: str
    operator_label: str
    created_at: str
    planned_run: dict[str, object]
    signing_custody: dict[str, str]
    entries: tuple[CaptureManifestEntry, ...]
    path: Path
    reproduction_claimed: bool


@dataclass(frozen=True)
class CaptureManifestCheck:
    name: str
    status: str
    detail: str
    artifact_class: str | None = None
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class CaptureManifestReport:
    schema_version: str
    ok: bool
    manifest_path: str
    manifest_id: str | None
    bundle_id: str | None
    manifest_sha256: str | None
    checks: tuple[CaptureManifestCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str


def load_capture_manifest(path: Path) -> CaptureManifest:
    data = _read_json_object(path, label="capture manifest")
    unknown = sorted(set(data) - _MANIFEST_FIELDS)
    if unknown:
        raise CaptureManifestError(f"capture manifest has unknown field(s): {', '.join(unknown)}")
    schema_version = _required_str(data, "schema_version", label="capture manifest")
    if schema_version != CAPTURE_MANIFEST_SCHEMA_VERSION:
        raise CaptureManifestError(f"unsupported capture manifest schema_version: {schema_version}")
    if data.get("reproduction_claimed") is not False:
        raise CaptureManifestError("capture manifest reproduction_claimed must be false")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CaptureManifestError("capture manifest entries must be a non-empty list")
    return CaptureManifest(
        schema_version=schema_version,
        manifest_id=_required_str(data, "manifest_id", label="capture manifest"),
        bundle_id=_required_str(data, "bundle_id", label="capture manifest"),
        operator_label=_required_str(data, "operator_label", label="capture manifest"),
        created_at=_required_str(data, "created_at", label="capture manifest"),
        planned_run=_planned_run(data.get("planned_run")),
        signing_custody=_signing_custody(data.get("signing_custody"), label="capture manifest signing_custody"),
        entries=tuple(_entry_from_json(row, index=index) for index, row in enumerate(raw_entries)),
        path=path,
        reproduction_claimed=False,
    )


def verify_capture_manifest(
    manifest_path: Path,
    requirements: Sequence[ReproductionRequirement],
    *,
    signature_path: Path | None = None,
    public_key: Path | str | None = None,
    require_signature: bool = False,
) -> CaptureManifestReport:
    checks: list[CaptureManifestCheck] = []
    manifest: CaptureManifest | None = None
    manifest_sha256: str | None = None
    try:
        manifest = load_capture_manifest(manifest_path)
        manifest_bytes = manifest_path.read_bytes()
        manifest_sha256 = sha256(manifest_bytes).hexdigest()
        checks.append(_pass("manifest_schema", "capture manifest schema loaded", path=manifest_path))
    except (OSError, CaptureManifestError) as exc:
        checks.append(_fail("manifest_schema", str(exc), path=manifest_path))

    if manifest is not None and manifest_sha256 is not None:
        checks.extend(_signature_checks(manifest_path, signature_path, public_key, require_signature=require_signature))
        checks.extend(_planned_run_checks(manifest))
        checks.extend(_entry_checks(manifest, requirements))

    ok = all(check.status != "fail" for check in checks)
    report_without_hash = {
        "schema_version": CAPTURE_MANIFEST_REPORT_SCHEMA_VERSION,
        "ok": ok,
        "manifest_path": str(manifest_path),
        "manifest_id": manifest.manifest_id if manifest is not None else None,
        "bundle_id": manifest.bundle_id if manifest is not None else None,
        "manifest_sha256": manifest_sha256,
        "checks": [_check_to_jsonable(check) for check in checks],
        "reproduction_claimed": False,
        "boundary": CAPTURE_MANIFEST_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return CaptureManifestReport(
        schema_version=CAPTURE_MANIFEST_REPORT_SCHEMA_VERSION,
        ok=ok,
        manifest_path=str(manifest_path),
        manifest_id=manifest.manifest_id if manifest is not None else None,
        bundle_id=manifest.bundle_id if manifest is not None else None,
        manifest_sha256=manifest_sha256,
        checks=tuple(checks),
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=CAPTURE_MANIFEST_BOUNDARY,
    )


def capture_manifest_report_to_jsonable(report: CaptureManifestReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "manifest_path": report.manifest_path,
        "manifest_id": report.manifest_id,
        "bundle_id": report.bundle_id,
        "manifest_sha256": report.manifest_sha256,
        "checks": [_check_to_jsonable(check) for check in report.checks],
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def load_capture_manifest_signature(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaptureManifestError(f"missing capture manifest signature sidecar: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CaptureManifestError(f"invalid capture manifest signature JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CaptureManifestError("capture manifest signature sidecar must be a JSON object")
    signature = cast(dict[str, object], value)
    unknown = sorted(set(signature) - _SIGNATURE_FIELDS)
    if unknown:
        raise CaptureManifestError(f"capture manifest signature has unknown field(s): {', '.join(unknown)}")
    if signature.get("schema_version") != REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION:
        raise CaptureManifestError("unsupported capture manifest signature schema_version")
    if signature.get("signature_algorithm") != REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM:
        raise CaptureManifestError("unsupported capture manifest signature_algorithm")
    if signature.get("fingerprint_algorithm") != FINGERPRINT_ALGORITHM:
        raise CaptureManifestError("unsupported capture manifest signature fingerprint_algorithm")
    for key in ("manifest_sha256", "fingerprint"):
        _sha256_field(signature, key, label="capture manifest signature")
    for key in ("signature_b64", "public_key_b64", "provider", "key_id", "manifest_filename"):
        _required_str(signature, key, label="capture manifest signature", allow_empty=(key == "key_id"))
    if Path(str(signature["manifest_filename"])).name != signature["manifest_filename"]:
        raise CaptureManifestError("capture manifest signature manifest_filename must be a basename")
    return signature


def _planned_run_checks(manifest: CaptureManifest) -> list[CaptureManifestCheck]:
    checks: list[CaptureManifestCheck] = []
    run = manifest.planned_run
    if run.get("mode") != "live":
        checks.append(_fail("planned_run_mode", "planned_run.mode must be live"))
    else:
        checks.append(_pass("planned_run_mode", "planned run is live"))
    if run.get("benchmark_protocol") != "terminal-bench@2.0":
        checks.append(_fail("planned_run_protocol", "planned_run.benchmark_protocol must be terminal-bench@2.0"))
    else:
        checks.append(_pass("planned_run_protocol", "planned benchmark protocol is terminal-bench@2.0"))
    models = run.get("model_backends")
    if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
        checks.append(_fail("planned_run_models", "planned_run.model_backends must be a list of strings"))
    elif _normal_model_backends(tuple(models)) != _PAPER_MODEL_BACKENDS:
        checks.append(
            _fail(
                "planned_run_models",
                "planned_run.model_backends must cover MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5.2",
                metadata={"model_backends": list(models)},
            )
        )
    else:
        checks.append(_pass("planned_run_models", "planned paper model backends are complete"))
    return checks


def _entry_checks(
    manifest: CaptureManifest,
    requirements: Sequence[ReproductionRequirement],
) -> list[CaptureManifestCheck]:
    required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
    required_set = frozenset(required_classes)
    by_class: dict[str, list[CaptureManifestEntry]] = {}
    checks: list[CaptureManifestCheck] = []
    for entry in manifest.entries:
        by_class.setdefault(entry.required_artifact_class, []).append(entry)

    missing = [artifact_class for artifact_class in required_classes if artifact_class not in by_class]
    extras = sorted(set(by_class) - required_set)
    duplicates = sorted(artifact_class for artifact_class, entries in by_class.items() if len(entries) > 1)
    if missing or extras or duplicates:
        parts: list[str] = []
        if missing:
            parts.append("missing required class(es): " + ", ".join(missing))
        if extras:
            parts.append("unknown class(es): " + ", ".join(extras))
        if duplicates:
            parts.append("duplicate class(es): " + ", ".join(duplicates))
        checks.append(_fail("class_coverage", "; ".join(parts), metadata={"required_classes": list(required_classes)}))
    else:
        checks.append(
            _pass(
                "class_coverage",
                "capture manifest contains exactly one entry per required artifact class",
            )
        )

    for artifact_class in sorted(by_class):
        for entry in by_class[artifact_class]:
            checks.append(_entry_shape_check(entry, known_class=artifact_class in required_set))
    return checks


def _entry_shape_check(entry: CaptureManifestEntry, *, known_class: bool) -> CaptureManifestCheck:
    if not known_class:
        return _fail(
            _entry_check_name(entry.required_artifact_class),
            "unsupported required artifact class",
            artifact_class=entry.required_artifact_class,
        )
    shape_error = artifact_shape_error_from_payload(entry.required_artifact_class, entry.planned_artifact)
    if shape_error is not None:
        return _fail(
            _entry_check_name(entry.required_artifact_class),
            "invalid planned artifact shape: " + shape_error,
            artifact_class=entry.required_artifact_class,
        )
    return _pass(
        _entry_check_name(entry.required_artifact_class),
        "planned artifact shape verified",
        artifact_class=entry.required_artifact_class,
        metadata={"source_provider": entry.planned_source.get("provider")},
    )


def _signature_checks(
    manifest_path: Path,
    signature_path: Path | None,
    public_key: Path | str | None,
    *,
    require_signature: bool,
) -> list[CaptureManifestCheck]:
    if signature_path is None:
        if require_signature:
            return [_fail("manifest_signature", "capture manifest signature is required but was not supplied")]
        return [
            _pass(
                "manifest_signature",
                "capture manifest signature not supplied; optional for advisory verification",
            )
        ]
    try:
        manifest_bytes = manifest_path.read_bytes()
        signature = load_capture_manifest_signature(signature_path)
        expected_hash = sha256(manifest_bytes).hexdigest()
        if signature["manifest_sha256"] != expected_hash:
            raise CaptureManifestError("capture manifest signature manifest_sha256 does not match manifest bytes")
        if signature["manifest_filename"] != manifest_path.name:
            raise CaptureManifestError("capture manifest signature manifest_filename does not match manifest path")
        embedded_public_key = str(signature["public_key_b64"])
        embedded_fingerprint = public_key_fingerprint(embedded_public_key)
        if signature["fingerprint"] != embedded_fingerprint:
            raise CaptureManifestError("capture manifest signature embedded public key does not match fingerprint")
        verification_key: Path | str = embedded_public_key if public_key is None else public_key
        if public_key is not None:
            trusted_fingerprint = public_key_fingerprint(public_key)
            if trusted_fingerprint != signature["fingerprint"]:
                raise CaptureManifestError("trusted public key does not match capture manifest signature fingerprint")
            if signature["public_key_b64"] != public_key_raw_b64(public_key):
                raise CaptureManifestError(
                    "capture manifest signature embedded public key does not match trusted public key"
                )
        verify_bytes_signature(manifest_bytes, str(signature["signature_b64"]), verification_key)
    except (OSError, CorpusSigningError, CaptureManifestError) as exc:
        return [_fail("manifest_signature", str(exc), path=signature_path)]
    return [
        _pass(
            "manifest_signature",
            "capture manifest signature verified",
            path=signature_path,
            metadata={"fingerprint": str(signature["fingerprint"]), "key_id": str(signature["key_id"])},
        )
    ]


def _entry_from_json(value: object, *, index: int) -> CaptureManifestEntry:
    if not isinstance(value, dict):
        raise CaptureManifestError(f"capture manifest entry {index} must be an object")
    data = cast(dict[str, object], value)
    unknown = sorted(set(data) - _ENTRY_FIELDS)
    if unknown:
        raise CaptureManifestError(f"capture manifest entry {index} has unknown field(s): {', '.join(unknown)}")
    planned_artifact = data.get("planned_artifact")
    if not isinstance(planned_artifact, dict):
        raise CaptureManifestError(f"capture manifest entry {index} planned_artifact must be an object")
    notes_value = data.get("notes")
    if notes_value is not None and not isinstance(notes_value, str):
        raise CaptureManifestError(f"capture manifest entry {index} notes must be a string when present")
    return CaptureManifestEntry(
        required_artifact_class=_required_str(data, "required_artifact_class", label=f"entry {index}"),
        planned_source=_planned_source(data.get("planned_source"), index),
        planned_artifact=cast(dict[str, Any], planned_artifact),
        notes=notes_value,
    )


def _planned_run(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CaptureManifestError("capture manifest planned_run must be an object")
    run = cast(dict[str, object], value)
    unknown = sorted(set(run) - _PLANNED_RUN_FIELDS)
    if unknown:
        raise CaptureManifestError(f"capture manifest planned_run has unknown field(s): {', '.join(unknown)}")
    for key in ("run_id", "mode", "benchmark_protocol", "evaluator"):
        _required_str(run, key, label="capture manifest planned_run")
    if not isinstance(run.get("tool_budget"), dict):
        raise CaptureManifestError("capture manifest planned_run.tool_budget must be an object")
    cap = run.get("outbound_bandwidth_cap_bps")
    if not isinstance(cap, int) or cap <= 0:
        raise CaptureManifestError("capture manifest planned_run.outbound_bandwidth_cap_bps must be positive")
    mirrored = run.get("mirrored_resources")
    if not isinstance(mirrored, list) or not all(isinstance(item, str) and item for item in mirrored):
        raise CaptureManifestError("capture manifest planned_run.mirrored_resources must be a list of strings")
    return run


def _planned_source(value: object, index: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CaptureManifestError(f"capture manifest entry {index} planned_source must be an object")
    source = cast(dict[str, object], value)
    unknown = sorted(set(source) - _PLANNED_SOURCE_FIELDS)
    if unknown:
        raise CaptureManifestError(
            f"capture manifest entry {index} planned_source has unknown field(s): {', '.join(unknown)}"
        )
    result: dict[str, str] = {}
    for key in _PLANNED_SOURCE_FIELDS:
        item = source.get(key)
        if not isinstance(item, str) or not item:
            raise CaptureManifestError(f"capture manifest entry {index} planned_source.{key} must be non-empty")
        result[key] = item
    if result["captured_after"] > result["captured_before"]:
        raise CaptureManifestError(
            f"capture manifest entry {index} planned_source captured_after must not exceed captured_before"
        )
    return result


def _signing_custody(value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CaptureManifestError(f"{label} must be an object")
    custody = cast(dict[str, object], value)
    unknown = sorted(set(custody) - _SIGNING_CUSTODY_FIELDS)
    if unknown:
        raise CaptureManifestError(f"{label} has unknown field(s): {', '.join(unknown)}")
    result = {"provider": _required_str(custody, "provider", label=label)}
    key_id = custody.get("key_id")
    if key_id is not None:
        if not isinstance(key_id, str):
            raise CaptureManifestError(f"{label}.key_id must be a string when present")
        result["key_id"] = key_id
    fingerprint = custody.get("fingerprint")
    if fingerprint is not None:
        if not isinstance(fingerprint, str) or not _is_sha256(fingerprint):
            raise CaptureManifestError(f"{label}.fingerprint must be a lowercase sha256 digest when present")
        result["fingerprint"] = fingerprint
    return result


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaptureManifestError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CaptureManifestError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(data, dict):
        raise CaptureManifestError(f"{label} must be a JSON object")
    return cast(dict[str, object], data)


def _check_to_jsonable(check: CaptureManifestCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "artifact_class": check.artifact_class,
        "path": check.path,
        "metadata": check.metadata,
    }


def _pass(
    name: str,
    detail: str,
    *,
    artifact_class: str | None = None,
    path: Path | str | None = None,
    metadata: dict[str, object] | None = None,
) -> CaptureManifestCheck:
    return CaptureManifestCheck(
        name=name,
        status="pass",
        detail=detail,
        artifact_class=artifact_class,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _fail(
    name: str,
    detail: str,
    *,
    artifact_class: str | None = None,
    path: Path | str | None = None,
    metadata: dict[str, object] | None = None,
) -> CaptureManifestCheck:
    return CaptureManifestCheck(
        name=name,
        status="fail",
        detail=detail,
        artifact_class=artifact_class,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _entry_check_name(artifact_class: str) -> str:
    return "planned_artifact_" + artifact_class


def _required_str(
    data: Mapping[str, object],
    key: str,
    *,
    label: str,
    allow_empty: bool = False,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CaptureManifestError(f"{label} missing non-empty string field: {key}")
    return value


def _sha256_field(data: Mapping[str, object], key: str, *, label: str) -> str:
    value = _required_str(data, key, label=label)
    if not _is_sha256(value):
        raise CaptureManifestError(f"{label} {key} must be a lowercase sha256 digest")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _normal_model_backends(values: tuple[str, ...]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in values:
        key = value.lower().replace("_", "-").replace(" ", "-")
        if key in {"minimax", "minimax-m2.5", "minimax-m25", "minimax-m2-5"}:
            normalized.add("minimax")
        elif key in {"qwen", "qwen3.5-35b-a3b", "qwen3-5-35b-a3b", "qwen35-35b-a3b"}:
            normalized.add("qwen")
        elif key in {"glm", "glm-5", "glm5", "glm-5.2", "glm-52", "glm52"}:
            normalized.add("glm")
        else:
            normalized.add(key)
    return frozenset(normalized)
