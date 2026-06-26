import pytest

from self_harness.config import EngineConfig
from self_harness.exceptions import InvalidConfigError
from self_harness.types import ProposalBudget


def test_engine_config_validates_positive_values() -> None:
    with pytest.raises(InvalidConfigError):
        EngineConfig(rounds=0)
    with pytest.raises(InvalidConfigError):
        EngineConfig(evaluation_repeats=0)
    with pytest.raises(InvalidConfigError):
        EngineConfig(proposal_budget=ProposalBudget(max_proposals=0))
    with pytest.raises(InvalidConfigError):
        EngineConfig(proposal_budget=ProposalBudget(max_payload_bytes=0))


def test_engine_config_defaults_are_production_metadata() -> None:
    config = EngineConfig()

    assert config.rounds == 3
    assert config.evaluation_repeats == 2
    assert config.schema_version == "1.2"
    assert config.protocol_version == "toy-self-harness-v1"
