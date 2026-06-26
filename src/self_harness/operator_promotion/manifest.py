from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from self_harness.operator_promotion.types import (
    POLICY_KINDS,
    PROMOTION_BOUNDARY,
    PROMOTION_MANIFEST_SCHEMA_VERSION,
    PROMOTION_STATUSES,
    PolicyKind,
    PromotionEntry,
    PromotionError,
    PromotionManifest,
    PromotionStatus,
)
from self_harness.types import stable_json_dumps

MANIFEST_FIELDS = frozenset({"schema_version", "entries", "boundary"})
ENTRY_FIELDS = frozenset({"name", "kind", "path", "sha256", "byte_size", "status"})


def empty_promotion_manifest() -> PromotionManifest:
    return PromotionManifest(
        schema_version=PROMOTION_MANIFEST_SCHEMA_VERSION,
        entries=(),
        boundary=PROMOTION_BOUNDARY,
    )


def init_promotion_manifest(path: Path, *, force: bool = False) -> PromotionManifest:
    if path.exists() and not force:
        raise PromotionError(f"promotion manifest already exists: {path}")
    manifest = empty_promotion_manifest()
    save_promotion_manifest(manifest, path)
    return manifest


def load_promotion_manifest(path: Path) -> PromotionManifest:
    data = _read_json_object(path)
    unknown = sorted(set(data) - MANIFEST_FIELDS)
    if unknown:
        raise PromotionError(f"promotion manifest has unknown fields: {', '.join(unknown)}")
    schema_version = _required_str(data, "schema_version")
    if schema_version != PROMOTION_MANIFEST_SCHEMA_VERSION:
        raise PromotionError(f"unsupported promotion manifest schema_version: {schema_version}")
    boundary = _required_str(data, "boundary")
    if boundary != PROMOTION_BOUNDARY:
        raise PromotionError("promotion manifest boundary does not match this implementation")
    entries_value = data.get("entries")
    if not isinstance(entries_value, list):
        raise PromotionError("promotion manifest entries must be a list")
    entries = tuple(_entry_from_json(item, index=index) for index, item in enumerate(entries_value))
    _reject_duplicate_names(entries)
    return PromotionManifest(schema_version=schema_version, entries=entries, boundary=boundary)


def save_promotion_manifest(manifest: PromotionManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(promotion_manifest_to_jsonable(manifest)) + "\n", encoding="utf-8")


def promotion_manifest_to_jsonable(manifest: PromotionManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "entries": [
            {
                "name": entry.name,
                "kind": entry.kind,
                "path": entry.path,
                "sha256": entry.sha256,
                "byte_size": entry.byte_size,
                "status": entry.status,
            }
            for entry in manifest.entries
        ],
        "boundary": manifest.boundary,
    }


def canonical_manifest_bytes(path: Path) -> bytes:
    manifest = load_promotion_manifest(path)
    return (stable_json_dumps(promotion_manifest_to_jsonable(manifest)) + "\n").encode("utf-8")


def manifest_sha256(path: Path) -> str:
    return sha256(canonical_manifest_bytes(path)).hexdigest()


def add_promotion_entry(
    manifest_path: Path,
    *,
    name: str,
    kind: str,
    file_path: Path,
    status: str = "draft",
) -> PromotionManifest:
    manifest = load_promotion_manifest(manifest_path)
    _validate_name(name)
    policy_kind = _policy_kind(kind)
    promotion_status = _promotion_status(status)
    if any(entry.name == name for entry in manifest.entries):
        raise PromotionError(f"promotion manifest already contains entry: {name}")
    resolved_file = _resolve_file(file_path)
    digest, byte_size = _hash_file(resolved_file)
    entry = PromotionEntry(
        name=name,
        kind=policy_kind,
        path=_stored_path(resolved_file, manifest_path.parent),
        sha256=digest,
        byte_size=byte_size,
        status=promotion_status,
    )
    updated = PromotionManifest(
        schema_version=manifest.schema_version,
        entries=tuple(sorted((*manifest.entries, entry), key=lambda item: item.name)),
        boundary=manifest.boundary,
    )
    save_promotion_manifest(updated, manifest_path)
    return updated


