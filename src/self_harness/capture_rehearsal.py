from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from self_harness.capture_manifest import (
    CaptureManifest,
    CaptureManifestError,
    load_capture_manifest,
    verify_capture_manifest,
)
from self_harness.capture_manifest_diff import diff_capture_manifest_to_bundle
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_from_private_key_pem,
    public_key_raw_b64,
    sign_bytes,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError
from self_harness.reproduction_bundle import (
    REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
    REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
    load_reproduction_bundle,
    reproduction_bundle_artifact_index,
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
)
from self_harness.signing import (
    ExternalSignerError,
    parse_external_signer_command,
    sign_payload_with_external_signer,
)
from self_harness.types import stable_json_dumps

CAPTURE_REHEARSAL_SCHEMA_VERSION = "1.0"
CAPTURE_REHEARSAL_BOUNDARY = (
    "operator capture pipeline rehearsal only; materializes planned artifact stubs, builds and "
    "optionally signs a synthetic reproduction bundle, and runs existing offline verifiers without "
    "contacting Harbor, Docker, registries, scanners, PyPI, Sigstore, model providers, or cloud "
    "services, and never claims benchmark reproduction"
)


class CaptureRehearsalError(ValueError):
    """Raised when a capture pipeline rehearsal cannot run safely."""


@dataclass(frozen=True)
class CaptureRehearsalStage:
    name: str
    status: str
    detail: str
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class CaptureRehearsalReport:
    schema_version: str
    ok: bool
    rehearsal_id: str
    manifest_path: str
    out_dir: str
    artifact_dir: str | None
    bundle_path: str | None
    bundle_signature_path: str | None
    stages: tuple[CaptureRehearsalStage, ...]
    manifest_report_hash: str | None
    bundle_report_hash: str | None
    diff_report_hash: str | None
    readiness_report_hash: str | None
    reproduction_ready: bool | None
    report_hash: str
    reproduction_claimed: bool
    boundary: str


