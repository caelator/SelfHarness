from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from self_harness._artifact_shapes import artifact_shape_error
from self_harness.reproduction_bundle import (
    REPRODUCTION_BUNDLE_SCHEMA_VERSION,
    ReproductionBundleEntry,
)
from self_harness.reproduction_readiness import ReproductionRequirement
from self_harness.types import stable_json_dumps

SOURCE_FIELDS = frozenset({"provider", "url", "captured_at", "operator_label"})


class ReproductionBundleBuildError(ValueError):
    """Raised when a reproduction evidence bundle cannot be authored safely."""


@dataclass(frozen=True)
class ReproductionBundleDocument:
    schema_version: str
    bundle_id: str
    created_at: str
    operator_label: str
    entries: tuple[ReproductionBundleEntry, ...]
    reproduction_claimed: bool = False


def build_reproduction_bundle(
    artifacts: Mapping[str, Path],
    *,
    bundle_path: Path,
    requirements: Sequence[ReproductionRequirement],
    bundle_id: str,
    operator_label: str,
    created_at: str,
    source_defaults: Mapping[str, str],
    entry_sources: Mapping[str, Mapping[str, str]] | None = None,
    entry_notes: Mapping[str, str] | None = None,
    strict_shapes: bool = True,
) -> ReproductionBundleDocument:
    """Build a deterministic reproduction evidence bundle manifest from existing artifacts."""

    bundle_id = _required_string(bundle_id, "bundle_id")
    operator_label = _required_string(operator_label, "operator_label")
    created_at = _required_string(created_at, "created_at")
    source = _source(source_defaults, label="source defaults")
    required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
    required_set = frozenset(required_classes)
    artifact_classes = frozenset(artifacts)
    missing = [artifact_class for artifact_class in required_classes if artifact_class not in artifact_classes]
    unknown = sorted(artifact_classes - required_set)
    if missing or unknown:
        parts: list[str] = []
        if missing:
            parts.append("missing required class(es): " + ", ".join(missing))
        if unknown:
            parts.append("unknown class(es): " + ", ".join(unknown))
        raise ReproductionBundleBuildError("; ".join(parts))

    entry_sources = entry_sources or {}
    entry_notes = entry_notes or {}
    unknown_entry_sources = sorted(set(entry_sources) - required_set)
    unknown_entry_notes = sorted(set(entry_notes) - required_set)
    if unknown_entry_sources or unknown_entry_notes:
        parts = []
        if unknown_entry_sources:
            parts.append("unknown entry source class(es): " + ", ".join(unknown_entry_sources))
        if unknown_entry_notes:
            parts.append("unknown entry note class(es): " + ", ".join(unknown_entry_notes))
        raise ReproductionBundleBuildError("; ".join(parts))

    bundle_dir = bundle_path.resolve().parent
    entries: list[ReproductionBundleEntry] = []
    for artifact_class in required_classes:
        artifact_path = artifacts[artifact_class]
        resolved = artifact_path.resolve()
        relative_path = _relative_artifact_path(bundle_dir, resolved)
        payload = _artifact_payload(resolved, artifact_class=artifact_class)
        if strict_shapes:
            shape_error = artifact_shape_error(artifact_class, resolved)
            if shape_error is not None:
                raise ReproductionBundleBuildError(
                    f"invalid artifact evidence for class {artifact_class}: {shape_error}"
                )
        entry_source = dict(source)
        entry_source.update(_source(entry_sources.get(artifact_class, {}), label=f"{artifact_class} source"))
        entries.append(
            ReproductionBundleEntry(
                required_artifact_class=artifact_class,
                path=relative_path,
                sha256=sha256(payload).hexdigest(),
                byte_size=len(payload),
                source=entry_source,
                notes=entry_notes.get(artifact_class),
            )
        )

    return ReproductionBundleDocument(
        schema_version=REPRODUCTION_BUNDLE_SCHEMA_VERSION,
        bundle_id=bundle_id,
        created_at=created_at,
        operator_label=operator_label,
        entries=tuple(entries),
        reproduction_claimed=False,
    )


def reproduction_bundle_document_to_jsonable(document: ReproductionBundleDocument) -> dict[str, object]:
    return {
        "schema_version": document.schema_version,
        "bundle_id": document.bundle_id,
        "created_at": document.created_at,
        "operator_label": document.operator_label,
        "entries": [_entry_to_jsonable(entry) for entry in document.entries],
        "reproduction_claimed": document.reproduction_claimed,
    }


def write_reproduction_bundle(document: ReproductionBundleDocument, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = stable_json_dumps(reproduction_bundle_document_to_jsonable(document)) + "\n"
    path.write_text(payload, encoding="utf-8")


def _entry_to_jsonable(entry: ReproductionBundleEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "required_artifact_class": entry.required_artifact_class,
        "path": entry.path,
        "sha256": entry.sha256,
        "byte_size": entry.byte_size,
        "source": dict(entry.source),
    }
    if entry.notes is not None:
        payload["notes"] = entry.notes
    return payload


def _relative_artifact_path(bundle_dir: Path, artifact_path: Path) -> str:
    try:
        return artifact_path.relative_to(bundle_dir).as_posix()
    except ValueError as exc:
        raise ReproductionBundleBuildError(
            f"artifact path must be inside bundle directory: {artifact_path}"
        ) from exc


def _artifact_payload(path: Path, *, artifact_class: str) -> bytes:
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise ReproductionBundleBuildError(f"missing artifact for class {artifact_class}: {path}") from exc
    except OSError as exc:
        raise ReproductionBundleBuildError(f"could not read artifact for class {artifact_class}: {path}") from exc
    if not payload:
        raise ReproductionBundleBuildError(f"artifact for class {artifact_class} must be non-empty: {path}")
    return payload


def _source(source: Mapping[str, str], *, label: str) -> dict[str, str]:
    unknown = sorted(set(source) - SOURCE_FIELDS)
    if unknown:
        raise ReproductionBundleBuildError(f"{label} has unknown field(s): {', '.join(unknown)}")
    result: dict[str, str] = {}
    for key, value in source.items():
        result[key] = _required_string(value, f"{label}.{key}")
    return result


def _required_string(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReproductionBundleBuildError(f"{label} must be a non-empty string")
    return value