def validate_manifest_files(manifest_path: Path, manifest: PromotionManifest) -> list[PromotionError]:
    errors: list[PromotionError] = []
    names: set[str] = set()
    for entry in manifest.entries:
        if entry.name in names:
            errors.append(PromotionError(f"duplicate promotion entry name: {entry.name}"))
        names.add(entry.name)
        try:
            resolved = resolve_entry_path(manifest_path, entry)
            digest, byte_size = _hash_file(resolved)
        except PromotionError as exc:
            errors.append(exc)
            continue
        if digest != entry.sha256:
            errors.append(PromotionError(f"promotion entry sha256 mismatch: {entry.name}"))
        if byte_size != entry.byte_size:
            errors.append(PromotionError(f"promotion entry byte_size mismatch: {entry.name}"))
    return errors


def resolve_entry_path(manifest_path: Path, entry: PromotionEntry) -> Path:
    path = Path(entry.path)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return _resolve_file(path)


def _entry_from_json(value: object, *, index: int) -> PromotionEntry:
    if not isinstance(value, dict):
        raise PromotionError(f"promotion manifest entry {index} must be an object")
    data = cast(dict[str, Any], value)
    unknown = sorted(set(data) - ENTRY_FIELDS)
    if unknown:
        raise PromotionError(f"promotion manifest entry {index} has unknown fields: {', '.join(unknown)}")
    name = _required_str(data, "name")
    _validate_name(name)
    return PromotionEntry(
        name=name,
        kind=_policy_kind(_required_str(data, "kind")),
        path=_required_str(data, "path"),
        sha256=_sha256_field(data, "sha256"),
        byte_size=_nonnegative_int(data, "byte_size"),
        status=_promotion_status(_required_str(data, "status")),
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromotionError(f"missing promotion manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PromotionError(f"invalid promotion manifest JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PromotionError("promotion manifest JSON must be an object")
    return cast(dict[str, Any], data)


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PromotionError(f"promotion manifest missing non-empty string field: {key}")
    return value


def _policy_kind(value: str) -> PolicyKind:
    if value not in POLICY_KINDS:
        raise PromotionError(f"unknown promotion policy kind: {value}")
    return cast(PolicyKind, value)


def _promotion_status(value: str) -> PromotionStatus:
    if value not in PROMOTION_STATUSES:
        raise PromotionError(f"unknown promotion status: {value}")
    return cast(PromotionStatus, value)


def _sha256_field(data: dict[str, Any], key: str) -> str:
    value = _required_str(data, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise PromotionError(f"promotion manifest {key} must be a lowercase sha256 digest")
    return value


def _nonnegative_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value < 0:
        raise PromotionError(f"promotion manifest {key} must be a non-negative integer")
    return value


def _validate_name(name: str) -> None:
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise PromotionError("promotion entry name must be a simple stable identifier")


def _reject_duplicate_names(entries: tuple[PromotionEntry, ...]) -> None:
    names: set[str] = set()
    for entry in entries:
        if entry.name in names:
            raise PromotionError(f"duplicate promotion entry name: {entry.name}")
        names.add(entry.name)


def _resolve_file(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise PromotionError(f"promotion entry file does not exist: {path}")
    return resolved


def _hash_file(path: Path) -> tuple[str, int]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PromotionError(f"promotion entry file could not be read: {path}") from exc
    return sha256(payload).hexdigest(), len(payload)


def _stored_path(path: Path, base_dir: Path) -> str:
    try:
        relative = os.path.relpath(path, base_dir.resolve())
    except ValueError:
        return str(path)
    return Path(relative).as_posix()
