from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from self_harness.adapters.base import TaskAdapter
from self_harness.corpus import TaskCorpus, load_corpus
from self_harness.exceptions import TaskLoadError
from self_harness.types import FailureCategory, HarnessSpec, RunRecord, Task, TraceEvent, VerifierOutcome

SNIPPET_LIMIT = 4096
DEFAULT_TIMEOUT_SECONDS = 30


def load_tasks_json(path: Path) -> list[Task]:
    return load_corpus(path, allow_legacy=True).tasks


@dataclass(frozen=True)
class LocalSubprocessTaskAdapter(TaskAdapter):
    keep_workdir: bool = False

    def load(self, corpus: TaskCorpus) -> list[Task]:
        return list(corpus.tasks)

    def runner(self) -> LocalSubprocessRunner:
        return LocalSubprocessRunner(keep_workdir=self.keep_workdir)


@dataclass(frozen=True)
class LocalSubprocessRunner:
    """Run metadata-defined local commands in a fresh workspace per attempt."""

    keep_workdir: bool = False

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        workdir = Path(tempfile.mkdtemp(prefix=f"self-harness-{task.id}-{attempt_index}-"))
        try:
            _copy_template(task.metadata.get("workspace_template"), workdir)
            timeout = _timeout_seconds(task, harness)
            env = _merged_env(task.metadata.get("env"))
            solve_command = _required_metadata_str(task, "solve_command")
            verify_command = _required_metadata_str(task, "verify_command")

            trace: list[TraceEvent] = [
                TraceEvent(kind="workspace", message="created fresh workdir", metadata={"workdir": str(workdir)})
            ]
            solve = _run_command(solve_command, workdir, timeout, env)
            trace.append(_event_from_result("solve", solve_command, solve, workdir))
            if solve.timed_out:
                return _timeout_record(task, attempt_index, trace, "solve command timed out")

            verify = _run_command(verify_command, workdir, timeout, env)
            trace.append(_event_from_result("verify", verify_command, verify, workdir))
            if verify.timed_out:
                return _timeout_record(task, attempt_index, trace, "verify command timed out")

            passed = verify.returncode == 0
            classification = _classify_verify_result(verify_command, verify)
            outcome = VerifierOutcome(
                passed=passed,
                terminal_cause=FailureCategory.VERIFIER_PASS.value if passed else classification.category.value,
                causal_status="confirmed" if passed else "rejected",
                mechanism="subprocess-exit-zero" if passed else classification.mechanism,
                message="verifier exited 0" if passed else _failure_message(verify, classification),
            )
            return RunRecord(
                task_id=task.id,
                split=task.split,
                passed=passed,
                trace=trace,
                outcome=outcome,
                attempt_index=attempt_index,
            )
        finally:
            if not self.keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class _FailureClassification:
    category: FailureCategory
    mechanism: str


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


def _timeout_seconds(task: Task, harness: HarnessSpec) -> int:
    metadata_timeout = task.metadata.get("timeout_seconds")
    if metadata_timeout is not None:
        if not isinstance(metadata_timeout, int) or metadata_timeout < 1:
            raise TaskLoadError("timeout_seconds must be a positive integer")
        return metadata_timeout
    for key in ("verify_timeout_override", "solve_timeout_override"):
        value = harness.runtime_policy.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return DEFAULT_TIMEOUT_SECONDS


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


def _required_metadata_str(task: Task, key: str) -> str:
    value = task.metadata.get(key)
    if not isinstance(value, str) or not value:
        raise TaskLoadError(f"task {task.id} missing metadata string: {key}")
    return value


def _run_command(command: str, workdir: Path, timeout: int, env: dict[str, str]) -> _CommandResult:
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _CommandResult(
            returncode=124,
            stdout=_coerce_output(exc.stdout),
            stderr=_coerce_output(exc.stderr),
            timed_out=True,
        )
    return _CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _event_from_result(kind: str, command: str, result: _CommandResult, workdir: Path) -> TraceEvent:
    return TraceEvent(
        kind=kind,
        message=f"{kind} command exited {result.returncode}",
        metadata={
            "command": command,
            "returncode": result.returncode,
            "stdout": _snippet(result.stdout),
            "stderr": _snippet(result.stderr),
            "timed_out": result.timed_out,
            "workdir": str(workdir),
        },
    )


def _timeout_record(task: Task, attempt_index: int, trace: list[TraceEvent], message: str) -> RunRecord:
    return RunRecord(
        task_id=task.id,
        split=task.split,
        passed=False,
        trace=trace,
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.TIMEOUT.value,
            causal_status="environment",
            mechanism="solve-or-verify-timeout",
            message=message,
        ),
        attempt_index=attempt_index,
    )


def _classify_verify_result(command: str, result: _CommandResult) -> _FailureClassification:
    signal = f"{command}\n{result.stdout}\n{result.stderr}".lower()
    if _looks_like_environment_error(result, signal):
        return _FailureClassification(FailureCategory.ENVIRONMENT_ERROR, "command-environment-error")
    if _looks_like_missing_artifact(command, signal):
        return _FailureClassification(FailureCategory.MISSING_ARTIFACT, "missing-artifact-check")
    if _looks_like_assertion_failure(signal):
        return _FailureClassification(FailureCategory.ASSERTION_FAIL, "assertion-output")
    return _FailureClassification(FailureCategory.VERIFIER_FAIL, "nonzero-exit")


def _looks_like_environment_error(result: _CommandResult, signal: str) -> bool:
    return result.returncode in {126, 127} or any(
        token in signal
        for token in [
            "command not found",
            "not found",
            "permission denied",
            "no such command",
        ]
    )


def _looks_like_missing_artifact(command: str, signal: str) -> bool:
    normalized = command.strip()
    return (
        normalized.startswith("test -f ")
        or normalized.startswith("[ -f ")
        or "no such file" in signal
        or "missing file" in signal
        or "missing artifact" in signal
    )


def _looks_like_assertion_failure(signal: str) -> bool:
    return any(
        token in signal
        for token in [
            "assertionerror",
            "assert ",
            "expected",
            "actual",
            "pytest",
            "unittest",
            "traceback",
        ]
    )


def _failure_message(result: _CommandResult, classification: _FailureClassification) -> str:
    snippets = [part for part in [_snippet(result.stdout), _snippet(result.stderr)] if part]
    suffix = f": {' | '.join(snippets)}" if snippets else ""
    return f"{classification.category.value}: verifier exited {result.returncode}{suffix}"


def _snippet(value: str) -> str:
    return value[:SNIPPET_LIMIT]


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
