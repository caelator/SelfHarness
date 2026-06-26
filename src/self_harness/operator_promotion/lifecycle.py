from __future__ import annotations

from pathlib import Path
from typing import cast

from self_harness.operator_promotion.manifest import load_promotion_manifest, save_promotion_manifest
from self_harness.operator_promotion.types import (
    PROMOTION_STATUS_ORDER,
    PROMOTION_STATUSES,
    PromotionEntry,
    PromotionError,
    PromotionManifest,
    PromotionStatus,
)


def set_promotion_status(manifest_path: Path, *, name: str, status: str) -> PromotionManifest:
    manifest = load_promotion_manifest(manifest_path)
    if status not in PROMOTION_STATUSES:
        raise PromotionError(f"unknown promotion status: {status}")
    next_status = cast(PromotionStatus, status)
    entries: list[PromotionEntry] = []
    found = False
    for entry in manifest.entries:
        if entry.name != name:
            entries.append(entry)
            continue
        found = True
        _validate_transition(entry.status, next_status, name=name)
        entries.append(
            PromotionEntry(
                name=entry.name,
                kind=entry.kind,
                path=entry.path,
                sha256=entry.sha256,
                byte_size=entry.byte_size,
                status=next_status,
            )
        )
    if not found:
        raise PromotionError(f"promotion manifest has no entry named: {name}")
    updated = PromotionManifest(
        schema_version=manifest.schema_version,
        entries=tuple(entries),
        boundary=manifest.boundary,
    )
    save_promotion_manifest(updated, manifest_path)
    return updated


def _validate_transition(current: PromotionStatus, next_status: PromotionStatus, *, name: str) -> None:
    if current == next_status:
        return
    if current == "retired":
        raise PromotionError(f"promotion entry is retired and cannot be reactivated: {name}")
    if PROMOTION_STATUS_ORDER[next_status] < PROMOTION_STATUS_ORDER[current]:
        raise PromotionError(
            f"promotion status transition must be monotonic for {name}: {current} -> {next_status}"
        )
