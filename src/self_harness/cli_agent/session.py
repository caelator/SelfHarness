"""Interactive multi-turn session wrapping the agentic loop for free-form coding against a real repo."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from self_harness.adapters.agentic.agent_loop import run_agent_loop
from self_harness.adapters.agentic.runner import DEFAULT_GLM_MODEL
from self_harness.adapters.agentic.tools import DEFAULT_TOOL_TIMEOUT_SECONDS, ToolResult
from self_harness.adapters.llm.messages import (
    AnthropicAgentTransport,
    MessagesTransport,
    StreamingAnthropicAgentTransport,
)
from self_harness.adapters.terminal_bench.agent_render import render_system_prompt
from self_harness.cli_agent.effort import valid_effort_or_none
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


class _EventSummary(TypedDict):
    steps: int
    tool_calls: int
    usage: dict[str, int]


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

    def _streaming_transport(
        self,
        on_text_delta: Callable[[str], None] | None,
        on_tool_start: Callable[[str], None] | None,
    ) -> MessagesTransport:
        # Streaming transports carry per-turn callbacks, so build a fresh one each turn rather than cache.
        return StreamingAnthropicAgentTransport(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            on_text_delta=on_text_delta,
            on_tool_start=on_tool_start,
        )

    @property
    def harness_hash(self) -> str:
        return harness_hash(self.harness)

    def reset(self) -> None:
        self.history.clear()
        self.turn_index = 0

    def send(
        self,
        user_text: str,
        *,
        on_text_delta: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str], None] | None = None,
        on_tool_event: Callable[[str, str, bool], None] | None = None,
        on_tool_starting: Callable[[str, str], None] | None = None,
        on_model_request: Callable[[int], None] | None = None,
    ) -> TurnResult:
        """Run one user turn through the agentic loop, persisting conversation state and harvesting.

        When ``on_text_delta`` is provided, a streaming transport is used so the reply arrives token by
        token (interactive UI); otherwise the blocking transport is used (tests, piped/non-tty). The
        ``on_tool_event(name, summary, ok)`` callback fires as each tool completes; ``on_tool_starting``
        fires just before a tool runs (for "running X…" feedback); ``on_model_request(step)`` fires before
        each model call. All are in addition to the always-recorded ``tool_activity`` list.
        """

        self.turn_index += 1
        activity: list[str] = []

        def _summarize(name: str, tool_input: Any) -> str:
            if name == "bash":
                return f"$ {str(tool_input.get('command', ''))[:80]}"
            if name in {"read_file", "write_file"}:
                return str(tool_input.get("path", ""))
            return name

        def _observe(name: str, tool_input: Any, result: Any) -> None:
            self.harvester.observe(name, tool_input, result)
            ok = not result.is_error
            summary = _summarize(name, tool_input)
            if name == "bash":
                activity.append(f"bash: {summary[2:]} ({'ok' if ok else 'error'})")
            elif name in {"read_file", "write_file"}:
                activity.append(f"{name}: {summary}")
            else:
                activity.append(name)
            if on_tool_event is not None:
                on_tool_event(name, summary, ok)

        def _starting(name: str, tool_input: Any) -> None:
            if on_tool_starting is not None:
                on_tool_starting(name, _summarize(name, tool_input))

        if on_text_delta is not None:
            transport: MessagesTransport = self._streaming_transport(on_text_delta, on_tool_start)
        else:
            transport = self._get_transport()

        loop = run_agent_loop(
            transport=transport,
            system_prompt=_interactive_system_prompt(self.harness, self.model),
            task_prompt=user_text,
            workdir=self.workdir,
            env=dict(os.environ),
            max_steps=self.max_steps,
            tool_timeout_seconds=self.tool_timeout_seconds,
            history=self.history,
            on_tool_result=_observe,
            on_tool_start=_starting if on_tool_starting is not None else None,
            on_model_request=on_model_request,
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


HEADLESS_CLI_BACKENDS = frozenset({"codex", "agy", "claude"})


@dataclass
class HeadlessCliSession:
    """A ``self-harness code`` session backed by a local headless coding CLI.

    The external CLI owns its internal tool loop, so SelfHarness passes the active harness plus
    conversation history in the stdin prompt and records the final response back into the regular
    session history. Codex's JSON event stream is also mirrored into the failure harvester when command
    details are present; Agy and Claude currently expose only final text in this headless path.
    """

    backend: str
    binary: str
    workdir: Path
    harness: HarnessSpec
    harvester: FailureHarvester
    model: str | None = None
    effort: str | None = None
    max_steps: int = DEFAULT_MAX_STEPS
    tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS
    evolving: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    turn_index: int = 0

    @property
    def harness_hash(self) -> str:
        return harness_hash(self.harness)

    def reset(self) -> None:
        self.history.clear()
        self.turn_index = 0

    def send(
        self,
        user_text: str,
        *,
        on_text_delta: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str], None] | None = None,
        on_tool_event: Callable[[str, str, bool], None] | None = None,
        on_tool_starting: Callable[[str, str], None] | None = None,
        on_model_request: Callable[[int], None] | None = None,
    ) -> TurnResult:
        del on_tool_start  # Headless CLIs do not expose SelfHarness's native text/tool callbacks here.
        self.turn_index += 1
        if on_model_request is not None:
            on_model_request(0)

        backend = _normalize_headless_backend(self.backend)
        activity: list[str] = []
        timeout_seconds = max(30, int(self.tool_timeout_seconds) * max(1, int(self.max_steps)))
        with tempfile.TemporaryDirectory(prefix=f"self-harness-{backend}-") as tmp:
            last_message_path = Path(tmp) / "last-message.txt"
            command = _headless_command(
                backend=backend,
                binary=self.binary,
                workdir=self.workdir,
                last_message_path=last_message_path,
                timeout_seconds=timeout_seconds,
                model=self.model,
                effort=self.effort,
            )
            if on_tool_starting is not None:
                on_tool_starting(backend, f"$ {self.binary} {_headless_command_label(backend)}")
            try:
                completed = subprocess.run(
                    command,
                    input=_headless_prompt(
                        self.harness,
                        self.history,
                        user_text,
                        backend=backend,
                        model=self.model,
                        effort=self.effort,
                        binary=self.binary,
                    ),
                    cwd=self.workdir,
                    env=dict(os.environ),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except FileNotFoundError:
                return TurnResult(
                    final_text="",
                    steps=1,
                    tool_calls=0,
                    stop_reason="model_error",
                    usage={},
                    error=f"{backend} binary not found: {self.binary}",
                )
            except subprocess.TimeoutExpired:
                return TurnResult(
                    final_text="",
                    steps=1,
                    tool_calls=0,
                    stop_reason="model_error",
                    usage={},
                    error=f"{backend} headless run timed out after {timeout_seconds}s",
                )

            event_summary: _EventSummary = {"steps": 1, "tool_calls": 0, "usage": {}}
            if backend == "codex":
                event_summary = _observe_codex_events(
                    completed.stdout,
                    harvester=self.harvester,
                    activity=activity,
                    on_tool_event=on_tool_event,
                )
            final_text = _read_headless_output(last_message_path, completed.stdout, backend=backend)
            if on_text_delta is not None and final_text:
                on_text_delta(final_text)
            harvested = self.harvester.flush(id_prefix=f"cli-{self.turn_index:03d}")

            if completed.returncode != 0:
                return TurnResult(
                    final_text=final_text,
                    steps=max(1, event_summary["steps"]),
                    tool_calls=event_summary["tool_calls"],
                    stop_reason="model_error",
                    usage=event_summary["usage"],
                    harvested=harvested,
                    error=_format_headless_error(completed, final_text, backend=backend),
                    tool_activity=activity,
                )

        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": final_text})
        return TurnResult(
            final_text=final_text,
            steps=max(1, event_summary["steps"]),
            tool_calls=event_summary["tool_calls"],
            stop_reason="end_turn",
            usage=event_summary["usage"],
            harvested=harvested,
            tool_activity=activity,
        )


CodexCliSession = HeadlessCliSession


def _normalize_headless_backend(value: str) -> str:
    backend = value.strip().lower().replace("_", "-")
    if backend.endswith("-cli"):
        backend = backend[:-4]
    if backend == "claude-code":
        backend = "claude"
    if backend not in HEADLESS_CLI_BACKENDS:
        raise ValueError(f"unsupported headless CLI backend: {value}")
    return backend


def headless_binary_for_backend(backend: str) -> str:
    normalized = _normalize_headless_backend(backend)
    specific = os.environ.get(f"SELF_HARNESS_{normalized.upper()}_BINARY")
    if specific:
        return specific
    generic = os.environ.get("SELF_HARNESS_HEADLESS_BINARY")
    if generic:
        return generic
    return normalized


def _headless_command(
    *,
    backend: str,
    binary: str,
    workdir: Path,
    last_message_path: Path,
    timeout_seconds: int,
    model: str | None = None,
    effort: str | None = None,
) -> list[str]:
    model = model or _headless_model_override(backend)
    effort = effort or _headless_effort_override(backend)
    effort = valid_effort_or_none(backend, effort)
    if backend == "codex":
        command = [
            binary,
            "exec",
            "--cd",
            str(workdir),
            "--skip-git-repo-check",
            "--ephemeral",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--json",
            "--output-last-message",
            str(last_message_path),
        ]
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.append("-")
        return command
    if backend == "agy":
        command = [
            binary,
            "--print",
            "--dangerously-skip-permissions",
            "--print-timeout",
            f"{timeout_seconds}s",
        ]
        if model:
            command.extend(["--model", model])
        return command
    if backend == "claude":
        command = [
            binary,
            "--print",
            "--bare",
            "--dangerously-skip-permissions",
            "--output-format",
            "text",
            "--input-format",
            "text",
            "--no-session-persistence",
        ]
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        return command
    raise ValueError(f"unsupported headless CLI backend: {backend}")


def _headless_command_label(backend: str) -> str:
    if backend == "codex":
        return "exec"
    if backend == "agy":
        return "--print"
    if backend == "claude":
        return "--print"
    return "run"


def _headless_model_override(backend: str) -> str | None:
    specific = os.environ.get(f"SELF_HARNESS_{backend.upper()}_MODEL")
    if specific:
        return specific
    return os.environ.get("SELF_HARNESS_HEADLESS_MODEL")


def _headless_effort_override(backend: str) -> str | None:
    specific = os.environ.get(f"SELF_HARNESS_{backend.upper()}_EFFORT")
    if specific:
        return specific
    return os.environ.get("SELF_HARNESS_HEADLESS_EFFORT")


def _interactive_system_prompt(harness: HarnessSpec, model: str) -> str:
    return "\n\n".join(
        [
            _runtime_identity_instructions(provider="glm", model=model),
            render_system_prompt(harness),
        ]
    )


def _runtime_identity_instructions(
    *,
    provider: str,
    model: str | None,
    effort: str | None = None,
    binary: str | None = None,
) -> str:
    provider_label = {
        "glm": "GLM via Z.ai",
        "codex": "Codex headless CLI",
        "agy": "Agy headless CLI",
        "claude": "Claude headless CLI",
    }.get(provider, provider)
    model_text = model or (DEFAULT_GLM_MODEL if provider == "glm" else "provider default")
    effort_text = effort or "provider default"
    binary_line = f"\n- Headless binary: {binary}" if binary else ""
    return (
        "Runtime identity:\n"
        f"- SelfHarness Code provider: {provider_label}.\n"
        f"- Configured model id: {model_text}.\n"
        f"- Configured reasoning effort: {effort_text}."
        f"{binary_line}\n"
        "- If the user asks what model, provider, or backend is being used, answer from this runtime "
        "identity. Do not infer identity from the API protocol or compatibility layer."
    )


def _headless_prompt(
    harness: HarnessSpec,
    history: list[dict[str, Any]],
    user_text: str,
    *,
    backend: str,
    model: str | None,
    effort: str | None,
    binary: str,
) -> str:
    parts = [
        "You are running as the SelfHarness coding agent in the current working directory.",
        _runtime_identity_instructions(provider=backend, model=model, effort=effort, binary=binary),
        "Follow this active harness exactly:",
        render_system_prompt(harness),
    ]
    prior = _history_text(history)
    if prior:
        parts.extend(["Conversation so far:", prior])
    parts.extend(["Current user request:", user_text])
    return "\n\n".join(parts)


def _history_text(history: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in history[-12:]:
        role = str(msg.get("role", "message"))
        content = msg.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _read_headless_output(path: Path, stdout: str, *, backend: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text.strip():
        return text.strip()
    if backend != "codex":
        return stdout.strip()
    last_assistant = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = _extract_text(event)
        if candidate:
            last_assistant = candidate
    return last_assistant.strip()


def _observe_codex_events(
    stdout: str,
    *,
    harvester: FailureHarvester,
    activity: list[str],
    on_tool_event: Callable[[str, str, bool], None] | None,
) -> _EventSummary:
    usage: dict[str, int] = {}
    pending_commands: dict[str, str] = {}
    steps = 0
    tool_calls = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        steps += 1
        _merge_usage(usage, event.get("usage"))
        item = event.get("item") if isinstance(event.get("item"), dict) else event
        command = _extract_command(item)
        item_id = str(item.get("id") or event.get("id") or len(pending_commands))
        if command and str(event.get("type", "")).endswith("started"):
            pending_commands[item_id] = command
            continue
        if command:
            pending_commands[item_id] = command

        completed_command = pending_commands.pop(item_id, command or "")
        if completed_command and _is_completed_event(event):
            output = _extract_output(item)
            exit_code = _extract_exit_code(item)
            is_error = bool(exit_code not in (None, 0))
            tool_calls += 1
            summary = f"$ {completed_command[:80]}"
            activity.append(f"bash: {completed_command[:80]} ({'error' if is_error else 'ok'})")
            harvester.observe(
                "bash",
                {"command": completed_command},
                ToolResult(output=output or f"exit_code={exit_code}", is_error=is_error),
            )
            if on_tool_event is not None:
                on_tool_event("bash", summary, not is_error)
    return {"steps": steps, "tool_calls": tool_calls, "usage": usage}


def _merge_usage(target: dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, int):
            target[key] = target.get(key, 0) + value


def _extract_text(event: dict[str, Any]) -> str:
    raw_item = event.get("item")
    item = raw_item if isinstance(raw_item, dict) else event
    for key in ("text", "message", "content", "final_message"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_command(item: dict[str, Any]) -> str:
    for key in ("command", "cmd"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    args = item.get("args")
    if isinstance(args, list) and all(isinstance(part, str) for part in args):
        return " ".join(args)
    return ""


def _extract_output(item: dict[str, Any]) -> str:
    for key in ("aggregated_output", "output", "stdout", "stderr"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_exit_code(item: dict[str, Any]) -> int | None:
    for key in ("exit_code", "exitCode", "returncode", "return_code"):
        value = item.get(key)
        if isinstance(value, int):
            return value
    return None


def _is_completed_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type", ""))
    return event_type.endswith("completed") or event_type.endswith("finished")


def _format_headless_error(
    completed: subprocess.CompletedProcess[str], final_text: str, *, backend: str
) -> str:
    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    detail = stderr or stdout or final_text or f"{backend} headless run failed"
    return f"{backend} headless run exited {completed.returncode}: {detail[-1200:]}"
