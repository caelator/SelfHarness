from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from self_harness._artifact_shapes import artifact_shape_error
from self_harness.capture_extract import (
    EXTRACTABLE_ARTIFACT_CLASSES,
    CaptureExtractError,
    extract_artifact_from_paths,
    parse_proposer_backend_map,
)
from self_harness.reproduction_bundle import (
    ReproductionBundleError,
    load_reproduction_bundle,
    reproduction_bundle_artifact_index,
    reproduction_bundle_report_to_jsonable,
    verify_reproduction_bundle,
)
from self_harness.reproduction_bundle_build import (
    ReproductionBundleBuildError,
    build_reproduction_bundle,
    write_reproduction_bundle,
)
from self_harness.reproduction_readiness import (
    ReproductionReadinessError,
    ReproductionRequirement,
    evaluate_reproduction_readiness,
    load_readiness_matrix_report,
    reproduction_readiness_report_to_jsonable,
)
from self_harness.types import stable_json_dumps

CAPTURE_ADMISSION_SCHEMA_VERSION = "1.0"
CAPTURE_ADMISSION_BOUNDARY = (
    "offline post-capture admission orchestration only; extracts operator-supplied raw files, "
    "packages existing live-evidence artifacts into a reproduction bundle, verifies local bundle "
    "material, and optionally evaluates readiness without contacting Harbor, Docker, registries, "
    "scanners, PyPI, Sigstore, model providers, or cloud services, and never claims benchmark "
    "reproduction"
)

_RAW_PATH_KEYS = frozenset(
    {
        "harbor_discovery_result",
        "image_policy",
        "model_backend_preflight_result",
        "network_controls",
        "harbor_run_dir",
        "capture_envelope",
        "attempts_jsonl",
        "split_manifest_result",
        "fixed_protocol_declaration",
        "fixed_protocol_result",
        "proposer_request_log",
        "proposer_context_log",
        "audit_run_dir",
    }
)
_FIXED_PROTOCOL_BOUND_CLASSES = frozenset(
    {"live_harbor_audit", "live_two_repeat_evaluation_report", "proposal_validation_manifest"}
)
_SPLIT_BOUND_CLASSES = frozenset({"proposer_context_manifest"})


class CaptureAdmissionError(ValueError):
    """Raised when post-capture admission cannot run safely."""


@dataclass(frozen=True)
class CaptureAdmissionResult:
    payload: dict[str, object]

    @property
    def ok(self) -> bool:
        return self.payload.get("ok") is True


