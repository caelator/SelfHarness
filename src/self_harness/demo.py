from __future__ import annotations

from dataclasses import dataclass

from self_harness.types import HarnessSpec, RunRecord, Split, Task, TraceEvent, VerifierOutcome


def demo_tasks() -> list[Task]:
    return [
        Task(
            id="missing_artifact_short",
            split=Split.HELD_IN,
            failure_mode="missing_artifact",
            description="Create the required report file before finishing.",
        ),
        Task(
            id="repeated_failed_command",
            split=Split.HELD_IN,
            failure_mode="repeated_failed_command",
            description="Recover from a failing command without repeating it.",
        ),
        Task(
            id="late_verification",
            split=Split.HELD_IN,
            failure_mode="late_verification",
            description="Verify the candidate answer early enough to recover.",
        ),
        Task(
            id="environment_persistence",
            split=Split.HELD_IN,
            failure_mode="environment_persistence",
            description="Persist an environment update across command boundaries.",
        ),
        Task(
            id="long_context_overprompting",
            split=Split.HELD_OUT,
            failure_mode="cross_split_tension",
            description="Plan before writing because premature artifact creation is harmful.",
        ),
    ]


@dataclass(frozen=True)
class ToyRunner:
    """A deterministic runner that makes the validation gate observable."""

    seed: int = 0

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        del self
        if task.failure_mode == "missing_artifact":
            passed = _has_targeted_artifact_rule(harness) or _has_broad_artifact_rule(harness)
            return _record(
                task,
                attempt_index,
                passed,
                "missing_required_artifact",
                "missing_artifact",
                "Verifier expected a named output file, but no artifact was produced.",
                "agent finished without creating the requested file",
            )
        if task.failure_mode == "repeated_failed_command":
            passed = "do not repeat the exact failed command" in harness.failure_recovery
            return _record(
                task,
                attempt_index,
                passed,
                "repeated_failed_command",
                "repeated_failed_command",
                "Verifier saw the same failing command repeated without a changed strategy.",
                "agent retried the same failed command",
            )
        if task.failure_mode == "late_verification":
            passed = "as soon as a candidate artifact or fix exists" in harness.verification
            return _record(
                task,
                attempt_index,
                passed,
                "verification_too_late",
                "late_verification",
                "Verifier found the mistake after the recovery window had already closed.",
                "agent delayed verification until final answer",
            )
        if task.failure_mode == "environment_persistence":
            passed = "persist the change" in harness.execution and "fresh shell" in harness.execution
            return _record(
                task,
                attempt_index,
                passed,
                "environment_state_lost",
                "environment_persistence",
                "Verifier observed that a setup change disappeared in the next command.",
                "agent assumed shell-local environment changes would persist",
            )
        if task.failure_mode == "cross_split_tension":
            passed = not _has_broad_artifact_rule(harness)
            return _record(
                task,
                attempt_index,
                passed,
                "overprompted_long_context_task",
                "cross_split_tension",
                "Verifier saw premature artifact creation displace required planning.",
                "agent created output too early on a planning-heavy task",
            )
        raise ValueError(f"unknown toy failure mode: {task.failure_mode}")


def _has_broad_artifact_rule(harness: HarnessSpec) -> bool:
    return "immediately for every task before doing analysis" in harness.bootstrap


def _has_targeted_artifact_rule(harness: HarnessSpec) -> bool:
    return "explicitly names a required output file" in harness.bootstrap


def _record(
    task: Task,
    attempt_index: int,
    passed: bool,
    terminal_cause: str,
    mechanism: str,
    message: str,
    trace_message: str,
) -> RunRecord:
    if passed:
        outcome = VerifierOutcome(
            passed=True,
            terminal_cause="passed",
            causal_status="not_applicable",
            mechanism="none",
            message="Verifier passed.",
        )
        trace = [TraceEvent(kind="verifier", message="task passed", metadata={"mode": task.failure_mode})]
    else:
        outcome = VerifierOutcome(
            passed=False,
            terminal_cause=terminal_cause,
            causal_status="agent_causal",
            mechanism=mechanism,
            message=message,
        )
        trace = [TraceEvent(kind="agent", message=trace_message, metadata={"mode": task.failure_mode})]
    return RunRecord(
        task_id=task.id,
        split=task.split,
        passed=passed,
        trace=trace,
        outcome=outcome,
        attempt_index=attempt_index,
    )
