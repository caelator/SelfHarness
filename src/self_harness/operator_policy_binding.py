from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from self_harness.operator_bundle import (
    OperatorPolicyBundle,
    OperatorPolicyBundleError,
    load_operator_policy_bundle,
)
from self_harness.operator_promotion import (
    PromotionEntry,
    PromotionError,
    load_promotion_manifest,
    verify_promotion_manifest,
)
from self_harness.operator_promotion.manifest import resolve_entry_path
from self_harness.types import stable_json_dumps

POLICY_BINDING_SCHEMA_VERSION = "1.0"
POLICY_BINDING_BOUNDARY = (
    "operator policy binding verification only; cross-checks existing operator bundle paths and "
    "promotion manifest digests, does not run Harbor, Docker, registries, scanners, PyPI, Sigstore, "
    "models, or cloud providers, and is not benchmark reproduction evidence"
)


@dataclass(frozen=True)
class PolicyBindingCheck:
    name: str
    status: str
    detail: str
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class PolicyBindingReport:
    schema_version: str
    bundle_path: str
    promotion_path: str
    signature_path: str | None
    trusted_public_key: str | None
    ok: bool
    checks: tuple[PolicyBindingCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str


@dataclass(frozen=True)
class _BundleTarget:
    kind: str
    path: Path
    label: str


def verify_policy_binding(
    bundle_path: Path,
    promotion_path: Path,
    *,
    signature_path: Path | None = None,
    trusted_public_key: Path | None = None,
    today: date | None = None,
) -> PolicyBindingReport:
    checks: list[PolicyBindingCheck] = []
    bundle: OperatorPolicyBundle | None = None
    promotion_entries: tuple[PromotionEntry, ...] = ()

    try:
        bundle = load_operator_policy_bundle(bundle_path, today=today)
        _add_check(
            checks,
            name="operator_bundle",
            passed=True,
            detail="operator policy bundle loaded",
            path=bundle_path,
        )
    except OperatorPolicyBundleError as exc:
        _add_check(
            checks,
            name="operator_bundle",
            passed=False,
            detail=str(exc),
            path=bundle_path,
        )

    promotion_report = verify_promotion_manifest(
        promotion_path,
        signature_path=signature_path,
        trusted_public_key=trusted_public_key,
    )
    _add_check(
        checks,
        name="promotion_manifest",
        passed=promotion_report.ok,
        detail=(
            "promotion manifest verification passed"
            if promotion_report.ok
            else "promotion manifest verification failed"
        ),
        path=promotion_path,
        metadata={"report_hash": promotion_report.report_hash},
    )
    try:
        promotion_entries = load_promotion_manifest(promotion_path).entries
    except PromotionError:
        promotion_entries = ()

    if bundle is not None and promotion_report.ok:
        _check_active_bindings(checks, bundle=bundle, promotion_path=promotion_path, entries=promotion_entries)

    ok = all(check.status == "pass" for check in checks)
    return _report(
        bundle_path=bundle_path,
        promotion_path=promotion_path,
        signature_path=signature_path,
        trusted_public_key=trusted_public_key,
        ok=ok,
        checks=tuple(checks),
    )


def policy_binding_report_to_jsonable(report: PolicyBindingReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "bundle_path": report.bundle_path,
        "promotion_path": report.promotion_path,
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
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def _check_active_bindings(
    checks: list[PolicyBindingCheck],
    *,
    bundle: OperatorPolicyBundle,
    promotion_path: Path,
    entries: tuple[PromotionEntry, ...],
) -> None:
    targets = _bundle_targets(bundle)
    active_entries = [entry for entry in entries if entry.status == "active"]
    active_bindings = _active_entry_bindings(promotion_path, active_entries)
    target_keys = {(target.kind, target.path) for target in targets}

    for target in targets:
        matches = active_bindings.get((target.kind, target.path), [])
        if not matches:
            _add_check(
                checks,
                name=f"binding_{target.label}",
                passed=False,
                detail="bundle policy file is missing from active promotion entries",
                path=target.path,
                metadata={"kind": target.kind},
            )
            continue
        if len(matches) > 1:
            _add_check(
                checks,
                name=f"binding_{target.label}",
                passed=False,
                detail="bundle policy file has multiple active promotion entries",
                path=target.path,
                metadata={"kind": target.kind, "entry_names": [entry.name for entry in matches]},
            )
            continue
        entry = matches[0]
        digest, byte_size = _hash_file(target.path)
        passed = entry.sha256 == digest and entry.byte_size == byte_size
        _add_check(
            checks,
            name=f"binding_{target.label}",
            passed=passed,
            detail="bundle policy file matches active promotion digest"
            if passed
            else "bundle policy file digest or byte_size does not match active promotion entry",
            path=target.path,
            metadata={
                "kind": target.kind,
                "entry_name": entry.name,
                "bundle_sha256": digest,
                "promotion_sha256": entry.sha256,
                "bundle_byte_size": byte_size,
                "promotion_byte_size": entry.byte_size,
            },
        )

    extras: list[dict[str, object]] = []
    for (kind, path), matching_entries in active_bindings.items():
        if (kind, path) in target_keys:
            continue
        for entry in matching_entries:
            extras.append({"kind": kind, "path": str(path), "entry_name": entry.name})
    _add_check(
        checks,
        name="active_promotion_entries_bound",
        passed=not extras,
        detail="all active promotion entries are referenced by the operator bundle",
        path=promotion_path,
        metadata={"extra_active_entries": extras} if extras else None,
    )


def _bundle_targets(bundle: OperatorPolicyBundle) -> tuple[_BundleTarget, ...]:
    targets: list[_BundleTarget] = []
    _append_optional(targets, kind="image_policy", path=bundle.image_policy, label="image_policy")
    _append_optional(targets, kind="freshness_policy", path=bundle.freshness_policy, label="freshness_policy")
    _append_optional(
        targets,
        kind="vulnerability_policy",
        path=bundle.vulnerability_policy,
        label="vulnerability_policy",
    )
    _append_optional(
        targets,
        kind="scanner_db_freshness_policy",
        path=bundle.scanner_db_freshness_policy,
        label="scanner_db_freshness_policy",
    )
    for index, path in enumerate(bundle.trusted_public_keys):
        targets.append(
            _BundleTarget(kind="trusted_public_keys", path=path.resolve(), label=f"trusted_public_key_{index}")
        )
    return tuple(targets)


def _append_optional(targets: list[_BundleTarget], *, kind: str, path: Path | None, label: str) -> None:
    if path is not None:
        targets.append(_BundleTarget(kind=kind, path=path.resolve(), label=label))


def _active_entry_bindings(
    promotion_path: Path,
    entries: list[PromotionEntry],
) -> dict[tuple[str, Path], list[PromotionEntry]]:
    bindings: dict[tuple[str, Path], list[PromotionEntry]] = {}
    for entry in entries:
        try:
            path = resolve_entry_path(promotion_path, entry).resolve()
        except PromotionError:
            continue
        bindings.setdefault((entry.kind, path), []).append(entry)
    return bindings


def _hash_file(path: Path) -> tuple[str, int]:
    payload = path.read_bytes()
    return sha256(payload).hexdigest(), len(payload)


def _report(
    *,
    bundle_path: Path,
    promotion_path: Path,
    signature_path: Path | None,
    trusted_public_key: Path | None,
    ok: bool,
    checks: tuple[PolicyBindingCheck, ...],
) -> PolicyBindingReport:
    report_without_hash = {
        "schema_version": POLICY_BINDING_SCHEMA_VERSION,
        "bundle_path": str(bundle_path),
        "promotion_path": str(promotion_path),
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
        "reproduction_claimed": False,
        "boundary": POLICY_BINDING_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return PolicyBindingReport(
        schema_version=POLICY_BINDING_SCHEMA_VERSION,
        bundle_path=str(bundle_path),
        promotion_path=str(promotion_path),
        signature_path=str(signature_path) if signature_path is not None else None,
        trusted_public_key=str(trusted_public_key) if trusted_public_key is not None else None,
        ok=ok,
        checks=checks,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=POLICY_BINDING_BOUNDARY,
    )


def _add_check(
    checks: list[PolicyBindingCheck],
    *,
    name: str,
    passed: bool,
    detail: str,
    path: Path | None,
    metadata: dict[str, object] | None = None,
) -> None:
    checks.append(
        PolicyBindingCheck(
            name=name,
            status="pass" if passed else "fail",
            detail=detail,
            path=str(path) if path is not None else None,
            metadata=metadata,
        )
    )