def run_capture_rehearsal(
    *,
    manifest_path: Path,
    requirements: Sequence[ReproductionRequirement],
    readiness_matrix_report: Mapping[str, object],
    out_dir: Path,
    rehearsal_id: str,
    operator_label: str,
    manifest_signature_path: Path | None = None,
    manifest_public_key: Path | str | None = None,
    require_manifest_signature: bool = False,
    bundle_private_key: Path | None = None,
    bundle_external_signer: str | None = None,
    bundle_public_key: Path | None = None,
    bundle_fingerprint: str | None = None,
    bundle_signature_path: Path | None = None,
    bundle_signature_provider: str | None = None,
    bundle_key_id: str | None = None,
    require_bundle_signature: bool = False,
) -> CaptureRehearsalReport:
    rehearsal_id = _required_str(rehearsal_id, "rehearsal_id")
    operator_label = _required_str(operator_label, "operator_label")
    out_dir = out_dir.resolve()
    artifact_dir = out_dir / "artifacts"
    bundle_path = out_dir / "bundle.json"
    signature_path = bundle_signature_path.resolve() if bundle_signature_path is not None else bundle_path.with_suffix(
        ".json.sig"
    )

    stages: list[CaptureRehearsalStage] = []
    manifest_report = verify_capture_manifest(
        manifest_path,
        requirements,
        signature_path=manifest_signature_path,
        public_key=manifest_public_key,
        require_signature=require_manifest_signature,
    )
    stages.append(
        _stage(
            "manifest_verification",
            manifest_report.ok,
            "capture manifest verifier accepted the plan"
            if manifest_report.ok
            else "capture manifest verifier rejected the plan",
            path=manifest_path,
            metadata={"report_hash": manifest_report.report_hash},
        )
    )
    if not manifest_report.ok:
        return _report(
            rehearsal_id=rehearsal_id,
            manifest_path=manifest_path,
            out_dir=out_dir,
            artifact_dir=None,
            bundle_path=None,
            bundle_signature_path=None,
            stages=stages,
            manifest_report_hash=manifest_report.report_hash,
            bundle_report_hash=None,
            diff_report_hash=None,
            readiness_report_hash=None,
            reproduction_ready=None,
        )

    try:
        manifest = load_capture_manifest(manifest_path)
        _materialize_artifacts(manifest, artifact_dir)
        stages.append(
            _stage(
                "planned_artifacts_materialized",
                True,
                "planned artifacts materialized as synthetic rehearsal files",
                path=artifact_dir,
                metadata={"artifact_count": len(manifest.entries)},
            )
        )
        artifacts = {
            entry.required_artifact_class: artifact_dir / f"{entry.required_artifact_class}.json"
            for entry in manifest.entries
        }
        document = build_reproduction_bundle(
            artifacts,
            bundle_path=bundle_path,
            requirements=requirements,
            bundle_id=manifest.bundle_id,
            operator_label=operator_label,
            created_at=manifest.created_at,
            source_defaults={},
            entry_sources=_entry_sources(manifest),
            entry_notes=_entry_notes(manifest),
            strict_shapes=True,
        )
        write_reproduction_bundle(document, bundle_path)
        stages.append(
            _stage(
                "bundle_build",
                True,
                "synthetic reproduction bundle built from planned artifacts",
                path=bundle_path,
                metadata={"bundle_id": document.bundle_id, "entry_count": len(document.entries)},
            )
        )
    except (OSError, CaptureManifestError, CaptureRehearsalError, ReproductionBundleBuildError) as exc:
        stages.append(_stage("bundle_build", False, str(exc), path=bundle_path))
        return _report(
            rehearsal_id=rehearsal_id,
            manifest_path=manifest_path,
            out_dir=out_dir,
            artifact_dir=str(artifact_dir),
            bundle_path=str(bundle_path),
            bundle_signature_path=None,
            stages=stages,
            manifest_report_hash=manifest_report.report_hash,
            bundle_report_hash=None,
            diff_report_hash=None,
            readiness_report_hash=None,
            reproduction_ready=None,
        )

    signature_written = False
    try:
        if bundle_private_key is not None or bundle_external_signer is not None:
            sidecar = _bundle_signature_sidecar(
                bundle_path=bundle_path,
                private_key=bundle_private_key,
                external_signer=bundle_external_signer,
                public_key=bundle_public_key,
                expected_fingerprint=bundle_fingerprint,
                provider=bundle_signature_provider or manifest.signing_custody["provider"],
                key_id=bundle_key_id if bundle_key_id is not None else manifest.signing_custody.get("key_id", ""),
            )
            signature_path.parent.mkdir(parents=True, exist_ok=True)
            signature_path.write_text(stable_json_dumps(sidecar) + "\n", encoding="utf-8")
            signature_written = True
            stages.append(
                _stage(
                    "bundle_signature",
                    True,
                    "synthetic bundle signature sidecar written",
                    path=signature_path,
                    metadata={"fingerprint": sidecar["fingerprint"], "key_id": sidecar["key_id"]},
                )
            )
        elif require_bundle_signature:
            stages.append(_stage("bundle_signature", False, "bundle signature is required but no signer was supplied"))
        else:
            stages.append(
                _stage("bundle_signature", "skipped", "bundle signature not requested for advisory rehearsal")
            )
    except (OSError, CorpusSigningError, ExternalSignerError, CaptureRehearsalError) as exc:
        stages.append(_stage("bundle_signature", False, str(exc), path=signature_path))

    bundle_signature_for_checks = signature_path if signature_written else None
    bundle_report = verify_reproduction_bundle(
        bundle_path,
        requirements,
        signature_path=bundle_signature_for_checks,
        public_key=bundle_public_key,
        require_signature=require_bundle_signature,
    )
    stages.append(
        _stage(
            "bundle_verification",
            bundle_report.ok,
            "synthetic bundle verifier accepted the rehearsal bundle"
            if bundle_report.ok
            else "synthetic bundle verifier rejected the rehearsal bundle",
            path=bundle_path,
            metadata={"report_hash": bundle_report.report_hash},
        )
    )

    diff_report = diff_capture_manifest_to_bundle(
        manifest_path,
        bundle_path,
        requirements,
        manifest_signature_path=manifest_signature_path,
        bundle_signature_path=bundle_signature_for_checks,
        require_manifest_signature=require_manifest_signature,
        require_bundle_signature=require_bundle_signature,
    )
    stages.append(
        _stage(
            "manifest_bundle_diff",
            diff_report.ok,
            "capture manifest and synthetic bundle are consistent"
            if diff_report.ok
            else "capture manifest and synthetic bundle drift was detected",
            path=bundle_path,
            metadata={"report_hash": diff_report.report_hash, "matched_count": diff_report.matched_count},
        )
    )

    try:
        bundle = load_reproduction_bundle(bundle_path)
        readiness_report = evaluate_reproduction_readiness(
            requirements,
            readiness_matrix_report,
            reproduction_bundle_artifact_index(bundle),
            metadata={
                "capture_rehearsal": {
                    "rehearsal_id": rehearsal_id,
                    "bundle_id": bundle.bundle_id,
                    "bundle_report_hash": bundle_report.report_hash,
                    "diff_report_hash": diff_report.report_hash,
                }
            },
        )
        fail_count = sum(1 for check in readiness_report.checks if check.status == "fail")
        stages.append(
            _stage(
                "reproduction_readiness_evaluation",
                True,
                "reproduction-readiness evaluator completed; live dependency readiness may still be false",
                metadata={
                    "report_hash": readiness_report.report_hash,
                    "reproduction_ready": readiness_report.reproduction_ready,
                    "failed_checks": fail_count,
                },
            )
        )
    except (OSError, ReproductionReadinessError) as exc:
        readiness_report = None
        stages.append(_stage("reproduction_readiness_evaluation", False, str(exc)))

    return _report(
        rehearsal_id=rehearsal_id,
        manifest_path=manifest_path,
        out_dir=out_dir,
        artifact_dir=str(artifact_dir),
        bundle_path=str(bundle_path),
        bundle_signature_path=str(signature_path) if signature_written else None,
        stages=stages,
        manifest_report_hash=manifest_report.report_hash,
        bundle_report_hash=bundle_report.report_hash,
        diff_report_hash=diff_report.report_hash,
        readiness_report_hash=readiness_report.report_hash if readiness_report is not None else None,
        reproduction_ready=readiness_report.reproduction_ready if readiness_report is not None else None,
    )


