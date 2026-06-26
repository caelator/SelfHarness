from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from self_harness.readiness_matrix import ReadinessMatrixCatalog, ReadinessMatrixEntry
from self_harness.readiness_promotion import contains_reproduction_claim, evaluate_provisioned_surface
from self_harness.types import stable_json_dumps

READINESS_DRIFT_SCHEMA_VERSION = "1.0"
READINESS_DRIFT_BOUNDARY = (
    "release/operator readiness drift verification only; cross-checks the declarative readiness "
    "catalog against existing offline preflight artifacts, does not run Harbor, Docker, registries, "
    "scanners, PyPI, Sigstore, models, or cloud providers, and is not benchmark reproduction evidence"
)


class ReadinessDriftError(ValueError):
    """Raised when readiness drift inputs are corrupt before drift evaluation."""


@dataclass(frozen=True)
class ReadinessDriftCheck:
    name: str
    status: str
    detail: str
    dependency: str | None = None
    preflight_surface: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ReadinessDriftReport:
    schema_version: str
    ok: bool
    checks: tuple[ReadinessDriftCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str


def evaluate_readiness_drift(
    catalog: ReadinessMatrixCatalog,
    *,
    operator_preflight_result: Mapping[str, object] | None = None,
    scanner_result: Mapping[str, object] | None = None,
    harbor_discovery_result: Mapping[str, object] | None = None,
    release_smoke_result: Mapping[str, object] | None = None,
    model_backend_preflight_result: Mapping[str, object] | None = None,
    container_preflight_result: Mapping[str, object] | None = None,
    attestation_result: Mapping[str, object] | None = None,
) -> ReadinessDriftReport:
    surface_results = {
        "operator_preflight": operator_preflight_result,
        "scanner_check": scanner_result,
        "harbor_discovery_check": harbor_discovery_result,
        "release_smoke": release_smoke_result,
        "model_backend_preflight": model_backend_preflight_result,
        "container_preflight": container_preflight_result,
        "attestation_check": attestation_result,
    }
    checks: list[ReadinessDriftCheck] = []

    for surface, result in surface_results.items():
        if result is not None and contains_reproduction_claim(result):
            checks.append(
                _fail(
                    name=f"{surface}_reproduction_claim",
                    detail="preflight surface unexpectedly claims benchmark reproduction",
                    dependency=None,
                    preflight_surface=surface,
                )
            )

    for entry in catalog.entries:
        checks.append(_evaluate_entry(entry, surface_results))

    ok = all(check.status != "fail" for check in checks)
    report_without_hash = {
        "schema_version": READINESS_DRIFT_SCHEMA_VERSION,
        "ok": ok,
        "checks": [_check_to_jsonable(check) for check in checks],
        "reproduction_claimed": False,
        "boundary": READINESS_DRIFT_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return ReadinessDriftReport(
        schema_version=READINESS_DRIFT_SCHEMA_VERSION,
        ok=ok,
        checks=tuple(checks),
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=READINESS_DRIFT_BOUNDARY,
    )


def readiness_drift_report_to_jsonable(report: ReadinessDriftReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "checks": [_check_to_jsonable(check) for check in report.checks],
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def _evaluate_entry(
    entry: ReadinessMatrixEntry,
    surface_results: dict[str, Mapping[str, object] | None],
) -> ReadinessDriftCheck:
    if entry.status != "provisioned" or not entry.reproduction_relevant:
        return _advisory(
            name=_entry_check_name(entry),
            detail="readiness entry is advisory until it is provisioned and reproduction relevant",
            dependency=entry.dependency,
            preflight_surface=entry.preflight_surface,
            metadata=_entry_metadata(entry),
        )

    surface = evaluate_provisioned_surface(entry, surface_results)
    if surface.ok:
        return _pass(
            name=_entry_check_name(entry),
            detail=surface.detail,
            dependency=entry.dependency,
            preflight_surface=surface.preflight_surface,
            metadata=surface.metadata,
        )
    return _fail(
        name=_entry_check_name(entry),
        detail=surface.detail,
        dependency=entry.dependency,
        preflight_surface=surface.preflight_surface,
        metadata=surface.metadata,
    )


def _entry_metadata(entry: ReadinessMatrixEntry) -> dict[str, object]:
    return {
        "catalog_status": entry.status,
        "operator_action": entry.operator_action,
        "reproduction_relevant": entry.reproduction_relevant,
    }


def _entry_check_name(entry: ReadinessMatrixEntry) -> str:
    return "readiness_" + _slug(entry.dependency)


def _pass(
    *,
    name: str,
    detail: str,
    dependency: str | None,
    preflight_surface: str | None,
    metadata: dict[str, object] | None = None,
) -> ReadinessDriftCheck:
    return ReadinessDriftCheck(
        name=name,
        status="pass",
        detail=detail,
        dependency=dependency,
        preflight_surface=preflight_surface,
        metadata=metadata,
    )


def _fail(
    *,
    name: str,
    detail: str,
    dependency: str | None,
    preflight_surface: str | None,
    metadata: dict[str, object] | None = None,
) -> ReadinessDriftCheck:
    return ReadinessDriftCheck(
        name=name,
        status="fail",
        detail=detail,
        dependency=dependency,
        preflight_surface=preflight_surface,
        metadata=metadata,
    )


def _advisory(
    *,
    name: str,
    detail: str,
    dependency: str | None,
    preflight_surface: str | None,
    metadata: dict[str, object] | None = None,
) -> ReadinessDriftCheck:
    return ReadinessDriftCheck(
        name=name,
        status="advisory",
        detail=detail,
        dependency=dependency,
        preflight_surface=preflight_surface,
        metadata=metadata,
    )


def _check_to_jsonable(check: ReadinessDriftCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "dependency": check.dependency,
        "preflight_surface": check.preflight_surface,
        "metadata": check.metadata,
    }


def _slug(value: str) -> str:
    chars: list[str] = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("_")
            previous_dash = True
    return "".join(chars).strip("_") or "entry"
