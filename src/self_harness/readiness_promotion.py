from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from self_harness.readiness_matrix import ReadinessMatrixCatalog, ReadinessMatrixEntry
from self_harness.types import stable_json_dumps

READINESS_PROMOTION_SCHEMA_VERSION = "1.0"
READINESS_PROMOTION_BOUNDARY = (
    "release/operator readiness promotion admission only; compares baseline and candidate readiness "
    "catalogs against existing surface artifacts, does not mutate catalogs, run Harbor, Docker, "
    "registries, scanners, PyPI, Sigstore, models, or cloud providers, and is not benchmark "
    "reproduction evidence"
)


class ReadinessPromotionError(ValueError):
    """Raised when readiness promotion inputs are corrupt before admission evaluation."""


@dataclass(frozen=True)
class ProvisionedSurfaceEvaluation:
    ok: bool
    detail: str
    preflight_surface: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class ReadinessPromotionTransition:
    dependency: str | None
    status: str
    detail: str
    transition: str
    baseline_status: str | None = None
    candidate_status: str | None = None
    preflight_surface: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ReadinessPromotionReport:
    schema_version: str
    ok: bool
    baseline_path: str
    candidate_path: str
    admitted_transitions: tuple[ReadinessPromotionTransition, ...]
    rejected_transitions: tuple[ReadinessPromotionTransition, ...]
    advisory_transitions: tuple[ReadinessPromotionTransition, ...]
    unchanged_count: int
    report_hash: str
    reproduction_claimed: bool
    boundary: str


