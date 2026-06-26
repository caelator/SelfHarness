from __future__ import annotations

from typing import Any

from self_harness.adapters.terminal_bench.provenance import source_hash
from self_harness.types import HarnessSpec

INSTRUCTION_SURFACES = [
    "system_prompt",
    "bootstrap",
    "execution",
    "verification",
    "failure_recovery",
]


def render_agent_config(harness: HarnessSpec) -> dict[str, Any]:
    """Render editable harness surfaces to a Harbor-shaped agent config."""

    config = {
        "adapter": "self-harness-terminal-bench-dry-run",
        "instructions": [
            {"surface": surface, "text": str(getattr(harness, surface))}
            for surface in INSTRUCTION_SURFACES
        ],
        "runtime_policy": dict(harness.runtime_policy),
        "tools": list(harness.tools),
        "skills": list(harness.skills),
        "memory_sources": list(harness.memory_sources),
        "subagents": [dict(item) for item in harness.subagents],
    }
    config["config_hash"] = source_hash(config)
    return config
