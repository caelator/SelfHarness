from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from self_harness.operator_promotion.attest import verify_promotion_signature
from self_harness.operator_promotion.manifest import (
    canonical_manifest_bytes,
    load_promotion_manifest,
    validate_manifest_files,
)
from self_harness.operator_promotion.types import (
    PROMOTION_BOUNDARY,
    PROMOTION_VERIFICATION_SCHEMA_VERSION,
    PromotionCheck,
    PromotionError,
    PromotionVerificationReport,
)
from self_harness.types import stable_json_dumps


def verify_promotion_manifest(
    manifest_path: Path,
    *,
    signature_path: Path | None = None,
    trusted_public_key: Path | None = None,
) -> PromotionVerificationReport:
    checks: list[PromotionCheck] = []
    manifest_digest: str | None = None
    try:
        manifest = load_promotion_manifest(manifest_path)
        manifest_digest = sha256(canonical_manifest_bytes(manifest_path)).hexdigest()
        _add_check(
            checks,
            name="manifest_schema",
            passed=True,
            detail="promotion manifest schema and boundary are supported",
            path=manifest_path,
        )
        _add_check(
            checks,
            name="manifest_entries_present",
            passed=bool(manifest.entries),
            detail="promotion manifest contains at least one entry",
            path=manifest_path,
            metadata={"entry_count": len(manifest.entries)},
        )
        file_errors = validate_manifest_files(manifest_path, manifest)
        _add_check(
            checks,
            name="manifest_entry_files",
            passed=not file_errors,
            detail="promotion entry files exist and match recorded sha256/byte_size",
            path=manifest_path,
            metadata={"errors": [str(error) for error in file_errors]} if file_errors else None,
        )
    except PromotionError as exc:
        _add_check(
            checks,
            name="manifest_schema",
            passed=False,
            detail=str(exc),
            path=manifest_path,
        )
    if signature_path is None:
        _add_check(
            checks,
            name="promotion_signature",
            passed=True,
            detail="promotion signature verification skipped",
            path=None,
            metadata={"required": False},
        )
    else:
        try:
            signature = verify_promotion_signature(
                manifest_path,
                signature_path,
                trusted_public_key=trusted_public_key,
            )
            _add_check(
                checks,
                name="promotion_signature",
                passed=True,
                detail="promotion signature verified",
                path=signature_path,
                metadata={
                    "fingerprint": signature.fingerprint,
                    "provider": signature.provider,
                    "key_id": signature.key_id,
                    "mode": signature.mode,
                },
            )
        except PromotionError as exc:
            _add_check(
                checks,
                name="promotion_signature",
                passed=False,
                detail=str(exc),
                path=signature_path,
            )
    ok = all(check.status == "pass" for check in checks)
    return _report(
        manifest_path=manifest_path,
        manifest_sha256=manifest_digest,
        signature_path=signature_path,
        trusted_public_key=trusted_public_key,
        ok=ok,
        checks=tuple(checks),
    )


def promotion_verification_report_to_jsonable(report: PromotionVerificationReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "manifest_path": report.manifest_path,
        "manifest_sha256": report.manifest_sha256,
        "signature_path": report.signature_path,
        "trusted_public_key": report.trusted_public_key,
        "ok": report.ok,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "metadata": check.metadata,
            }
            for check in report.checks
        ],
        "report_hash": report.report_hash,
        "boundary": report.boundary,
    }


def _report(
    *,
    manifest_path: Path,
    manifest_sha256: str | None,
    signature_path: Path | None,
    trusted_public_key: Path | None,
    ok: bool,
    checks: tuple[PromotionCheck, ...],
) -> PromotionVerificationReport:
    report_without_hash = {
        "schema_version": PROMOTION_VERIFICATION_SCHEMA_VERSION,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "signature_path": str(signature_path) if signature_path is not None else None,
        "trusted_public_key": str(trusted_public_key) if trusted_public_key is not None else None,
        "ok": ok,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "metadata": check.metadata,
            }
            for check in checks
        ],
        "boundary": PROMOTION_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return PromotionVerificationReport(
        schema_version=PROMOTION_VERIFICATION_SCHEMA_VERSION,
        manifest_path=str(manifest_path),
        manifest_sha256=manifest_sha256,
        signature_path=str(signature_path) if signature_path is not None else None,
        trusted_public_key=str(trusted_public_key) if trusted_public_key is not None else None,
        ok=ok,
        checks=checks,
        report_hash=report_hash,
        boundary=PROMOTION_BOUNDARY,
    )


def _add_check(
    checks: list[PromotionCheck],
    *,
    name: str,
    passed: bool,
    detail: str,
    path: Path | None,
    metadata: dict[str, object] | None = None,
) -> None:
    checks.append(
        PromotionCheck(
            name=name,
            status="pass" if passed else "fail",
            detail=detail,
            path=str(path) if path is not None else None,
            metadata=metadata,
        )
    )
