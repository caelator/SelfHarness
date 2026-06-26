from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from self_harness._artifact_shapes import artifact_shape_error
from self_harness.types import stable_json_dumps

REPRODUCTION_REQUIREMENTS_SCHEMA_VERSION = "1.0"
REPRODUCTION_READINESS_SCHEMA_VERSION = "1.0"
REPRODUCTION_READINESS_BOUNDARY = (
    "benchmark reproduction readiness only; maps paper reproduction requirements to existing "
    "offline artifacts and readiness reports without contacting Harbor, Docker, registries, "
    "scanners, PyPI, Sigstore, scanner databases, model providers, or cloud services, and never "
    "claims benchmark reproduction"
)

_CATALOG_FIELDS = frozenset({"schema_version", "requirements"})
_REQUIREMENT_FIELDS = frozenset(
    {
        "requirement_id",
        "paper_reference",
        "description",
        "readiness_matrix_dependencies",
        "readiness_matrix_dependency",
        "required_artifact_class",
        "required_state",
        "notes",
    }
)


class ReproductionReadinessError(ValueError):
    """Raised when reproduction-readiness inputs are malformed or unsafe."""


@dataclass(frozen=True)
class ReproductionRequirement:
    requirement_id: str
    paper_reference: str
    description: str
    readiness_matrix_dependencies: tuple[str, ...]
    required_artifact_class: str
    required_state: str
    notes: str


