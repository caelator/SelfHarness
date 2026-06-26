from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTPUT_LIMIT = 8192
DEFAULT_TOOL_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ToolResult:
    """The outcome of executing one tool call inside the task workdir."""

    output: str
    is_error: bool


def tool_schemas() -> list[dict[str, Any]]:
    """Anthropic Messages ``tools`` definitions exposed to the agent."""

    return [
        {
            "name": "bash",
            "description": (
                "Run a shell command in the task workspace and return its combined stdout/stderr "
                "and exit code. Use this to inspect files, run programs, and make changes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The shell command to run."}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_file",
            "description": "Read a UTF-8 text file in the workspace by relative path.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the workspace root."}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "write_file",
            "description": "Create or overwrite a UTF-8 text file in the workspace by relative path.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the workspace root."},
                    "content": {"type": "string", "description": "The full file contents to write."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    ]


def execute_tool(
    name: str,
    tool_input: Mapping[str, Any],
    *,
    workdir: Path,
    env: dict[str, str],
    timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> ToolResult:
    """Execute one tool call inside ``workdir``. Model-supplied input is treated as untrusted."""

    if name == "bash":
        return _run_bash(tool_input, workdir=workdir, env=env, timeout_seconds=timeout_seconds)
    if name == "read_file":
        return _read_file(tool_input, workdir=workdir)
    if name == "write_file":
        return _write_file(tool_input, workdir=workdir)
    return ToolResult(output=f"unknown tool: {name}", is_error=True)


def _run_bash(
    tool_input: Mapping[str, Any],
    *,
    workdir: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> ToolResult:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(output="bash requires a non-empty 'command' string", is_error=True)
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(output=f"command timed out after {timeout_seconds}s", is_error=True)
    combined = completed.stdout
    if completed.stderr:
        combined = f"{combined}\n{completed.stderr}" if combined else completed.stderr
    body = _cap(combined)
    return ToolResult(
        output=f"exit_code={completed.returncode}\n{body}".strip(),
        is_error=completed.returncode != 0,
    )


def _read_file(tool_input: Mapping[str, Any], *, workdir: Path) -> ToolResult:
    resolved = _resolve_in_workdir(tool_input.get("path"), workdir)
    if resolved is None:
        return ToolResult(output="read_file requires a 'path' inside the workspace", is_error=True)
    if not resolved.is_file():
        return ToolResult(output=f"no such file: {tool_input.get('path')}", is_error=True)
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(output=f"could not read file: {exc}", is_error=True)
    return ToolResult(output=_cap(text), is_error=False)


def _write_file(tool_input: Mapping[str, Any], *, workdir: Path) -> ToolResult:
    resolved = _resolve_in_workdir(tool_input.get("path"), workdir)
    if resolved is None:
        return ToolResult(output="write_file requires a 'path' inside the workspace", is_error=True)
    content = tool_input.get("content")
    if not isinstance(content, str):
        return ToolResult(output="write_file requires a 'content' string", is_error=True)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult(output=f"could not write file: {exc}", is_error=True)
    return ToolResult(output=f"wrote {len(content)} bytes to {tool_input.get('path')}", is_error=False)


def _resolve_in_workdir(path: Any, workdir: Path) -> Path | None:
    """Resolve a model-supplied path and confine it to the workspace (no traversal escape)."""

    if not isinstance(path, str) or not path.strip():
        return None
    workdir_root = workdir.resolve()
    candidate = (workdir_root / path).resolve()
    if candidate != workdir_root and workdir_root not in candidate.parents:
        return None
    return candidate


def _cap(value: str) -> str:
    if len(value) <= OUTPUT_LIMIT:
        return value
    return value[:OUTPUT_LIMIT] + f"\n... [truncated, {len(value) - OUTPUT_LIMIT} more chars]"