def evaluate_readiness_promotion(
    baseline: ReadinessMatrixCatalog,
    candidate: ReadinessMatrixCatalog,
    *,
    surface_results: Mapping[str, Mapping[str, object] | None],
    allow_demotions: bool = False,
) -> ReadinessPromotionReport:
    baseline_entries = _entry_map(baseline, label="baseline")
    candidate_entries = _entry_map(candidate, label="candidate")
    admitted: list[ReadinessPromotionTransition] = []
    rejected: list[ReadinessPromotionTransition] = []
    advisory: list[ReadinessPromotionTransition] = []
    unchanged_count = 0

    for surface, result in sorted(surface_results.items()):
        if result is not None and contains_reproduction_claim(result):
            rejected.append(
                ReadinessPromotionTransition(
                    dependency=None,
                    status="rejected",
                    detail="preflight surface unexpectedly claims benchmark reproduction",
                    transition="surface-reproduction-claim",
                    preflight_surface=surface,
                )
            )

    for dependency in sorted(set(baseline_entries) - set(candidate_entries)):
        baseline_entry = baseline_entries[dependency]
        rejected.append(
            ReadinessPromotionTransition(
                dependency=dependency,
                status="rejected",
                detail="candidate catalog removed a baseline readiness entry",
                transition="removed",
                baseline_status=baseline_entry.status,
                candidate_status=None,
                preflight_surface=baseline_entry.preflight_surface,
            )
        )

    for dependency in sorted(set(candidate_entries) - set(baseline_entries)):
        candidate_entry = candidate_entries[dependency]
        _classify_new_entry(candidate_entry, surface_results, admitted, rejected, advisory)

    for dependency in sorted(set(baseline_entries).intersection(candidate_entries)):
        baseline_entry = baseline_entries[dependency]
        candidate_entry = candidate_entries[dependency]
        if baseline_entry == candidate_entry:
            unchanged_count += 1
            continue
        _classify_existing_entry(
            baseline_entry,
            candidate_entry,
            surface_results,
            allow_demotions=allow_demotions,
            admitted=admitted,
            rejected=rejected,
            advisory=advisory,
        )

    ok = not rejected
    report_without_hash = {
        "schema_version": READINESS_PROMOTION_SCHEMA_VERSION,
        "ok": ok,
        "baseline_path": str(baseline.path),
        "candidate_path": str(candidate.path),
        "admitted_transitions": [_transition_to_jsonable(transition) for transition in admitted],
        "rejected_transitions": [_transition_to_jsonable(transition) for transition in rejected],
        "advisory_transitions": [_transition_to_jsonable(transition) for transition in advisory],
        "unchanged_count": unchanged_count,
        "reproduction_claimed": False,
        "boundary": READINESS_PROMOTION_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return ReadinessPromotionReport(
        schema_version=READINESS_PROMOTION_SCHEMA_VERSION,
        ok=ok,
        baseline_path=str(baseline.path),
        candidate_path=str(candidate.path),
        admitted_transitions=tuple(admitted),
        rejected_transitions=tuple(rejected),
        advisory_transitions=tuple(advisory),
        unchanged_count=unchanged_count,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=READINESS_PROMOTION_BOUNDARY,
    )


def readiness_promotion_report_to_jsonable(report: ReadinessPromotionReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "baseline_path": report.baseline_path,
        "candidate_path": report.candidate_path,
        "admitted_transitions": [_transition_to_jsonable(transition) for transition in report.admitted_transitions],
        "rejected_transitions": [_transition_to_jsonable(transition) for transition in report.rejected_transitions],
        "advisory_transitions": [_transition_to_jsonable(transition) for transition in report.advisory_transitions],
        "unchanged_count": report.unchanged_count,
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def evaluate_provisioned_surface(
    entry: ReadinessMatrixEntry,
    surface_results: Mapping[str, Mapping[str, object] | None],
) -> ProvisionedSurfaceEvaluation:
    if entry.preflight_surface == "none":
        return _surface_fail(entry, "provisioned reproduction-relevant entry has no preflight surface")

    surface_result = surface_results.get(entry.preflight_surface)
    if surface_result is None:
        return _surface_fail(
            entry,
            "preflight surface result not supplied for provisioned reproduction-relevant entry",
        )

    if surface_result.get("ok") is not True:
        return _surface_fail(
            entry,
            "preflight surface ok field is not true",
            extra={"surface_ok": _json_scalar(surface_result.get("ok"))},
        )

    failed_checks, error = _failed_required_checks(surface_result)
    if error is not None:
        return _surface_fail(entry, error)
    if failed_checks:
        return _surface_fail(
            entry,
            "preflight surface has failed required checks",
            extra={"failed_required_checks": list(failed_checks)},
        )
    if entry.preflight_surface == "container_preflight" and surface_result.get("mode") != "live":
        return _surface_fail(
            entry,
            "container preflight surface must be a live report for provisioned readiness",
            extra={"surface_mode": _json_scalar(surface_result.get("mode"))},
        )
    if entry.preflight_surface == "attestation_check" and surface_result.get("backend") != "sigstore":
        return _surface_fail(
            entry,
            "attestation surface must use the sigstore backend for provisioned readiness",
            extra={"surface_backend": _json_scalar(surface_result.get("backend"))},
        )
    if entry.preflight_surface == "attestation_check" and surface_result.get("cryptographic_valid") is not True:
        return _surface_fail(
            entry,
            "attestation surface must be cryptographically valid for provisioned readiness",
            extra={"cryptographic_valid": _json_scalar(surface_result.get("cryptographic_valid"))},
        )
    if entry.preflight_surface == "model_backend_preflight" and surface_result.get("mode") != "live":
        return _surface_fail(
            entry,
            "model backend preflight surface must be a live report for provisioned readiness",
            extra={"surface_mode": _json_scalar(surface_result.get("mode"))},
        )

    return ProvisionedSurfaceEvaluation(
        ok=True,
        detail="provisioned readiness entry is covered by a passing preflight surface",
        preflight_surface=entry.preflight_surface,
        metadata=_entry_metadata(entry),
    )


def contains_reproduction_claim(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("reproduction_claimed") is True:
            return True
        return any(contains_reproduction_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_reproduction_claim(item) for item in value)
    return False


def _classify_new_entry(
    candidate_entry: ReadinessMatrixEntry,
    surface_results: Mapping[str, Mapping[str, object] | None],
    admitted: list[ReadinessPromotionTransition],
    rejected: list[ReadinessPromotionTransition],
    advisory: list[ReadinessPromotionTransition],
) -> None:
    if candidate_entry.status == "provisioned" and candidate_entry.reproduction_relevant:
        _classify_evidence_required(
            candidate_entry,
            "new-provisioned",
            surface_results,
            admitted=admitted,
            rejected=rejected,
            baseline_status=None,
        )
        return
    target = admitted if candidate_entry.status != "optional" else advisory
    target.append(
        ReadinessPromotionTransition(
            dependency=candidate_entry.dependency,
            status="admitted" if target is admitted else "advisory",
            detail="candidate catalog adds a non-provisioned or non-reproduction readiness entry",
            transition="added",
            baseline_status=None,
            candidate_status=candidate_entry.status,
            preflight_surface=candidate_entry.preflight_surface,
            metadata=_entry_metadata(candidate_entry),
        )
    )


def _classify_existing_entry(
    baseline_entry: ReadinessMatrixEntry,
    candidate_entry: ReadinessMatrixEntry,
    surface_results: Mapping[str, Mapping[str, object] | None],
    *,
    allow_demotions: bool,
    admitted: list[ReadinessPromotionTransition],
    rejected: list[ReadinessPromotionTransition],
    advisory: list[ReadinessPromotionTransition],
) -> None:
    if _is_demoted(baseline_entry.status, candidate_entry.status) and not allow_demotions:
        rejected.append(
            ReadinessPromotionTransition(
                dependency=candidate_entry.dependency,
                status="rejected",
                detail="candidate catalog demotes a readiness entry without --allow-demotion",
                transition="demoted",
                baseline_status=baseline_entry.status,
                candidate_status=candidate_entry.status,
                preflight_surface=candidate_entry.preflight_surface,
                metadata=_status_metadata(baseline_entry, candidate_entry),
            )
        )
        return

    if (
        candidate_entry.status == "provisioned"
        and candidate_entry.reproduction_relevant
        and baseline_entry.preflight_surface != candidate_entry.preflight_surface
    ):
        rejected.append(
            ReadinessPromotionTransition(
                dependency=candidate_entry.dependency,
                status="rejected",
                detail="candidate catalog changes preflight_surface on a provisioned reproduction-relevant row",
                transition="preflight-surface-changed",
                baseline_status=baseline_entry.status,
                candidate_status=candidate_entry.status,
                preflight_surface=candidate_entry.preflight_surface,
                metadata={
                    **_status_metadata(baseline_entry, candidate_entry),
                    "baseline_preflight_surface": baseline_entry.preflight_surface,
                    "candidate_preflight_surface": candidate_entry.preflight_surface,
                },
            )
        )
        return

    if candidate_entry.status == "provisioned" and candidate_entry.reproduction_relevant:
        transition = "promoted" if baseline_entry.status != "provisioned" else "provisioned-edit"
        _classify_evidence_required(
            candidate_entry,
            transition,
            surface_results,
            admitted=admitted,
            rejected=rejected,
            baseline_status=baseline_entry.status,
        )
        return

    target = advisory if candidate_entry.status == "optional" else admitted
    target.append(
        ReadinessPromotionTransition(
            dependency=candidate_entry.dependency,
            status="advisory" if target is advisory else "admitted",
            detail="candidate catalog changes a non-provisioned or non-reproduction readiness entry",
            transition="metadata-or-status-edit",
            baseline_status=baseline_entry.status,
            candidate_status=candidate_entry.status,
            preflight_surface=candidate_entry.preflight_surface,
            metadata=_status_metadata(baseline_entry, candidate_entry),
        )
    )


def _classify_evidence_required(
    entry: ReadinessMatrixEntry,
    transition: str,
    surface_results: Mapping[str, Mapping[str, object] | None],
    *,
    admitted: list[ReadinessPromotionTransition],
    rejected: list[ReadinessPromotionTransition],
    baseline_status: str | None,
) -> None:
    surface = evaluate_provisioned_surface(entry, surface_results)
    target = admitted if surface.ok else rejected
    target.append(
        ReadinessPromotionTransition(
            dependency=entry.dependency,
            status="admitted" if surface.ok else "rejected",
            detail=surface.detail,
            transition=transition,
            baseline_status=baseline_status,
            candidate_status=entry.status,
            preflight_surface=entry.preflight_surface,
            metadata=surface.metadata,
        )
    )


def _entry_map(catalog: ReadinessMatrixCatalog, *, label: str) -> dict[str, ReadinessMatrixEntry]:
    entries: dict[str, ReadinessMatrixEntry] = {}
    duplicates: list[str] = []
    for entry in catalog.entries:
        if entry.dependency in entries:
            duplicates.append(entry.dependency)
        entries[entry.dependency] = entry
    if duplicates:
        raise ReadinessPromotionError(f"{label} catalog has duplicate readiness dependency entries: {duplicates}")
    return entries


def _failed_required_checks(result: Mapping[str, object]) -> tuple[tuple[str, ...], str | None]:
    checks = result.get("checks")
    if checks is None:
        return (), None
    if not isinstance(checks, list):
        return (), "preflight surface checks field must be a list when present"
    failed: list[str] = []
    for index, raw_check in enumerate(checks):
        if not isinstance(raw_check, dict):
            return (), f"preflight surface check {index} must be an object"
        check = raw_check
        if (check.get("required") is True or check.get("required_for_live") is True) and check.get("status") != "pass":
            name = check.get("name")
            failed.append(str(name) if isinstance(name, str) and name else f"check_{index}")
    return tuple(failed), None


def _surface_fail(
    entry: ReadinessMatrixEntry,
    detail: str,
    *,
    extra: Mapping[str, object] | None = None,
) -> ProvisionedSurfaceEvaluation:
    return ProvisionedSurfaceEvaluation(
        ok=False,
        detail=detail,
        preflight_surface=entry.preflight_surface,
        metadata={**_entry_metadata(entry), **dict(extra or {})},
    )


def _entry_metadata(entry: ReadinessMatrixEntry) -> dict[str, object]:
    return {
        "catalog_status": entry.status,
        "operator_action": entry.operator_action,
        "reproduction_relevant": entry.reproduction_relevant,
    }


def _status_metadata(
    baseline_entry: ReadinessMatrixEntry,
    candidate_entry: ReadinessMatrixEntry,
) -> dict[str, object]:
    return {
        "baseline_status": baseline_entry.status,
        "candidate_status": candidate_entry.status,
        "reproduction_relevant": candidate_entry.reproduction_relevant,
        "operator_action": candidate_entry.operator_action,
    }


def _is_demoted(baseline_status: str, candidate_status: str) -> bool:
    order = {"blocked": 0, "optional": 1, "provisioned": 2}
    return order[candidate_status] < order[baseline_status]


def _transition_to_jsonable(transition: ReadinessPromotionTransition) -> dict[str, object]:
    return {
        "dependency": transition.dependency,
        "status": transition.status,
        "detail": transition.detail,
        "transition": transition.transition,
        "baseline_status": transition.baseline_status,
        "candidate_status": transition.candidate_status,
        "preflight_surface": transition.preflight_surface,
        "metadata": transition.metadata,
    }


def _json_scalar(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
