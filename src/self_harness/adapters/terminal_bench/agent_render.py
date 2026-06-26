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


_SURFACE_HEADINGS = {
    "system_prompt": "Role",
    "bootstrap": "Getting started",
    "execution": "Execution",
    "verification": "Verification",
    "failure_recovery": "Failure recovery",
}


def render_system_prompt(harness: HarnessSpec) -> str:
    """Assemble a single agent system prompt from the editable harness surfaces.

    This is the load-bearing link between the harness and agent behavior: the five instruction
    surfaces are concatenated in a fixed order, followed by the capability surfaces (tools, skills,
    memory sources) and any runtime-policy constraints. Because every promoted harness edit changes
    these surfaces, it deterministically changes the prompt the solving agent receives — which is
    what lets harness edits move real task-success rates.
    """

    sections: list[str] = []
    for surface in INSTRUCTION_SURFACES:
        text = str(getattr(harness, surface)).strip()
        if text:
            sections.append(f"## {_SURFACE_HEADINGS.get(surface, surface)}\n{text}")

    if harness.skills:
        sections.append("## Skills\n" + "\n".join(f"- {skill}" for skill in harness.skills))
    if harness.memory_sources:
        sections.append("## Memory sources\n" + "\n".join(f"- {source}" for source in harness.memory_sources))

    constraints = _runtime_policy_constraints(harness.runtime_policy)
    if constraints:
        sections.append("## Runtime policy\n" + "\n".join(f"- {line}" for line in constraints))

    return "\n\n".join(sections)


def _runtime_policy_constraints(runtime_policy: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if runtime_policy.get("enabled") is True:
        max_messages = runtime_policy.get("max_total_tool_messages")
        if isinstance(max_messages, int) and max_messages > 0:
            lines.append(
                f"Keep tool use bounded: aim to finish within about {max_messages} tool messages; "
                "if you exceed this, stop exploring and commit to a concrete solution."
            )
        max_errors = runtime_policy.get("max_recent_tool_errors")
        if isinstance(max_errors, int) and max_errors > 0:
            lines.append(
                f"After about {max_errors} recent tool errors, change strategy instead of retrying."
            )
        instruction = runtime_policy.get("instruction")
        if isinstance(instruction, str) and instruction.strip():
            lines.append(instruction.strip())
    return lines
