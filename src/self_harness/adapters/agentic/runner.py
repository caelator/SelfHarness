from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from self_harness.adapters.agentic.agent_loop import (
    DEFAULT_MAX_STEPS,
    run_agent_loop,
)
from self_harness.adapters.agentic.codex_verifier import CodexVerifier
from self_harness.adapters.agentic.tools import DEFAULT_TOOL_TIMEOUT_SECONDS
from self_harness.adapters.base import TaskAdapter
from self_harness.adapters.llm.messages import AnthropicAgentTransport, MessagesTransport
from self_harness.adapters.terminal_bench.agent_render import render_system_prompt
from self_harness.corpus import TaskCorpus
from self_harness.exceptions import AgenticRunnerError, TaskLoadError
from self_harness.types import (
    FailureCategory,
    HarnessSpec,
    RunRecord,
    Task,
    TraceEvent,
    VerifierOutcome,
)

AGENTIC_MODEL_ID = "glm-5.2-agentic-runner"
DEFAULT_GLM_MODEL = "glm-5.2"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic"

# Task metadata that would let an untrusted corpus smuggle solver/judge configuration is rejected.
DISALLOWED_AGENTIC_METADATA_KEYS = frozenset(
    {
        "api_key",
        "base_url",
        "model",
        "codex_binary",
        "system_prompt",
        "transport",
    }
)


@dataclass(frozen=True)
class CodexJudge:
    """Default verifier protocol marker — anything with this ``judge`` signature works."""

    def judge(
        self, *, success_criteria: str, task_description: str, workdir: Path
    ) -> VerifierOutcome:  # pragma: no cover - interface
        raise NotImplementedError


TransportFactory = Callable[[], MessagesTransport]
Verifier = CodexVerifier | CodexJudge


@dataclass(frozen=True)
class GLMAgenticRunner:
    """Run GLM 5.2 as a tool-using agent under the candidate harness, judged by a real verifier.

    Unlike the deterministic demo runner, this runner actually solves each task: it renders the
    candidate harness into a system prompt, lets the model act in an isolated workspace with real
    tools, then judges success with the Codex CLI. Harness edits therefore change genuine task
    outcomes, so the acceptance gate promotes edits that truly help.

    Outcomes are stochastic, so audits produced by this runner are not byte-reproducible — this is
    real agentic evaluation, not the deterministic demo runner.
    """

    transport_factory: TransportFactory
    verifier: Verifier
    max_steps: int = DEFAULT_MAX_STEPS
    tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS
    keep_workdir: bool = False

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        _reject_disallowed_metadata(task)
        success_criteria = _required_metadata_str(task, "success_criteria")
        workdir = Path(tempfile.mkdtemp(prefix=f"self-harness-agentic-{task.id}-{attempt_index}-"))
        try:
            _copy_template(task.metadata.get("workspace_template"), workdir)
            _materialize_workspace_files(task, workdir)
            env = _merged_env(task.metadata.get("env"))
            system_prompt = render_system_prompt(harness)
            task_prompt = _task_prompt(task)

            trace: list[TraceEvent] = [TraceEvent(kind="workspace", message="created fresh workdir")]
            try:
                loop = run_agent_loop(
                    transport=self.transport_factory(),
                    system_prompt=system_prompt,
                    task_prompt=task_prompt,
                    workdir=workdir,
                    env=env,
                    max_steps=self.max_steps,
                    tool_timeout_seconds=self.tool_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - surface any transport construction failure as a record.
                return _solver_error_record(task, attempt_index, trace, str(exc))

            trace.extend(loop.trace)
            if loop.stop_reason == "model_error":
                return _solver_error_record(task, attempt_index, trace, loop.error or "model error")

            outcome = self.verifier.judge(
                success_criteria=success_criteria,
                task_description=task.description,
                workdir=workdir,
            )
            trace.append(
                TraceEvent(kind="verdict", message=f"judge {'pass' if outcome.passed else 'fail'}")
            )
            return RunRecord(
                task_id=task.id,
                split=task.split,
                passed=outcome.passed,
                trace=trace,
                outcome=outcome,
                attempt_index=attempt_index,
                metadata={
                    "reward_value": 1.0 if outcome.passed else 0.0,
                    "reward_source": outcome.mechanism,
                    "trajectory_event_count": len(loop.trace),
                    "agent_steps": loop.steps,
                    "agent_tool_calls": loop.tool_calls,
                    "solver_token_usage": dict(loop.usage),
                    "agent_stop_reason": loop.stop_reason,
                },
            )
        finally:
            if not self.keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)


