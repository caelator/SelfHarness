from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from self_harness.exceptions import InvalidConfigError
from self_harness.types import ProposalBudget

DEFAULT_SCHEMA_VERSION = "1.2"
DEFAULT_PROTOCOL_VERSION = "toy-self-harness-v1"
DEFAULT_MODEL_ID = "deterministic-heuristic-proposer"


@dataclass(frozen=True)
class EngineConfig:
    """Runtime configuration for a Self-Harness run."""

    rounds: int = 3
    evaluation_repeats: int = 2
    seed: int = 0
    proposal_budget: ProposalBudget = field(default_factory=ProposalBudget)
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    schema_version: str = DEFAULT_SCHEMA_VERSION
    model_id: str = DEFAULT_MODEL_ID
    fail_on_empty: bool = False
    benchmark_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rounds < 1:
            raise InvalidConfigError("rounds must be at least 1")
        if self.evaluation_repeats < 1:
            raise InvalidConfigError("evaluation_repeats must be at least 1")
        if self.proposal_budget.max_proposals < 1:
            raise InvalidConfigError("max_proposals must be at least 1")
        if self.proposal_budget.max_payload_bytes < 1:
            raise InvalidConfigError("max_payload_bytes must be at least 1")
        if not self.protocol_version:
            raise InvalidConfigError("protocol_version must be non-empty")
        if not self.schema_version:
            raise InvalidConfigError("schema_version must be non-empty")
        if not self.model_id:
            raise InvalidConfigError("model_id must be non-empty")
        if not isinstance(self.benchmark_metadata, dict):
            raise InvalidConfigError("benchmark_metadata must be a dict")