def run_capture_admission(
    *,
    admission_id: str,
    requirements: Sequence[ReproductionRequirement],
    artifact_dir: Path,
    bundle_path: Path,
    bundle_id: str,
    operator_label: str,
    created_at: str,
    source_provider: str,
    source_captured_at: str,
    raw_inputs: Mapping[str, Mapping[str, Path]],
    raw_flags: Mapping[str, str],
    supplied_artifacts: Mapping[str, Path],
    readiness_matrix_result: Path | None = None,
    source_url: str | None = None,
    bundle_signature_path: Path | None = None,
    bundle_public_key: Path | str | None = None,
    require_bundle_signature: bool = False,
    skip_readiness: bool = False,
) -> CaptureAdmissionResult:
    admission_id = _required_string(admission_id, "admission_id")
    bundle_id = _required_string(bundle_id, "bundle_id")
    operator_label = _required_string(operator_label, "operator_label")
    created_at = _required_string(created_at, "created_at")
    source_provider = _required_string(source_provider, "source_provider")
    source_captured_at = _required_string(source_captured_at, "source_captured_at")
    if source_url is not None:
        source_url = _required_string(source_url, "source_url")
    if not skip_readiness and readiness_matrix_result is None:
        raise CaptureAdmissionError("--readiness-matrix-result is required unless --skip-readiness is set")

    required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
    required_set = frozenset(required_classes)
    _reject_unknown_classes(raw_inputs, required_set, label="raw input")
    _reject_unknown_classes(supplied_artifacts, required_set, label="supplied artifact")
    _reject_unknown_flags(raw_flags)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    stages: list[dict[str, object]] = []
    extractions: list[dict[str, object]] = []
    artifacts: dict[str, Path] = {}
    failures: list[str] = []

    for artifact_class in required_classes:
        out_path = artifact_dir / f"{artifact_class}.json"
        try:
            if artifact_class in raw_inputs:
                if artifact_class not in EXTRACTABLE_ARTIFACT_CLASSES:
                    raise CaptureAdmissionError(f"{artifact_class} does not support raw extraction")
                raw_paths = dict(raw_inputs[artifact_class])
                if artifact_class in _FIXED_PROTOCOL_BOUND_CLASSES and "fixed_protocol_result" not in raw_paths:
                    fixed_protocol_artifact = artifacts.get("fixed_protocol_config")
                    if fixed_protocol_artifact is not None:
                        raw_paths["fixed_protocol_result"] = fixed_protocol_artifact
                if artifact_class in _SPLIT_BOUND_CLASSES and "split_manifest_result" not in raw_paths:
                    split_artifact = artifacts.get("live_terminal_bench_split_manifest")
                    if split_artifact is not None:
                        raw_paths["split_manifest_result"] = split_artifact
                payload = _extract_from_raw(artifact_class, raw_paths, raw_flags)
                out_path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
                source = "extracted"
            elif artifact_class in supplied_artifacts:
                shutil.copyfile(supplied_artifacts[artifact_class], out_path)
                source = "supplied"
            elif out_path.is_file():
                source = "preexisting"
            else:
                raise CaptureAdmissionError(f"missing raw input or supplied artifact for {artifact_class}")
            shape_error = artifact_shape_error(artifact_class, out_path)
            if shape_error is not None:
                raise CaptureAdmissionError(f"invalid artifact evidence for class {artifact_class}: {shape_error}")
            artifacts[artifact_class] = out_path
            extractions.append(_artifact_row(artifact_class, out_path, source=source, status="pass"))
        except (OSError, CaptureExtractError, CaptureAdmissionError) as exc:
            failures.append(f"{artifact_class}: {exc}")
            extractions.append(
                {
                    "artifact_class": artifact_class,
                    "status": "fail",
                    "detail": str(exc),
                    "source": "extracted" if artifact_class in raw_inputs else "supplied",
                }
            )

    stages.append(_stage("artifact_admission", not failures, "artifact extraction and shape checks completed"))
    bundle_payload: dict[str, object] | None = None
    bundle_report_payload: dict[str, object] | None = None
    readiness_payload: dict[str, object]

    if failures:
        readiness_payload = {"skipped": True, "reason": "artifact admission failed"}
        return CaptureAdmissionResult(
            _report(
                admission_id=admission_id,
                operator_label=operator_label,
                created_at=created_at,
                stages=stages,
                raw_inputs=raw_input_rows(raw_inputs, raw_flags),
                extractions=extractions,
                bundle=None,
                bundle_verification=None,
                readiness=readiness_payload,
                ok=False,
            )
        )

    try:
        document = build_reproduction_bundle(
            artifacts,
            bundle_path=bundle_path,
            requirements=requirements,
            bundle_id=bundle_id,
            operator_label=operator_label,
            created_at=created_at,
            source_defaults={
                "provider": source_provider,
                "captured_at": source_captured_at,
                "operator_label": operator_label,
                **({"url": source_url} if source_url is not None else {}),
            },
            strict_shapes=True,
        )
        write_reproduction_bundle(document, bundle_path)
        bundle_sha256 = sha256(bundle_path.read_bytes()).hexdigest()
        bundle_payload = {
            "path": str(bundle_path),
            "bundle_id": bundle_id,
            "bundle_sha256": bundle_sha256,
            "operator_label": operator_label,
            "created_at": created_at,
        }
        stages.append(_stage("bundle_build", True, "reproduction bundle manifest built", path=bundle_path))
    except (OSError, ReproductionBundleBuildError) as exc:
        stages.append(_stage("bundle_build", False, str(exc), path=bundle_path))
        readiness_payload = {"skipped": True, "reason": "bundle build failed"}
        return CaptureAdmissionResult(
            _report(
                admission_id=admission_id,
                operator_label=operator_label,
                created_at=created_at,
                stages=stages,
                raw_inputs=raw_input_rows(raw_inputs, raw_flags),
                extractions=extractions,
                bundle=bundle_payload,
                bundle_verification=None,
                readiness=readiness_payload,
                ok=False,
            )
        )

    bundle_report = verify_reproduction_bundle(
        bundle_path,
        requirements,
        signature_path=bundle_signature_path,
        public_key=bundle_public_key,
        require_signature=require_bundle_signature,
    )
    bundle_report_payload = reproduction_bundle_report_to_jsonable(bundle_report)
    stages.append(
        _stage(
            "bundle_verification",
            bundle_report.ok,
            "reproduction bundle verifier accepted the bundle"
            if bundle_report.ok
            else "reproduction bundle verifier rejected the bundle",
            path=bundle_path,
            metadata={"report_hash": bundle_report.report_hash},
        )
    )

    if skip_readiness:
        readiness_payload = {"skipped": True}
    elif not bundle_report.ok:
        readiness_payload = {"skipped": True, "reason": "bundle verification failed"}
    else:
        assert readiness_matrix_result is not None
        try:
            bundle = load_reproduction_bundle(bundle_path)
            readiness_report = evaluate_reproduction_readiness(
                requirements,
                load_readiness_matrix_report(readiness_matrix_result),
                reproduction_bundle_artifact_index(bundle),
                metadata={
                    "capture_admission": {
                        "admission_id": admission_id,
                        "bundle_id": bundle_id,
                    }
                },
            )
            readiness_json = reproduction_readiness_report_to_jsonable(readiness_report)
            readiness_payload = {
                "skipped": False,
                "ok": readiness_report.ok,
                "reproduction_ready": readiness_report.reproduction_ready,
                "report_hash": readiness_report.report_hash,
                "checks": readiness_json["checks"],
            }
            stages.append(
                _stage(
                    "readiness_evaluation",
                    readiness_report.reproduction_ready,
                    "reproduction readiness passed"
                    if readiness_report.reproduction_ready
                    else "reproduction readiness is not ready",
                    metadata={"report_hash": readiness_report.report_hash},
                )
            )
        except (OSError, ReproductionBundleError, ReproductionReadinessError) as exc:
            readiness_payload = {"skipped": False, "ok": False, "error": str(exc)}
            stages.append(_stage("readiness_evaluation", False, str(exc)))

    readiness_ok = readiness_payload.get("skipped") is True or readiness_payload.get("reproduction_ready") is True
    ok = bool(bundle_report.ok and readiness_ok)
    return CaptureAdmissionResult(
        _report(
            admission_id=admission_id,
            operator_label=operator_label,
            created_at=created_at,
            stages=stages,
            raw_inputs=raw_input_rows(raw_inputs, raw_flags),
            extractions=extractions,
            bundle=bundle_payload,
            bundle_verification=bundle_report_payload,
            readiness=readiness_payload,
            ok=ok,
        )
    )