@dataclass(frozen=True)
class GLMAgenticTaskAdapter(TaskAdapter):
    """Load a corpus and provide a :class:`GLMAgenticRunner` backed by GLM 5.2 + the Codex judge."""

    api_key: str
    base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    model: str = DEFAULT_GLM_MODEL
    max_steps: int = DEFAULT_MAX_STEPS
    tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS
    codex_binary: str = "codex"
    keep_workdir: bool = False
    transport_factory: TransportFactory | None = field(default=None)
    verifier: Verifier | None = field(default=None)

    def load(self, corpus: TaskCorpus) -> list[Task]:
        return list(corpus.tasks)

    def runner(self) -> GLMAgenticRunner:
        if not self.api_key:
            raise AgenticRunnerError("GLMAgenticTaskAdapter requires a Z.ai API key")
        factory = self.transport_factory or self._default_transport_factory
        verifier = self.verifier or CodexVerifier(binary=self.codex_binary)
        return GLMAgenticRunner(
            transport_factory=factory,
            verifier=verifier,
            max_steps=self.max_steps,
            tool_timeout_seconds=self.tool_timeout_seconds,
            keep_workdir=self.keep_workdir,
        )

    def _default_transport_factory(self) -> MessagesTransport:
        return AnthropicAgentTransport(base_url=self.base_url, api_key=self.api_key, model=self.model)


def _task_prompt(task: Task) -> str:
    instructions = task.metadata.get("instructions")
    detail = instructions if isinstance(instructions, str) and instructions else task.description
    return (
        f"Task: {task.description}\n\n{detail}\n\n"
        "Work in the current directory using the available tools. When you are confident the task "
        "is complete, stop."
    )


def _reject_disallowed_metadata(task: Task) -> None:
    offending = sorted(key for key in task.metadata if key in DISALLOWED_AGENTIC_METADATA_KEYS)
    if offending:
        raise TaskLoadError(
            f"task {task.id} carries disallowed agentic metadata keys: {', '.join(offending)}"
        )


def _required_metadata_str(task: Task, key: str) -> str:
    value = task.metadata.get(key)
    if not isinstance(value, str) or not value:
        raise TaskLoadError(f"task {task.id} missing metadata string: {key}")
    return value


def _materialize_workspace_files(task: Task, workdir: Path) -> None:
    """Seed inline ``workspace_files`` (path -> text content) into the fresh workdir.

    Paths are confined to the workspace; a corpus cannot seed files outside it.
    """

    files = task.metadata.get("workspace_files")
    if files is None:
        return
    if not isinstance(files, dict):
        raise TaskLoadError("workspace_files must be an object of relative-path -> string content")
    workdir_root = workdir.resolve()
    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not isinstance(content, str):
            raise TaskLoadError("workspace_files entries must be string path -> string content")
        target = (workdir_root / rel_path).resolve()
        if target != workdir_root and workdir_root not in target.parents:
            raise TaskLoadError(f"workspace_files path escapes the workspace: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _copy_template(template: object, workdir: Path) -> None:
    if template is None:
        return
    if not isinstance(template, str):
        raise TaskLoadError("workspace_template must be a string path")
    template_path = Path(template)
    if not template_path.is_dir():
        raise TaskLoadError(f"workspace_template must be a directory: {template}")
    for child in template_path.iterdir():
        target = workdir / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def _merged_env(env_overlay: object) -> dict[str, str]:
    env = dict(os.environ)
    if env_overlay is None:
        return env
    if not isinstance(env_overlay, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env_overlay.items()
    ):
        raise TaskLoadError("env metadata must be an object of string keys and values")
    env.update(env_overlay)
    return env


def _solver_error_record(
    task: Task,
    attempt_index: int,
    trace: list[TraceEvent],
    detail: str,
) -> RunRecord:
    return RunRecord(
        task_id=task.id,
        split=task.split,
        passed=False,
        trace=trace,
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value,
            causal_status="environment",
            mechanism="agent-solver-error",
            message=f"agent solver error: {detail}"[:300],
        ),
        attempt_index=attempt_index,
        metadata={"reward_value": 0.0, "reward_source": "agent-solver-error"},
    )


def load_agentic_metadata_keys() -> frozenset[str]:
    return DISALLOWED_AGENTIC_METADATA_KEYS
