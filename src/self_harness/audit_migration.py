from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from self_harness.audit import SUPPORTED_SCHEMA_VERSIONS, load_audit_run
from self_harness.readiness import audit_tree_hash
from self_harness.types import stable_json_dumps, write_jsonl, write_stable_json

AUDIT_MIGRATION_REPORT_SCHEMA_VERSION = "1.0"
MIGRATION_PROVENANCE_SCHEMA_VERSION = "1.0"
LATEST_AUDIT_SCHEMA_VERSION = "1.4"
AUDIT_SCHEMA_ORDER = ("1.0", "1.1", "1.2", "1.3", "1.4")
MIGRATION_BOUNDARY = (
    "release/operator migration copy; source audit evidence is not mutated, "
    "canonical readiness hashes and default writers are not changed, no network resource is contacted, "
    "and migration output is not benchmark reproduction evidence"
)
MigrationClassification = Literal["lossless", "lossy", "unsupported"]
MigrationApply = Callable[[Path, str], tuple[str, ...]]


@dataclass(frozen=True)
class AuditMigrationReport:
    schema_version: str
    source: str
    destination: str
    from_schema_version: str
    to_schema_version: str
    files_copied: int
    changed_files: tuple[str, ...]
    source_audit_hash: str
    source_audit_hash_after: str
    destination_audit_hash: str
    classification: MigrationClassification
    transform_ids: tuple[str, ...]
    boundary: str


class AuditMigrationError(RuntimeError):
    """Raised when an audit migration cannot be performed safely."""


@dataclass(frozen=True)
class MigrationTransformResult:
    changed_files: tuple[str, ...]
    classification: MigrationClassification
    transform_ids: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationTransform:
    transform_id: str
    source_schema_version: str
    target_schema_version: str
    classification: MigrationClassification
    apply: MigrationApply
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationRegistry:
    transforms: tuple[MigrationTransform, ...]

    def transform_for(self, source_version: str, target_version: str) -> MigrationTransform | None:
        for transform in self.transforms:
            if (
                transform.source_schema_version == source_version
                and transform.target_schema_version == target_version
            ):
                return transform
        return None


def migrate_audit_tree(
    source_dir: Path,
    destination_dir: Path,
    *,
    target_schema_version: str = LATEST_AUDIT_SCHEMA_VERSION,
    target_major: str | None = None,
    allow_lossy: bool = False,
    transforms_json: Path | None = None,
) -> AuditMigrationReport:
    """Copy an audit tree and upgrade schema metadata in release/operator space only."""

    source = Path(source_dir)
    destination = Path(destination_dir)
    if not source.is_dir():
        raise AuditMigrationError(f"source audit directory does not exist: {source}")
    if destination.exists():
        raise AuditMigrationError(f"destination already exists: {destination}")
    if source.resolve() == destination.resolve():
        raise AuditMigrationError("source and destination audit directories must differ")

    target_version = _target_version(target_schema_version=target_schema_version, target_major=target_major)
    from_version = detect_audit_schema_version(source, require_explicit=target_major is not None)
    _validate_target(from_version, target_version)
    registry = _load_registry(transforms_json) if transforms_json is not None else DEFAULT_MIGRATION_REGISTRY
    transforms = _migration_path(from_version, target_version, registry=registry)
    _reject_unsupported_or_lossy(transforms, allow_lossy=allow_lossy)
    _reject_source_reproduction_claim(source)
    source_hash = audit_tree_hash(source)
    shutil.copytree(source, destination)
    changed_files: set[str] = set()
    classifications: list[MigrationClassification] = []
    transform_ids: list[str] = []
    notes: list[str] = []
    for transform in transforms:
        changed_files.update(transform.apply(destination, transform.target_schema_version))
        classifications.append(transform.classification)
        transform_ids.append(transform.transform_id)
        notes.extend(transform.notes)
    changed_files.add(
        _write_migration_provenance(
            destination,
            source_hash=source_hash,
            source_schema_version=from_version,
            target_schema_version=target_version,
            classification=_combine_classifications(classifications),
            transform_ids=tuple(transform_ids),
            notes=tuple(notes),
            allow_lossy=allow_lossy,
        )
    )

    load_audit_run(destination)
    source_hash_after = audit_tree_hash(source)
    if source_hash_after != source_hash:
        raise AuditMigrationError("source audit changed during migration")
    destination_hash = audit_tree_hash(destination)
    return AuditMigrationReport(
        schema_version=AUDIT_MIGRATION_REPORT_SCHEMA_VERSION,
        source=str(source),
        destination=str(destination),
        from_schema_version=from_version,
        to_schema_version=target_version,
        files_copied=sum(1 for path in destination.rglob("*") if path.is_file()),
        changed_files=tuple(sorted(item for item in changed_files if item)),
        source_audit_hash=source_hash,
        source_audit_hash_after=source_hash_after,
        destination_audit_hash=destination_hash,
        classification=_combine_classifications(classifications),
        transform_ids=tuple(transform_ids),
        boundary=MIGRATION_BOUNDARY,
    )


