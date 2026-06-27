"""Shared construction for GLM 5.2 agentic sessions (real tool-using solver + Codex judge).

Both the CLI (`glm-agentic-demo`) and the web UI build the same objects to run GLM as a real agent:
a `GLMAgenticTaskAdapter`/`GLMAgenticRunner` (solver + Codex verifier) and, optionally, an `LLMProposer`
backed by GLM 5.2 so the same fixed model both solves tasks and proposes edits to its own harness
(the paper's within-model setup). Centralizing it here keeps one wiring path and one set of warnings.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from self_harness.config import EngineConfig
from self_harness.exceptions import AgenticRunnerError
from self_harness.types import ProposalBudget

if TYPE_CHECKING:
    from self_harness.adapters.agentic.runner import GLMAgenticTaskAdapter
    from self_harness.llm_proposer import LLMProposer
    from self_harness.proposer import HeuristicProposer

DEFAULT_ZAI_BASE_URL = "https://api.z.ai/api/anthropic"

# Host-execution disclosure, identical across CLI and UI: the agentic runner executes model-generated
# shell commands directly on this host (only a per-attempt temp workdir + per-command timeout).
HOST_EXEC_WARNING_LINES = (
    "the agentic runner executes model-generated commands on this host (no container).",
    "Run only trusted corpora. This is real agentic evaluation, NOT a Terminal-Bench reproduction.",
)
HOST_EXEC_WARNING = " ".join(HOST_EXEC_WARNING_LINES)


def resolve_zai_api_key(env: Mapping[str, str] | None = None) -> str:
    """Return the Z.ai API key or raise ``AgenticRunnerError`` if it is unset."""

    source = env if env is not None else os.environ
    api_key = source.get("ZAI_API_KEY")
    if not api_key:
        raise AgenticRunnerError("missing ZAI_API_KEY for GLM agentic session")
    return api_key


def resolve_zai_base_url(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return source.get("ZAI_BASE_URL", DEFAULT_ZAI_BASE_URL)


def build_agentic_config(
    *,
    rounds: int,
    seed: int,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
) -> EngineConfig:
    """EngineConfig stamped with the agentic model id + schema version (matches the CLI demo)."""

    from self_harness.adapters.agentic.runner import AGENTIC_MODEL_ID

    return EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(max_proposals=max_proposals, max_payload_bytes=max_payload_bytes),
        model_id=AGENTIC_MODEL_ID,
        schema_version="1.4",
    )


def build_agentic_adapter(
    *,
    api_key: str,
    base_url: str,
    max_steps: int,
    tool_timeout_seconds: int,
    codex_binary: str,
    keep_workdir: bool,
) -> GLMAgenticTaskAdapter:
    """Build the GLM 5.2 solver adapter (Anthropic-style transport + Codex verifier)."""

    from self_harness.adapters.agentic.runner import GLMAgenticTaskAdapter

    return GLMAgenticTaskAdapter(
        api_key=api_key,
        base_url=base_url,
        max_steps=max_steps,
        tool_timeout_seconds=tool_timeout_seconds,
        codex_binary=codex_binary,
        keep_workdir=keep_workdir,
    )


def build_glm_proposer(
    *,
    api_key: str,
    base_url: str,
    on_usage: Callable[[dict[str, int]], None] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> LLMProposer:
    """Build the GLM 5.2 harness-edit proposer over the Z.ai chat-completions transport."""

    from self_harness.adapters.llm.paper_models import GLMClient
    from self_harness.llm_proposer import LLMProposer
    from self_harness.model_backend_preflight import build_zai_transport

    transport = build_zai_transport(base_url=base_url, api_key=api_key)
    return LLMProposer(
        GLMClient(
            transport=transport,
            max_tokens=max_tokens,
            temperature=temperature,
            on_usage=on_usage,
        )
    )


def build_proposer(
    mode: str,
    *,
    api_key: str,
    base_url: str,
    on_usage: Callable[[dict[str, int]], None] | None = None,
) -> HeuristicProposer | LLMProposer:
    """Return a heuristic or GLM proposer by mode (``"heuristic"`` / ``"glm"``)."""

    from self_harness.proposer import HeuristicProposer

    if mode == "heuristic":
        return HeuristicProposer()
    if mode == "glm":
        return build_glm_proposer(api_key=api_key, base_url=base_url, on_usage=on_usage)
    raise ValueError(f"unsupported proposer mode: {mode}")