def capture_admission_report_to_jsonable(result: CaptureAdmissionResult) -> dict[str, object]:
    return dict(result.payload)


def raw_input_rows(
    raw_inputs: Mapping[str, Mapping[str, Path]],
    raw_flags: Mapping[str, str],
) -> list[dict[str, object]]:
    return [
        {
            "artifact_class": artifact_class,
            "raw_input_paths": {key: str(path) for key, path in sorted(paths.items())},
            "raw_flags": dict(sorted(raw_flags.items())),
        }
        for artifact_class, paths in sorted(raw_inputs.items())
    ]


def _extract_from_raw(
    artifact_class: str,
    paths: Mapping[str, Path],
    raw_flags: Mapping[str, str],
) -> dict[str, object]:
    unknown = sorted(set(paths) - _RAW_PATH_KEYS)
    if unknown:
        raise CaptureAdmissionError(f"unknown raw input field(s): {', '.join(unknown)}")
    return extract_artifact_from_paths(
        artifact_class,
        capture_run_id=raw_flags.get("capture_run_id"),
        harbor_discovery_result=paths.get("harbor_discovery_result"),
        harbor_version=raw_flags.get("harbor_version"),
        image_policy=paths.get("image_policy"),
        model_backend_preflight_result=paths.get("model_backend_preflight_result"),
        network_controls=paths.get("network_controls"),
        harbor_run_dir=paths.get("harbor_run_dir"),
        capture_envelope=paths.get("capture_envelope"),
        attempts_jsonl=paths.get("attempts_jsonl"),
        split_manifest_result=paths.get("split_manifest_result"),
        fixed_protocol_declaration=paths.get("fixed_protocol_declaration"),
        fixed_protocol_result=paths.get("fixed_protocol_result"),
        fixed_protocol_sha256=raw_flags.get("fixed_protocol_sha256"),
        proposer_request_log=paths.get("proposer_request_log"),
        proposer_context_log=paths.get("proposer_context_log"),
        audit_run_dir=paths.get("audit_run_dir"),
        proposer_backend_map=_proposer_backend_map_from_flags(raw_flags),
    )