def audit_migration_report_to_jsonable(report: AuditMigrationReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "source": report.source,
        "destination": report.destination,
        "from_schema_version": report.from_schema_version,
        "to_schema_version": report.to_schema_version,
        "files_copied": report.files_copied,
        "changed_files": list(report.changed_files),
        "source_audit_hash": report.source_audit_hash,
        "source_audit_hash_after": report.source_audit_hash_after,
        "destination_audit_hash": report.destination_audit_hash,
        "classification": report.classification,
        "transform_ids": list(report.transform_ids),
        "boundary": report.boundary,
    }


def detect_audit_schema_version(path: Path, *, require_explicit: bool = False) -> str:
    manifest = _read_json_object(Path(path) / "manifest.json")
    raw_version = manifest.get("schema_version")
    if raw_version is None:
        if require_explicit:
            raise AuditMigrationError("manifest schema_version is required for breaking-schema migration")
        return "1.0"
    if not isinstance(raw_version, str):
        raise AuditMigrationError("manifest schema_version must be a string")
    if raw_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise AuditMigrationError(f"unsupported audit schema_version: {raw_version}")
    return raw_version


def _validate_target(from_version: str, target_version: str) -> None:
    if target_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise AuditMigrationError(f"unsupported target audit schema_version: {target_version}")
    from_index = AUDIT_SCHEMA_ORDER.index(from_version)
    target_index = AUDIT_SCHEMA_ORDER.index(target_version)
    if target_index < from_index:
        raise AuditMigrationError(
            f"audit migration is upgrade-only; cannot migrate {from_version} to {target_version}"
        )
    if target_index == from_index:
        raise AuditMigrationError(f"audit is already at target schema_version: {target_version}")


def _upgrade_path(from_version: str, target_version: str) -> tuple[str, ...]:
    from_index = AUDIT_SCHEMA_ORDER.index(from_version)
    target_index = AUDIT_SCHEMA_ORDER.index(target_version)
    return AUDIT_SCHEMA_ORDER[from_index + 1 : target_index + 1]


def _target_version(*, target_schema_version: str, target_major: str | None) -> str:
    if target_major is None:
        return target_schema_version
    candidates = [version for version in AUDIT_SCHEMA_ORDER if version.split(".", 1)[0] == target_major]
    if not candidates:
        raise AuditMigrationError(f"unsupported target audit schema major: {target_major}")
    return candidates[-1]


def _migration_path(
    from_version: str,
    target_version: str,
    *,
    registry: MigrationRegistry,
) -> tuple[MigrationTransform, ...]:
    direct = registry.transform_for(from_version, target_version)
    if direct is not None:
        return (direct,)
    transforms: list[MigrationTransform] = []
    current = from_version
    for next_version in _upgrade_path(from_version, target_version):
        transform = registry.transform_for(current, next_version)
        if transform is None:
            raise AuditMigrationError(f"no audit migrator registered for {current} -> {next_version}")
        transforms.append(transform)
        current = next_version
    return tuple(transforms)


