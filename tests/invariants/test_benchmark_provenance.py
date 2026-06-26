import pytest

from self_harness.exceptions import PaperFidelityError
from self_harness.reporting.provenance import BenchmarkProvenance, validate_provenance_completeness


def test_reproduction_claim_rejects_unknown_live_provenance() -> None:
    provenance = BenchmarkProvenance(
        model_id="harbor-live-runner",
        model_version="anthropic/claude-opus-4-1",
        decoding_config={},
        harbor_version="unknown-live",
        dataset_version="terminal-bench@2.0",
        corpus_hash="terminal-bench@2.0",
        container_image_digest="unknown-live",
        task_split_assignment={},
    )

    with pytest.raises(PaperFidelityError):
        validate_provenance_completeness(provenance, reproduction_claimed=True)


def test_incomplete_provenance_is_allowed_without_reproduction_claim() -> None:
    provenance = BenchmarkProvenance(
        model_id="harbor-live-runner",
        model_version="anthropic/claude-opus-4-1",
        decoding_config={},
        harbor_version="unknown-live",
        dataset_version="terminal-bench@2.0",
        corpus_hash="terminal-bench@2.0",
        container_image_digest="unknown-live",
        task_split_assignment={},
    )

    validate_provenance_completeness(provenance, reproduction_claimed=False)


def test_reproduction_claim_rejects_candidate_artifact_status() -> None:
    provenance = BenchmarkProvenance(
        model_id="harbor-live-runner",
        model_version="anthropic/claude-opus-4-1",
        decoding_config={},
        harbor_version="harbor 1.0",
        dataset_version="terminal-bench@2.0",
        corpus_hash="sha256:corpus",
        container_image_digest="sha256:container",
        task_split_assignment={},
        harbor_artifact_validation_status="candidate",
    )

    with pytest.raises(PaperFidelityError):
        validate_provenance_completeness(provenance, reproduction_claimed=True)


def test_reproduction_claim_allows_validated_complete_provenance() -> None:
    provenance = BenchmarkProvenance(
        model_id="harbor-live-runner",
        model_version="anthropic/claude-opus-4-1",
        decoding_config={},
        harbor_version="harbor 1.0",
        dataset_version="terminal-bench@2.0",
        corpus_hash="sha256:corpus",
        container_image_digest="sha256:container",
        task_split_assignment={},
        harbor_artifact_validation_status="validated",
    )

    validate_provenance_completeness(provenance, reproduction_claimed=True)