def capture_rehearsal_report_to_jsonable(report: CaptureRehearsalReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "rehearsal_id": report.rehearsal_id,
        "manifest_path": report.manifest_path,
        "out_dir": report.out_dir,
        "artifact_dir": report.artifact_dir,
        "bundle_path": report.bundle_path,
        "bundle_signature_path": report.bundle_signature_path,
        "stages": [_stage_to_jsonable(stage) for stage in report.stages],
        "manifest_report_hash": report.manifest_report_hash,
        "bundle_report_hash": report.bundle_report_hash,
        "diff_report_hash": report.diff_report_hash,
        "readiness_report_hash": report.readiness_report_hash,
        "reproduction_ready": report.reproduction_ready,
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def _materialize_artifacts(manifest: CaptureManifest, artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for entry in manifest.entries:
        artifact = dict(entry.planned_artifact)
        if _contains_reproduction_claim(artifact):
            raise CaptureRehearsalError(
                f"planned artifact for class {entry.required_artifact_class} claims benchmark reproduction"
            )
        if artifact.get("mode") is not None and artifact.get("mode") != "live":
            raise CaptureRehearsalError(
                f"planned artifact for class {entry.required_artifact_class} mode must be live for rehearsal"
            )
        path = artifact_dir / f"{entry.required_artifact_class}.json"
        path.write_text(stable_json_dumps(artifact) + "\n", encoding="utf-8")


def _entry_sources(manifest: CaptureManifest) -> dict[str, dict[str, str]]:
    return {
        entry.required_artifact_class: {
            "provider": entry.planned_source["provider"],
            "captured_at": entry.planned_source["captured_after"],
            "operator_label": entry.planned_source["operator_label"],
        }
        for entry in manifest.entries
    }


def _entry_notes(manifest: CaptureManifest) -> dict[str, str]:
    return {
        entry.required_artifact_class: entry.notes
        for entry in manifest.entries
        if entry.notes is not None
    }


def _bundle_signature_sidecar(
    *,
    bundle_path: Path,
    private_key: Path | None,
    external_signer: str | None,
    public_key: Path | None,
    expected_fingerprint: str | None,
    provider: str,
    key_id: str,
) -> dict[str, object]:
    if private_key is not None and external_signer is not None:
        raise CaptureRehearsalError("bundle rehearsal signing accepts either private_key or external_signer, not both")
    if private_key is None and external_signer is None:
        raise CaptureRehearsalError("bundle rehearsal signing requires private_key or external_signer")
    bundle_bytes = bundle_path.read_bytes()
    if external_signer is not None:
        expected = public_key_fingerprint(public_key) if public_key is not None else expected_fingerprint
        response = sign_payload_with_external_signer(
            bundle_bytes,
            parse_external_signer_command(external_signer),
            provider=provider,
            key_id=key_id,
            expected_fingerprint=expected,
        )
        return _signature_sidecar(
            bundle_path=bundle_path,
            bundle_bytes=bundle_bytes,
            signature_b64=response.signature,
            public_key_b64=response.public_key_b64,
            fingerprint=response.fingerprint,
            provider=response.provider,
            key_id=response.key_id,
        )
    if private_key is None:
        raise CaptureRehearsalError("bundle_private_key is required unless bundle_external_signer is used")
    private_pem = private_key.read_bytes()
    public_key_material: Path | bytes = public_key if public_key is not None else public_key_from_private_key_pem(
        private_pem
    )
    fingerprint = _expected_fingerprint(public_key_material, expected_fingerprint)
    return _signature_sidecar(
        bundle_path=bundle_path,
        bundle_bytes=bundle_bytes,
        signature_b64=sign_bytes(bundle_bytes, private_pem),
        public_key_b64=public_key_raw_b64(public_key_material),
        fingerprint=fingerprint,
        provider=provider,
        key_id=key_id,
    )


def _signature_sidecar(
    *,
    bundle_path: Path,
    bundle_bytes: bytes,
    signature_b64: str,
    public_key_b64: str,
    fingerprint: str,
    provider: str,
    key_id: str,
) -> dict[str, object]:
    if public_key_fingerprint(public_key_b64) != fingerprint:
        raise CorpusSigningError("bundle signer public key fingerprint does not match signature sidecar fingerprint")
    verify_bytes_signature(bundle_bytes, signature_b64, public_key_b64)
    return {
        "schema_version": REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
        "manifest_filename": bundle_path.name,
        "manifest_sha256": sha256(bundle_bytes).hexdigest(),
        "signature_algorithm": REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
        "signature_b64": signature_b64,
        "public_key_b64": public_key_b64,
        "fingerprint": fingerprint,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "provider": provider,
        "key_id": key_id,
    }


def _expected_fingerprint(public_key: Path | str | bytes | None, expected_fingerprint: str | None) -> str:
    expected = expected_fingerprint.lower() if expected_fingerprint is not None else None
    if expected is not None and not _is_sha256(expected):
        raise CorpusSigningError("bundle signer fingerprint must be a 64-character lowercase hex digest")
    if public_key is None:
        if expected is None:
            raise CorpusSigningError("bundle signer fingerprint is required when public key is omitted")
        return expected
    public_key_fingerprint_value = public_key_fingerprint(public_key)
    if expected is not None and expected != public_key_fingerprint_value:
        raise CorpusSigningError("bundle signer public key does not match expected fingerprint")
    return public_key_fingerprint_value


def _report(
    *,
    rehearsal_id: str,
    manifest_path: Path,
    out_dir: Path,
    artifact_dir: str | None,
    bundle_path: str | None,
    bundle_signature_path: str | None,
    stages: Sequence[CaptureRehearsalStage],
    manifest_report_hash: str | None,
    bundle_report_hash: str | None,
    diff_report_hash: str | None,
    readiness_report_hash: str | None,
    reproduction_ready: bool | None,
) -> CaptureRehearsalReport:
    ok = all(stage.status != "fail" for stage in stages)
    report_without_hash = {
        "schema_version": CAPTURE_REHEARSAL_SCHEMA_VERSION,
        "ok": ok,
        "rehearsal_id": rehearsal_id,
        "manifest_path": str(manifest_path),
        "out_dir": str(out_dir),
        "artifact_dir": artifact_dir,
        "bundle_path": bundle_path,
        "bundle_signature_path": bundle_signature_path,
        "stages": [_stage_to_jsonable(stage) for stage in stages],
        "manifest_report_hash": manifest_report_hash,
        "bundle_report_hash": bundle_report_hash,
        "diff_report_hash": diff_report_hash,
        "readiness_report_hash": readiness_report_hash,
        "reproduction_ready": reproduction_ready,
        "reproduction_claimed": False,
        "boundary": CAPTURE_REHEARSAL_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return CaptureRehearsalReport(
        schema_version=CAPTURE_REHEARSAL_SCHEMA_VERSION,
        ok=ok,
        rehearsal_id=rehearsal_id,
        manifest_path=str(manifest_path),
        out_dir=str(out_dir),
        artifact_dir=artifact_dir,
        bundle_path=bundle_path,
        bundle_signature_path=bundle_signature_path,
        stages=tuple(stages),
        manifest_report_hash=manifest_report_hash,
        bundle_report_hash=bundle_report_hash,
        diff_report_hash=diff_report_hash,
        readiness_report_hash=readiness_report_hash,
        reproduction_ready=reproduction_ready,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=CAPTURE_REHEARSAL_BOUNDARY,
    )


def _stage(
    name: str,
    ok: bool | str,
    detail: str,
    *,
    path: Path | str | None = None,
    metadata: dict[str, object] | None = None,
) -> CaptureRehearsalStage:
    status = ok if isinstance(ok, str) else ("pass" if ok else "fail")
    return CaptureRehearsalStage(
        name=name,
        status=status,
        detail=detail,
        path=str(path) if path is not None else None,
        metadata=metadata,
    )


def _stage_to_jsonable(stage: CaptureRehearsalStage) -> dict[str, object]:
    return {
        "name": stage.name,
        "status": stage.status,
        "detail": stage.detail,
        "path": stage.path,
        "metadata": stage.metadata,
    }


def _required_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureRehearsalError(f"{label} must be a non-empty string")
    return value


def _contains_reproduction_claim(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("reproduction_claimed") is True:
            return True
        return any(_contains_reproduction_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_reproduction_claim(item) for item in value)
    return False


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
