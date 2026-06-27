from __future__ import annotations

import pytest

from self_harness.agentic_session import (
    DEFAULT_ZAI_BASE_URL,
    HOST_EXEC_WARNING,
    build_agentic_config,
    build_proposer,
    resolve_zai_api_key,
    resolve_zai_base_url,
)
from self_harness.exceptions import AgenticRunnerError
from self_harness.proposer import HeuristicProposer


def test_resolve_zai_api_key_requires_credentials() -> None:
    with pytest.raises(AgenticRunnerError, match="ZAI_API_KEY"):
        resolve_zai_api_key(env={})
    assert resolve_zai_api_key(env={"ZAI_API_KEY": "k"}) == "k"


def test_resolve_zai_base_url_defaults() -> None:
    assert resolve_zai_base_url(env={}) == DEFAULT_ZAI_BASE_URL
    assert resolve_zai_base_url(env={"ZAI_BASE_URL": "https://example/anthropic"}) == "https://example/anthropic"


def test_build_agentic_config_stamps_agentic_identity() -> None:
    config = build_agentic_config(rounds=2, seed=1, evaluation_repeats=1, max_proposals=4, max_payload_bytes=600)
    assert config.rounds == 2
    assert config.schema_version == "1.4"
    assert config.model_id == "glm-5.2-agentic-runner"


def test_build_proposer_heuristic_needs_no_network() -> None:
    proposer = build_proposer("heuristic", api_key="unused", base_url=DEFAULT_ZAI_BASE_URL)
    assert isinstance(proposer, HeuristicProposer)


def test_build_proposer_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unsupported proposer mode"):
        build_proposer("nope", api_key="k", base_url=DEFAULT_ZAI_BASE_URL)


def test_host_exec_warning_mentions_host_and_non_reproduction() -> None:
    assert "host" in HOST_EXEC_WARNING
    assert "NOT a Terminal-Bench reproduction" in HOST_EXEC_WARNING
