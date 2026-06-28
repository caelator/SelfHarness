from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from self_harness.adapters.agentic.tools import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    ToolResult,
    execute_tool,
    tool_schemas,
)
from self_harness.adapters.llm.messages import DEFAULT_MAX_TOKENS, MessagesTransport
from self_harness.exceptions import LLMClientError
from self_harness.types import TraceEvent

DEFAULT_MAX_STEPS = 12

# Observer fired after each tool executes (tool name, its input, the result). Lets an interactive caller
# react to tool activity — e.g. harvest failing commands — without the loop knowing about that concern.
ToolObserver = Callable[[str, Mapping[str, Any], ToolResult], None]

# Fired right BEFORE a tool executes (tool name, its input). Lets an interactive caller show "running X…"
# feedback during what is often the longest, silent part of a turn (e.g. a multi-minute `cargo test`).
ToolStarter = Callable[[str, Mapping[str, Any]], None]


@dataclass
class AgentLoopResult:
    """The outcome of running the agentic loop for one task attempt."""

    stop_reason: str
    steps: int
    tool_calls: int
    final_text: str
    trace: list[TraceEvent] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def run_agent_loop(
    *,
    transport: MessagesTransport,
    system_prompt: str,
    task_prompt: str,
    workdir: Path,
    env: dict[str, str],
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
    history: list[dict[str, Any]] | None = None,
    on_tool_result: ToolObserver | None = None,
    on_tool_start: ToolStarter | None = None,
    on_model_request: Callable[[int], None] | None = None,
) -> AgentLoopResult:
    """Drive a tool-calling agent until it stops, hits the step budget, or errors.

    The model acts in ``workdir`` using the bash/read_file/write_file tools. Every model turn and
    tool execution is recorded as a stable :class:`TraceEvent` so downstream clustering has
    low-cardinality, informative symptoms. The loop never raises on tool errors — those are fed back
    to the model as ``tool_result`` blocks with ``is_error`` so it can recover.

    For single-shot eval use, omit ``history``: the loop builds its own message list from ``task_prompt``
    and discards it on return (unchanged behaviour). For interactive/multi-turn use, pass a ``history``
    list: the loop seeds from it, appends the new user turn plus all assistant/tool turns to it in place,
    and the caller retains full conversation state across calls. ``on_tool_result`` fires after each tool
    runs so an interactive caller can observe activity (e.g. harvest failing commands).
    """

    tools = tool_schemas()
    messages: list[dict[str, Any]] = history if history is not None else []
    messages.append({"role": "user", "content": task_prompt})
    trace: list[TraceEvent] = []
    usage_totals: dict[str, int] = {}
    tool_calls = 0

    for step in range(max_steps):
        if on_model_request is not None:
            on_model_request(step)
        try:
            turn = transport.create_message(
                system=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )
        except LLMClientError as exc:
            trace.append(TraceEvent(kind="model_error", message="model request failed"))
            return AgentLoopResult(
                stop_reason="model_error",
                steps=step,
                tool_calls=tool_calls,
                final_text="",
                trace=trace,
                usage=usage_totals,
                error=str(exc),
            )

        _accumulate(usage_totals, turn.usage)
        text = turn.text().strip()
        tool_uses = turn.tool_uses()
        trace.append(
            TraceEvent(
                kind="model_turn",
                message=f"model turn stop_reason={turn.stop_reason} tool_calls={len(tool_uses)}",
            )
        )

        # Record the assistant turn verbatim so tool_use ids round-trip correctly.
        messages.append({"role": "assistant", "content": turn.content})

        if turn.stop_reason != "tool_use" or not tool_uses:
            return AgentLoopResult(
                stop_reason=turn.stop_reason,
                steps=step + 1,
                tool_calls=tool_calls,
                final_text=text,
                trace=trace,
                usage=usage_totals,
            )

        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            tool_calls += 1
            name = str(tool_use.get("name", ""))
            tool_input = tool_use.get("input")
            tool_input_map: Mapping[str, Any] = tool_input if isinstance(tool_input, Mapping) else {}
            if on_tool_start is not None:
                on_tool_start(name, tool_input_map)
            result = execute_tool(
                name,
                tool_input_map,
                workdir=workdir,
                env=env,
                timeout_seconds=tool_timeout_seconds,
            )
            trace.append(
                TraceEvent(
                    kind="tool_call",
                    message=f"tool {name} {'error' if result.is_error else 'ok'}",
                )
            )
            if on_tool_result is not None:
                on_tool_result(name, tool_input_map, result)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.get("id"),
                    "content": result.output,
                    "is_error": result.is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    trace.append(TraceEvent(kind="budget", message="reached max agent steps"))
    return AgentLoopResult(
        stop_reason="max_steps",
        steps=max_steps,
        tool_calls=tool_calls,
        final_text="",
        trace=trace,
        usage=usage_totals,
    )


def _accumulate(totals: dict[str, int], counts: Mapping[str, int]) -> None:
    for key, value in counts.items():
        if isinstance(value, int):
            totals[key] = totals.get(key, 0) + value
