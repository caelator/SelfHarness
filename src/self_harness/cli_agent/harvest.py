"""Harvest failing commands from an interactive session into inbox bundles (the flywheel producer).

When GLM runs a check/build/test command in a coding session and it fails, that's a real, self-validating
failure — exactly what the continuous self-improvement loop learns from. The harvester observes tool
activity, captures failing check commands together with the files the agent has touched, and writes each
as an inbox bundle in the shape ``task_sources.ingest_failing_bundle`` consumes. The already-running loop
drains the inbox; the CLI is purely a producer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from self_harness.adapters.agentic.tools import ToolResult
from self_harness.types import write_stable_json

# Only harvest commands that look like checks/builds/tests/runs — a failing `grep`/`ls` is not a defect to
# learn from, but a failing test/build is. Matched against the first token of the command.
DEFAULT_CHECK_PREFIXES: tuple[str, ...] = (
    "pytest",
    "python",
    "python3",
    "npm",
    "yarn",
    "pnpm",
    "make",
    "cargo",
    "go",
    "ruff",
    "mypy",
    "tox",
    "node",
    "jest",
    "gradle",
    "mvn",
)

_MAX_SNAPSHOT_FILES = 8
_MAX_SNAPSHOT_BYTES = 8192


@dataclass
class FailureHarvester:
    """Observe tool activity, capture failing check commands, and write inbox bundles on flush."""

    inbox_dir: Path
    workdir: Path
    enabled: bool = True
    check_prefixes: tuple[str, ...] = DEFAULT_CHECK_PREFIXES
    _touched: set[str] = field(default_factory=set)
    _candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    _written: list[str] = field(default_factory=list)

    def observe(self, name: str, tool_input: Mapping[str, Any], result: ToolResult) -> None:
        """Tool observer (matches agent_loop.ToolObserver). Tracks touched files; queues failing checks."""

        if not self.enabled:
            return
        if name in {"read_file", "write_file"}:
            path = tool_input.get("path")
            if isinstance(path, str) and path:
                self._touched.add(path)
            return
        if name != "bash" or not result.is_error:
            return
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return
        if not self._is_check_command(command):
            return
        key = hashlib.sha256(command.strip().encode("utf-8")).hexdigest()[:16]
        # Dedupe by command so a retried failing command is harvested once per session.
        self._candidates[key] = {"command": command.strip(), "key": key}

    def _is_check_command(self, command: str) -> bool:
        first = command.strip().split()
        if not first:
            return False
        token = first[0].rsplit("/", 1)[-1]  # handle ./venv/bin/pytest etc.
        return token in self.check_prefixes

    def _snapshot_paths(self, extra: set[str]) -> dict[str, str]:
        """Capture current contents of touched + command-referenced text files, capped count/size."""

        files: dict[str, str] = {}
        for rel in sorted(self._touched | extra):
            if len(files) >= _MAX_SNAPSHOT_FILES:
                break
            target = (self.workdir / rel).resolve()
            try:
                if self.workdir.resolve() not in target.parents and target != self.workdir.resolve():
                    continue
                if not target.is_file():
                    continue
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if len(text.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
                continue
            files[rel] = text
        return files

    @staticmethod
    def _command_file_tokens(command: str) -> set[str]:
        """Heuristically pull workspace-relative file paths named in a command (e.g. test_calc.py)."""

        tokens: set[str] = set()
        for raw in command.replace("'", " ").replace('"', " ").split():
            token = raw.strip()
            # Looks like a local file: has an extension, not a flag, not absolute/parent.
            if "." in token and not token.startswith(("-", "/")) and ".." not in token:
                tokens.add(token)
        return tokens

    def flush(self, *, id_prefix: str) -> list[str]:
        """Write queued failing-check candidates as inbox bundles. Returns the bundle ids written."""

        if not self.enabled or not self._candidates:
            return []
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for key, candidate in self._candidates.items():
            command = candidate["command"]
            # Snapshot files the agent touched plus any file named in the failing command, so the bundle
            # is reproducible by the loop (the named test/script and any files it was editing).
            snapshot = self._snapshot_paths(self._command_file_tokens(command))
            bundle_id = f"{id_prefix}-{key}"
            bundle: dict[str, Any] = {
                "id": bundle_id,
                "command": command,
                "description": f"Harvested from a coding session: `{command}` failed.",
            }
            if snapshot:
                bundle["files"] = snapshot
            target = self.inbox_dir / f"{bundle_id}.json"
            write_stable_json(target, bundle)
            written.append(bundle_id)
        self._written.extend(written)
        self._candidates.clear()
        return written

    @property
    def written_ids(self) -> list[str]:
        return list(self._written)