@dataclass(frozen=True)
class ReproductionReadinessCheck:
    requirement_id: str
    status: str
    detail: str
    paper_reference: str
    readiness_matrix_dependencies: tuple[str, ...]
    required_artifact_class: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class ReproductionReadinessReport:
    schema_version: str
    ok: bool
    reproduction_ready: bool
    checks: tuple[ReproductionReadinessCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str
    metadata: dict[str, object] | None = None


def load_reproduction_requirements(path: Path) -> tuple[ReproductionRequirement, ...]:
    data = _load_json_object(path, description="benchmark reproduction requirements")
    unknown_fields = set(data) - _CATALOG_FIELDS
    if unknown_fields:
        formatted = _format_fields(unknown_fields)
        raise ReproductionReadinessError(f"unknown reproduction requirements field(s): {formatted}")
    schema_version = data.get("schema_version")
    if schema_version != REPRODUCTION_REQUIREMENTS_SCHEMA_VERSION:
        raise ReproductionReadinessError(f"unsupported reproduction requirements schema_version: {schema_version!r}")
    raw_requirements = data.get("requirements")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ReproductionReadinessError("reproduction requirements must be a non-empty list")
    return tuple(_load_requirement(row, index) for index, row in enumerate(raw_requirements))


def load_readiness_matrix_report(path: Path) -> dict[str, object]:
    data = _load_json_object(path, description="readiness matrix report")
    if _contains_reproduction_claim(data):
        raise ReproductionReadinessError("readiness matrix report unexpectedly claims benchmark reproduction")
    if data.get("schema_version") != "1.0":
        raise ReproductionReadinessError("unsupported readiness matrix report schema_version")
    if data.get("ok") is not True:
        raise ReproductionReadinessError("readiness matrix report ok field is not true")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ReproductionReadinessError("readiness matrix report rows must be a list")
    return data


def evaluate_reproduction_readiness(
    requirements: Sequence[ReproductionRequirement],
    readiness_matrix_report: Mapping[str, object],
    artifact_index: Mapping[str, Sequence[Path]],
    *,
    metadata: Mapping[str, object] | None = None,
) -> ReproductionReadinessReport:
    if _contains_reproduction_claim(readiness_matrix_report):
        raise ReproductionReadinessError("readiness matrix report unexpectedly claims benchmark reproduction")
    rows = readiness_matrix_report.get("rows")
    if not isinstance(rows, list):
        raise ReproductionReadinessError("readiness matrix report rows must be a list")
    readiness_by_dependency = _readiness_rows_by_dependency(rows)
    checks = tuple(
        _evaluate_requirement(
            requirement,
            readiness_by_dependency=readiness_by_dependency,
            artifact_index=artifact_index,
        )
        for requirement in requirements
    )
    reproduction_ready = all(check.status == "pass" for check in checks)
    report_without_hash = {
        "schema_version": REPRODUCTION_READINESS_SCHEMA_VERSION,
        "ok": True,
        "reproduction_ready": reproduction_ready,
        "checks": [_check_to_jsonable(check) for check in checks],
        "reproduction_claimed": False,
        "boundary": REPRODUCTION_READINESS_BOUNDARY,
    }
    if metadata is not None:
        report_without_hash["metadata"] = dict(metadata)
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return ReproductionReadinessReport(
        schema_version=REPRODUCTION_READINESS_SCHEMA_VERSION,
        ok=True,
        reproduction_ready=reproduction_ready,
        checks=checks,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=REPRODUCTION_READINESS_BOUNDARY,
        metadata=dict(metadata) if metadata is not None else None,
    )


def reproduction_readiness_report_to_jsonable(report: ReproductionReadinessReport) -> dict[str, object]:
    payload = {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "reproduction_ready": report.reproduction_ready,
        "checks": [_check_to_jsonable(check) for check in report.checks],
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }
    if report.metadata is not None:
        payload["metadata"] = report.metadata
    return payload


def _load_requirement(value: object, index: int) -> ReproductionRequirement:
    if not isinstance(value, dict):
        raise ReproductionReadinessError(f"reproduction requirement {index} must be an object")
    unknown_fields = set(value) - _REQUIREMENT_FIELDS
    if unknown_fields:
        raise ReproductionReadinessError(
            f"unknown reproduction requirement field(s): {_format_fields(unknown_fields)}"
        )
    requirement_id = _required_str(value, "requirement_id", index)
    paper_reference = _required_str(value, "paper_reference", index)
    description = _required_str(value, "description", index)
    dependencies = _dependencies(value, index)
    required_artifact_class = _required_str(value, "required_artifact_class", index)
    required_state = _required_str(value, "required_state", index)
    if required_state != "provisioned":
        raise ReproductionReadinessError(
            f"reproduction requirement {index} has unsupported required_state: {required_state!r}"
        )
    notes = _required_str(value, "notes", index)
    return ReproductionRequirement(
        requirement_id=requirement_id,
        paper_reference=paper_reference,
        description=description,
        readiness_matrix_dependencies=dependencies,
        required_artifact_class=required_artifact_class,
        required_state=required_state,
        notes=notes,
    )


def _required_str(value: Mapping[str, object], key: str, index: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ReproductionReadinessError(f"reproduction requirement {index} field {key!r} must be a non-empty string")
    return item


def _dependencies(value: Mapping[str, object], index: int) -> tuple[str, ...]:
    raw_dependencies = value.get("readiness_matrix_dependencies")
    if raw_dependencies is None:
        raw_dependency = value.get("readiness_matrix_dependency")
        if not isinstance(raw_dependency, str) or not raw_dependency:
            raise ReproductionReadinessError(
                f"reproduction requirement {index} must declare readiness_matrix_dependencies"
            )
        return (raw_dependency,)
    if not isinstance(raw_dependencies, list) or not raw_dependencies:
        raise ReproductionReadinessError(
            f"reproduction requirement {index} readiness_matrix_dependencies must be a non-empty list"
        )
    dependencies: list[str] = []
    for dependency in raw_dependencies:
        if not isinstance(dependency, str) or not dependency:
            raise ReproductionReadinessError(
                f"reproduction requirement {index} readiness_matrix_dependencies must contain strings"
            )
        dependencies.append(dependency)
    return tuple(dependencies)


def _readiness_rows_by_dependency(rows: Sequence[object]) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReproductionReadinessError(f"readiness matrix row {index} must be an object")
        dependency = row.get("dependency")
        if not isinstance(dependency, str) or not dependency:
            raise ReproductionReadinessError(f"readiness matrix row {index} dependency must be a non-empty string")
        if dependency in result:
            raise ReproductionReadinessError(f"duplicate readiness matrix dependency: {dependency}")
        result[dependency] = row
    return result


def _evaluate_requirement(
    requirement: ReproductionRequirement,
    *,
    readiness_by_dependency: Mapping[str, Mapping[str, object]],
    artifact_index: Mapping[str, Sequence[Path]],
) -> ReproductionReadinessCheck:
    missing_dependencies: list[str] = []
    non_provisioned: dict[str, object] = {}
    dependency_statuses: dict[str, object] = {}
    for dependency in requirement.readiness_matrix_dependencies:
        row = readiness_by_dependency.get(dependency)
        if row is None:
            missing_dependencies.append(dependency)
            dependency_statuses[dependency] = "missing"
            continue
        status = row.get("status")
        dependency_statuses[dependency] = status if isinstance(status, str) else "malformed"
        if status != requirement.required_state:
            non_provisioned[dependency] = dependency_statuses[dependency]

    raw_artifacts = artifact_index.get(requirement.required_artifact_class, ())
    artifact_paths = tuple(path for path in raw_artifacts if path.is_file() and path.stat().st_size > 0)
    claiming_artifacts = tuple(str(path) for path in artifact_paths if _artifact_claims_reproduction(path))
    invalid_artifacts = tuple(
        f"{path}: {error}"
        for path in artifact_paths
        if (error := _artifact_evidence_error(requirement.required_artifact_class, path)) is not None
    )

    failures: list[str] = []
    if missing_dependencies:
        failures.append("missing readiness dependency: " + ", ".join(missing_dependencies))
    if non_provisioned:
        formatted = ", ".join(f"{dependency}={status}" for dependency, status in sorted(non_provisioned.items()))
        failures.append(f"readiness dependency not provisioned: {formatted}")
    if not artifact_paths:
        failures.append(f"missing non-empty artifact for class {requirement.required_artifact_class}")
    if claiming_artifacts:
        failures.append("artifact unexpectedly claims benchmark reproduction: " + ", ".join(claiming_artifacts))
    if invalid_artifacts:
        failures.append("invalid artifact evidence: " + ", ".join(invalid_artifacts))

    metadata: dict[str, object] = {
        "readiness_statuses": dependency_statuses,
        "artifact_paths": [str(path) for path in artifact_paths],
        "required_state": requirement.required_state,
        "notes": requirement.notes,
    }
    if failures:
        return ReproductionReadinessCheck(
            requirement_id=requirement.requirement_id,
            status="fail",
            detail="; ".join(failures),
            paper_reference=requirement.paper_reference,
            readiness_matrix_dependencies=requirement.readiness_matrix_dependencies,
            required_artifact_class=requirement.required_artifact_class,
            metadata=metadata,
        )
    return ReproductionReadinessCheck(
        requirement_id=requirement.requirement_id,
        status="pass",
        detail="paper reproduction requirement has provisioned readiness dependencies and artifact evidence",
        paper_reference=requirement.paper_reference,
        readiness_matrix_dependencies=requirement.readiness_matrix_dependencies,
        required_artifact_class=requirement.required_artifact_class,
        metadata=metadata,
    )


def _check_to_jsonable(check: ReproductionReadinessCheck) -> dict[str, object]:
    return {
        "requirement_id": check.requirement_id,
        "status": check.status,
        "detail": check.detail,
        "paper_reference": check.paper_reference,
        "readiness_matrix_dependencies": list(check.readiness_matrix_dependencies),
        "required_artifact_class": check.required_artifact_class,
        "metadata": check.metadata,
    }


def _load_json_object(path: Path, *, description: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReproductionReadinessError(f"missing {description}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReproductionReadinessError(f"invalid {description} JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ReproductionReadinessError(f"{description} must be a JSON object: {path}")
    if _contains_reproduction_claim(data):
        raise ReproductionReadinessError(f"{description} unexpectedly claims benchmark reproduction: {path}")
    return data


def _contains_reproduction_claim(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("reproduction_claimed") is True:
            return True
        return any(_contains_reproduction_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_reproduction_claim(item) for item in value)
    return False


def _artifact_claims_reproduction(path: Path) -> bool:
    if path.suffix != ".json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return _contains_reproduction_claim(data)


def _artifact_evidence_error(artifact_class: str, path: Path) -> str | None:
    return artifact_shape_error(artifact_class, path)


def _format_fields(fields: set[str]) -> str:
    return ", ".join(sorted(fields))