def _reject_unsupported_or_lossy(transforms: tuple[MigrationTransform, ...], *, allow_lossy: bool) -> None:
    for transform in transforms:
        if transform.classification == "unsupported":
            raise AuditMigrationError(f"unsupported audit migration transform: {transform.transform_id}")
        if transform.classification == "lossy" and not allow_lossy:
            raise AuditMigrationError(
                f"lossy audit migration requires --allow-lossy: {transform.transform_id}"
            )


def _combine_classifications(classifications: list[MigrationClassification]) -> MigrationClassification:
    if not classifications:
        return "unsupported"
    if "unsupported" in classifications:
        return "unsupported"
    if "lossy" in classifications:
        return "lossy"
    return "lossless"


def _load_registry(transforms_json: Path) -> MigrationRegistry:
    data = _read_json_object(Path(transforms_json))
    if data.get("schema_version") != MIGRATION_PROVENANCE_SCHEMA_VERSION:
        raise AuditMigrationError("migration transform registry schema_version must be 1.0")
    raw_transforms = data.get("transforms")
    if not isinstance(raw_transforms, list):
        raise AuditMigrationError("migration transform registry must contain a transforms list")
    transforms: list[MigrationTransform] = []
    seen_ids: set[str] = set()
    for index, raw_transform in enumerate(raw_transforms):
        if not isinstance(raw_transform, dict):
            raise AuditMigrationError(f"migration transform {index} must be a JSON object")
        transforms.append(_load_operator_transform(raw_transform, seen_ids=seen_ids, index=index))
    return MigrationRegistry(tuple(transforms) + DEFAULT_MIGRATION_REGISTRY.transforms)


def _load_operator_transform(
    raw_transform: dict[str, Any],
    *,
    seen_ids: set[str],
    index: int,
) -> MigrationTransform:
    allowed_keys = {
        "id",
        "source_schema_version",
        "target_schema_version",
        "classification",
        "drop_manifest_fields",
        "notes",
    }
    unknown = sorted(set(raw_transform) - allowed_keys)
    if unknown:
        raise AuditMigrationError(
            f"migration transform {index} contains unsupported keys: {', '.join(unknown)}"
        )
    transform_id = _required_transform_str(raw_transform, "id", index=index)
    if transform_id in seen_ids:
        raise AuditMigrationError(f"duplicate migration transform id: {transform_id}")
    seen_ids.add(transform_id)
    source_version = _required_transform_str(raw_transform, "source_schema_version", index=index)
    target_version = _required_transform_str(raw_transform, "target_schema_version", index=index)
    classification = _classification(raw_transform.get("classification"), index=index)
    _validate_transform_versions(source_version, target_version, transform_id=transform_id)
    notes = _transform_notes(raw_transform.get("notes"), index=index)
    drop_fields = _drop_manifest_fields(raw_transform.get("drop_manifest_fields"), index=index)
    if classification == "lossless" and drop_fields:
        raise AuditMigrationError(f"lossless migration transform may not drop manifest fields: {transform_id}")
    if classification == "lossy" and not drop_fields:
        raise AuditMigrationError(f"lossy migration transform must declare drop_manifest_fields: {transform_id}")
    if classification == "unsupported" and drop_fields:
        raise AuditMigrationError(
            f"unsupported migration transform may not declare drop_manifest_fields: {transform_id}"
        )
    return MigrationTransform(
        transform_id=transform_id,
        source_schema_version=source_version,
        target_schema_version=target_version,
        classification=classification,
        apply=_operator_transform_apply(drop_fields),
        notes=notes,
    )


def _required_transform_str(raw_transform: dict[str, Any], key: str, *, index: int) -> str:
    value = raw_transform.get(key)
    if not isinstance(value, str) or not value:
        raise AuditMigrationError(f"migration transform {index} field {key} must be a non-empty string")
    return value


def _classification(value: object, *, index: int) -> MigrationClassification:
    if value == "lossless":
        return "lossless"
    if value == "lossy":
        return "lossy"
    if value == "unsupported":
        return "unsupported"
    raise AuditMigrationError(
        f"migration transform {index} classification must be lossless, lossy, or unsupported"
    )


