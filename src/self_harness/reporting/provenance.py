from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from self_harness.exceptions import PaperFidelityError

INCOMPLETE_PROVENANCE_VALUES = {"", "unknown", "unknown-live", "dry-run", "not-recorded"}


@dataclass(frozen=True)
class BenchmarkProvenance:
    model_id: str
    model_version: str
    decoding_config: dict[str, Any]
    harbor_version: str
    dataset_version: str
    corpus_hash: str
    container_image_digest: str
    task_split_assignment: dict[str, str]
    harbor_artifact_validation_status: str = "candidate"


def provenance_from_manifest(manifest: dict[str, Any]) -> BenchmarkProvenance:
    return BenchmarkProvenance(
        model_id=str(manifest.get("model_id", "")),
        model_version=str(manifest.get("model_version", manifest.get("model_id", ""))),
        decoding_config=_dict_field(manifest.get("decoding_budget")),
        harbor_version=str(manifest.get("harbor_version", "")),
        dataset_version=str(manifest.get("benchmark_dataset_version", manifest.get("benchmark_dataset", ""))),
        corpus_hash=str(manifest.get("corpus_hash", manifest.get("benchmark_dataset_version", ""))),
        container_image_digest=str(manifest.get("container_image_digest", "")),
        task_split_assignment=_dict_of_str(manifest.get("task_split_assignment", {})),
        harbor_artifact_validation_status=str(manifest.get("harbor_artifact_validation_status", "candidate")),
    )


def validate_provenance_completeness(
    provenance: BenchmarkProvenance,
    *,
    reproduction_claimed: bool,
) -> None:
    if not reproduction_claimed:
        return
    missing = [
        field
        for field, value in [
            ("model_id", provenance.model_id),
            ("model_version", provenance.model_version),
            ("harbor_version", provenance.harbor_version),
            ("dataset_version", provenance.dataset_version),
            ("corpus_hash", provenance.corpus_hash),
            ("container_image_digest", provenance.container_image_digest),
        ]
        if value.strip().lower() in INCOMPLETE_PROVENANCE_VALUES
    ]
    if missing:
        raise PaperFidelityError(
            "benchmark reproduction claims require complete provenance: " + ", ".join(sorted(missing))
        )
    if provenance.harbor_artifact_validation_status != "validated":
        raise PaperFidelityError(
            "benchmark reproduction claims require validated Harbor artifacts"
        )


def _dict_field(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_of_str(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
