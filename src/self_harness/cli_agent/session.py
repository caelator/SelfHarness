"""Interactive multi-turn session wrapping the agentic loop for free-form coding against a real repo."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from self_harness.adapters.agentic.agent_loop import run_agent_loop
from self_harness.adapters.agentic.runner import DEFAULT_GLM_MODEL
from self_harness.adapters.agentic.tools import DEFAULT_TOOL_TIMEOUT_SECONDS
from self_harness.adapters.llm.messages import AnthropicAgentTransport, MessagesTransport
from self_harness.adapters.terminal_bench.agent_render import render_system_prompt
from self_harness.cli_agent.harvest import FailureHarvester
from self_harness.exceptions import InvalidPatchError
from self_harness.harness import harness_hash, initial_harness, load_harness_spec
from self_harness.types import HarnessSpec

DEFAULT_MAX_STEPS = 24  # higher than eval default: interactive coding turns can be longer.


@dataclass
class TurnResult:
    final_text: str
    steps: int
    tool_calls: int
    stop_reason: str
    usage: dict[str, int]
    harvested: list[str] = field(default_factory=list)
    error: str | None = None
    tool_activity: list[str] = field(default_factory=list)


def load_session_harness(harness_state: Path) -> tuple[HarnessSpec, bool]:
    """Load the persisted/evolving harness, falling back to initial_harness() (Figure 3).

    Mirrors ui._load_persisted_harness so the CLI and console share one evolving harness. Returns
    (spec, evolving) where evolving indicates a persisted lineage was found.
    """

    if harness_state.is_file():
        try:
            value = json.loads(harness_state.read_text(encoding="utf-8"))
            surfaces = value.get("harness") if isinstance(value, dict) else None
            if isinstance(surfaces, dict):
                return load_harness_spec(surfaces), True
        except (OSError, json.JSONDecodeError, InvalidPatchError):
            pass
    return initial_harness(), False


@dataclass
class InteractiveSession:
    """Owns the conversation history, harness, transport, and failure harvester for a coding session.

    Each ``send`` runs the agentic loop against the real ``workdir`` with the persistent ``history`` list,
    so context carries across turns. The system prompt is rendered from the *evolving* harness, so harness
    improvements change how the CLI behaves. Tool failures are observed by the harvester.
    """

    api_key: str
    base_url: str
    workdir: Path
    harness: HarnessSpec
    harvester: FailureHarvester
    model: str = DEFAULT_GLM_MODEL
    max_steps: int = DEFAULT_MAX_STEPS
    tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS
    evolving: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    turn_index: int = 0
    _transport: MessagesTransport | None = None

    def _get_transport(self) -> MessagesTransport:
        if self._transport is None:
            self._transport = AnthropicAgentTransport(
                base_url=self.base_url, api_key=self.api_key, model=self.model
            )
        return self._transport

    @property
    def harness_hash(self) -> str:
        return harness_hash(self.harness)

    def reset(self) -> None:
        self.history.clear()
        self.turn_index = 0

    def send(self, user_text: str) -> TurnResult:
        """Run one user turn through the agentic loop, persisting conversation state and harvesting."""

        self.turn_index += 1
        activity: list[str] = []

        def _observe(name: str, tool_input: Any, result: Any) -> None:
            self.harvester.observe(name, tool_input, result)
            if name == "bash":
                cmd = str(tool_input.get("command", ""))[:80]
                status = "ok" if not result.is_error else "error"
                activity.append(f"bash: {cmd} ({status})")
            elif name in {"read_file", "write_file"}:
                activity.append(f"{name}: {tool_input.get('path', '')}")

        loop = run_agent_loop(
            transport=self._get_transport(),
            system_prompt=render_system_prompt(self.harness),
            task_prompt=user_text,
            workdir=self.workdir,
            env=dict(os.environ),
            max_steps=self.max_steps,
            tool_timeout_seconds=self.tool_timeout_seconds,
            history=self.history,
            on_tool_result=_observe,
        )
        harvested = self.harvester.flush(id_prefix=f"cli-{self.turn_index:03d}")
        return TurnResult(
            final_text=loop.final_text,
            steps=loop.steps,
            tool_calls=loop.tool_calls,
            stop_reason=loop.stop_reason,
            usage=dict(loop.usage),
            harvested=harvested,
            error=loop.error,
            tool_activity=activity,
        )