def _transform_notes(value: object, *, index: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AuditMigrationError(f"migration transform {index} notes must be a list of strings")
    return tuple(value)


def _drop_manifest_fields(value: object, *, index: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise AuditMigrationError(f"migration transform {index} drop_manifest_fields must be a list of strings")
    fields = tuple(value)
    protected = {"schema_version", "migration_applied", "migration_provenance", "reproduction_claimed"}
    blocked = sorted(protected.intersection(fields))
    if blocked:
        raise AuditMigrationError(
            f"migration transform {index} may not drop protected manifest fields: {', '.join(blocked)}"
        )
    if len(set(fields)) != len(fields):
        raise AuditMigrationError(f"migration transform {index} drop_manifest_fields contains duplicates")
    return fields


def _validate_transform_versions(source_version: str, target_version: str, *, transform_id: str) -> None:
    if source_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise AuditMigrationError(f"unsupported transform source schema_version for {transform_id}: {source_version}")
    if target_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise AuditMigrationError(f"unsupported transform target schema_version for {transform_id}: {target_version}")
    if AUDIT_SCHEMA_ORDER.index(target_version) <= AUDIT_SCHEMA_ORDER.index(source_version):
        raise AuditMigrationError(f"migration transform must be upgrade-only: {transform_id}")


def _operator_transform_apply(drop_fields: tuple[str, ...]) -> MigrationApply:
    def apply(root: Path, target_version: str) -> tuple[str, ...]:
        changed = set(_migrate_metadata_only(root, target_version))
        if drop_fields:
            manifest_path = root / "manifest.json"
            manifest = _read_json_object(manifest_path)
            for field in drop_fields:
                manifest.pop(field, None)
            changed.add(_write_json_if_changed(root, manifest_path, manifest))
        return tuple(sorted(item for item in changed if item))

    return apply


def _reject_source_reproduction_claim(source: Path) -> None:
    manifest = _read_json_object(source / "manifest.json")
    if manifest.get("reproduction_claimed") is True:
        raise AuditMigrationError("audit migration source must not claim benchmark reproduction")


def _write_migration_provenance(
    root: Path,
    *,
    source_hash: str,
    source_schema_version: str,
    target_schema_version: str,
    classification: MigrationClassification,
    transform_ids: tuple[str, ...],
    notes: tuple[str, ...],
    allow_lossy: bool,
) -> str:
    manifest_path = root / "manifest.json"
    manifest = _read_json_object(manifest_path)
    if manifest.get("reproduction_claimed") is True:
        raise AuditMigrationError("audit migration output must not claim benchmark reproduction")
    manifest["migration_applied"] = True
    manifest["migration_provenance"] = {
        "schema_version": MIGRATION_PROVENANCE_SCHEMA_VERSION,
        "source_audit_hash": source_hash,
        "source_schema_version": source_schema_version,
        "target_schema_version": target_schema_version,
        "classification": classification,
        "transform_ids": list(transform_ids),
        "notes": list(notes),
        "lossy_allowed": allow_lossy,
        "boundary": MIGRATION_BOUNDARY,
    }
    return _write_json_if_changed(root, manifest_path, manifest)


def _migrate_metadata_only(root: Path, target_version: str) -> tuple[str, ...]:
    changed: set[str] = set()
    manifest_path = root / "manifest.json"
    manifest = _read_json_object(manifest_path)
    manifest["schema_version"] = target_version
    if "protocol_version" not in manifest and isinstance(manifest.get("protocol_hash"), str):
        manifest["protocol_version"] = manifest["protocol_hash"]
    changed.add(_write_json_if_changed(root, manifest_path, manifest))

    lineage_path = root / "lineage.json"
    lineage = _read_json_list(lineage_path)
    changed.add(_write_json_if_changed(root, lineage_path, [_row_with_schema(row, target_version) for row in lineage]))

    rounds_dir = root / "rounds"
    if not rounds_dir.is_dir():
        raise AuditMigrationError("missing rounds directory")
    for round_dir in sorted(rounds_dir.iterdir(), key=lambda item: item.name):
        if not round_dir.is_dir():
            continue
        changed.update(_migrate_jsonl_rows(root, round_dir / "proposals.jsonl", target_version, _proposal_defaults))
        changed.update(_migrate_jsonl_rows(root, round_dir / "evaluations.jsonl", target_version, _evaluation_defaults))
    return tuple(sorted(item for item in changed if item))


def _proposal_defaults(row: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    if "changed_surfaces" not in updated:
        surfaces = _changed_surfaces_from_patch(updated.get("patch"))
        if surfaces:
            updated["changed_surfaces"] = surfaces
    if "decision_reason" not in updated and isinstance(updated.get("status"), str):
        status = updated["status"]
        if status in {"accepted", "merged"}:
            updated["decision_reason"] = "migrated_accepted"
        elif status in {"rejected", "invalid"}:
            updated["decision_reason"] = "migrated_no_reason_recorded"
    return updated


def _evaluation_defaults(row: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    terminal_cause = updated.get("terminal_cause")
    if "failure_category" not in updated and isinstance(terminal_cause, str) and terminal_cause:
        updated["failure_category"] = terminal_cause
    return updated


def _changed_surfaces_from_patch(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    ops = value.get("ops")
    if not isinstance(ops, list):
        return []
    surfaces = {
        str(op["surface"])
        for op in ops
        if isinstance(op, dict) and isinstance(op.get("surface"), str) and op.get("surface")
    }
    return sorted(surfaces)


def _migrate_jsonl_rows(
    root: Path,
    path: Path,
    target_version: str,
    defaults: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[str, ...]:
    rows = [dict(defaults(row), schema_version=target_version) for row in _read_jsonl(path)]
    changed = _write_jsonl_if_changed(root, path, rows)
    return (changed,) if changed else ()


def _row_with_schema(row: object, target_version: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise AuditMigrationError("lineage entries must be JSON objects")
    return {**row, "schema_version": target_version}


def _write_json_if_changed(root: Path, path: Path, value: Any) -> str:
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    next_text = stable_json_dumps(value) + "\n"
    if previous != next_text:
        write_stable_json(path, value)
        return path.relative_to(root).as_posix()
    return ""


def _write_jsonl_if_changed(root: Path, path: Path, rows: list[dict[str, Any]]) -> str:
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    next_text = "".join(stable_json_dumps(row) + "\n" for row in rows)
    if previous != next_text:
        write_jsonl(path, rows)
        return path.relative_to(root).as_posix()
    return ""


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuditMigrationError(f"missing audit artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuditMigrationError(f"invalid JSON in audit artifact: {path}") from exc
    if not isinstance(value, dict):
        raise AuditMigrationError(f"{path} must be a JSON object")
    return value


def _read_json_list(path: Path) -> list[Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuditMigrationError(f"missing audit artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuditMigrationError(f"invalid JSON in audit artifact: {path}") from exc
    if not isinstance(value, list):
        raise AuditMigrationError(f"{path} must be a JSON list")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise AuditMigrationError(f"missing audit artifact: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditMigrationError(f"invalid JSONL row in {path}:{line_no}") from exc
        if not isinstance(value, dict):
            raise AuditMigrationError(f"{path}:{line_no} must be a JSON object")
        rows.append(value)
    return rows


DEFAULT_MIGRATION_REGISTRY = MigrationRegistry(
    (
        MigrationTransform(
            transform_id="metadata-1.0-to-1.1",
            source_schema_version="1.0",
            target_schema_version="1.1",
            classification="lossless",
            apply=_migrate_metadata_only,
            notes=("metadata-only additive migration",),
        ),
        MigrationTransform(
            transform_id="metadata-1.1-to-1.2",
            source_schema_version="1.1",
            target_schema_version="1.2",
            classification="lossless",
            apply=_migrate_metadata_only,
            notes=("metadata-only additive migration",),
        ),
        MigrationTransform(
            transform_id="metadata-1.2-to-1.3",
            source_schema_version="1.2",
            target_schema_version="1.3",
            classification="lossless",
            apply=_migrate_metadata_only,
            notes=("metadata-only additive migration",),
        ),
        MigrationTransform(
            transform_id="metadata-1.3-to-1.4",
            source_schema_version="1.3",
            target_schema_version="1.4",
            classification="lossless",
            apply=_migrate_metadata_only,
            notes=("metadata-only additive migration",),
        ),
    )
)
