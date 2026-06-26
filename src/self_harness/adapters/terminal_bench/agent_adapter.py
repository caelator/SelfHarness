from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from self_harness.adapters.terminal_bench.agent_render import render_agent_config
from self_harness.types import HarnessSpec, write_stable_json


@dataclass(frozen=True)
class HarborAgentInvocation:
    agent_name: str
    config_path: Path | None = None


class AgentAdapter(Protocol):
    def materialize(self, harness: HarnessSpec, workdir: Path) -> HarborAgentInvocation:
        ...


@dataclass(frozen=True)
class ClaudeCodeAgentAdapter:
    agent_name: str = "claude-code"

    def materialize(self, harness: HarnessSpec, workdir: Path) -> HarborAgentInvocation:
        return HarborAgentInvocation(agent_name=self.agent_name)


@dataclass(frozen=True)
class DeepAgentAdapter:
    agent_name: str = "deepagent"
    filename: str = "self-harness-agent-config.json"

    def materialize(self, harness: HarnessSpec, workdir: Path) -> HarborAgentInvocation:
        config_path = workdir / self.filename
        write_stable_json(config_path, render_agent_config(harness))
        return HarborAgentInvocation(agent_name=self.agent_name, config_path=config_path)