def _artifact_row(artifact_class: str, path: Path, *, source: str, status: str) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "artifact_class": artifact_class,
        "status": status,
        "source": source,
        "path": str(path),
        "sha256": sha256(data).hexdigest(),
        "byte_size": len(data),
        "shape_valid": True,
    }


def _report(
    *,
    admission_id: str,
    operator_label: str,
    created_at: str,
    stages: Sequence[Mapping[str, object]],
    raw_inputs: Sequence[Mapping[str, object]],
    extractions: Sequence[Mapping[str, object]],
    bundle: Mapping[str, object] | None,
    bundle_verification: Mapping[str, object] | None,
    readiness: Mapping[str, object],
    ok: bool,
) -> dict[str, object]:
    report_without_hash: dict[str, object] = {
        "schema_version": CAPTURE_ADMISSION_SCHEMA_VERSION,
        "ok": ok,
        "admission_id": admission_id,
        "operator_label": operator_label,
        "created_at": created_at,
        "stages": [dict(stage) for stage in stages],
        "raw_inputs": [dict(row) for row in raw_inputs],
        "extractions": [dict(row) for row in extractions],
        "bundle": dict(bundle) if bundle is not None else None,
        "bundle_verification": dict(bundle_verification) if bundle_verification is not None else None,
        "readiness": dict(readiness),
        "reproduction_claimed": False,
        "boundary": CAPTURE_ADMISSION_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return {**report_without_hash, "report_hash": report_hash}


def _stage(
    name: str,
    ok: bool,
    detail: str,
    *,
    path: Path | None = None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "status": "pass" if ok else "fail",
        "detail": detail,
    }
    if path is not None:
        payload["path"] = str(path)
    if metadata is not None:
        payload["metadata"] = dict(metadata)
    return payload


def _required_string(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureAdmissionError(f"{label} must be a non-empty string")
    return value


def _reject_unknown_classes(
    values: Mapping[str, object],
    required_classes: frozenset[str],
    *,
    label: str,
) -> None:
    unknown = sorted(set(values) - required_classes)
    if unknown:
        raise CaptureAdmissionError(f"{label} references unknown artifact class(es): {', '.join(unknown)}")


def _reject_unknown_flags(raw_flags: Mapping[str, str]) -> None:
    unknown = sorted(
        set(raw_flags)
        - {
            "capture_run_id",
            "fixed_protocol_sha256",
            "harbor_version",
            "proposer_backend_map",
        }
    )
    if unknown:
        raise CaptureAdmissionError(f"unknown raw flag(s): {', '.join(unknown)}")
    for key, value in raw_flags.items():
        _required_string(value, f"raw flag {key}")


def _proposer_backend_map_from_flags(raw_flags: Mapping[str, str]) -> dict[str, str]:
    value = raw_flags.get("proposer_backend_map")
    if value is None:
        return {}
    return parse_proposer_backend_map([item for item in value.split(",") if item])
