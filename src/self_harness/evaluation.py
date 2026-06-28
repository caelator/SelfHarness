from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from self_harness.types import AcceptDecision, EvaluationResult, HarnessSpec, RunRecord, Split, SplitResult, Task


class Runner(Protocol):
    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        ...


def evaluate(
    runner: Runner,
    harness: HarnessSpec,
    tasks: list[Task],
    repeats: int = 1,
    *,
    aggregation: str = "sum",
) -> EvaluationResult:
    """Evaluate each task up to ``repeats`` times.

    ``aggregation`` selects how repeats combine into split scores:

    - ``"sum"`` (default, paper-faithful): every attempt runs and a split's ``passed`` is the total number
      of passing *attempts* (tasks × repeats). This is the canonical scoring the paper defines and that the
      reproduction fixtures / paper-fidelity invariants pin — do not change it for eval/reproduction paths.
    - ``"majority"``: a split's ``passed`` counts *tasks* won by strict majority of their attempts, and
      attempts stop early for a task once the verdict is locked in (e.g. with repeats=3, two agreeing
      attempts skip the third). This is a practical mode for the continuous self-improvement loop: it
      denoises stochastic solving and gives the acceptance gate task-level resolution, at lower cost. It is
      symmetric across baseline/candidate arms, so it never biases the comparison.

    Either way, every attempt actually run is kept in ``records`` for the audit trail.
    """

    if repeats < 1:
        raise ValueError("evaluation repeats must be at least 1")
    if aggregation not in ("sum", "majority"):
        raise ValueError(f"unknown aggregation: {aggregation!r} (use 'sum' or 'majority')")
    records: list[RunRecord] = []
    if aggregation == "sum":
        for attempt_index in range(repeats):
            for task in tasks:
                record = runner.run(task, harness, attempt_index=attempt_index)
                if record.attempt_index != attempt_index:
                    record = replace(record, attempt_index=attempt_index)
                records.append(record)
    else:
        threshold = repeats // 2 + 1  # strict majority of the full repeat budget
        for task in tasks:
            passes = 0
            fails = 0
            for attempt_index in range(repeats):
                record = runner.run(task, harness, attempt_index=attempt_index)
                if record.attempt_index != attempt_index:
                    record = replace(record, attempt_index=attempt_index)
                records.append(record)
                if record.passed:
                    passes += 1
                else:
                    fails += 1
                if passes >= threshold or fails >= threshold:
                    break  # verdict locked; remaining attempts can't change it
    return EvaluationResult(
        held_in=_split_result(records, Split.HELD_IN, repeats, aggregation),
        held_out=_split_result(records, Split.HELD_OUT, repeats, aggregation),
        records=records,
        evaluation_repeats=repeats,
    )


def acceptance_rule(baseline: EvaluationResult, candidate: EvaluationResult) -> AcceptDecision:
    held_in_delta = candidate.held_in.passed - baseline.held_in.passed
    held_out_delta = candidate.held_out.passed - baseline.held_out.passed
    improves = held_in_delta > 0 or held_out_delta > 0
    degrades = held_in_delta < 0 or held_out_delta < 0
    if improves and not degrades:
        accepted = True
        reason = "strict improvement with no split regression"
    elif degrades:
        accepted = False
        reason = "candidate regresses at least one split"
    else:
        accepted = False
        reason = "candidate ties both splits"
    return AcceptDecision(
        accepted=accepted,
        reason=reason,
        baseline_held_in=baseline.held_in,
        baseline_held_out=baseline.held_out,
        candidate_held_in=candidate.held_in,
        candidate_held_out=candidate.held_out,
    )


def _split_result(records: list[RunRecord], split: Split, repeats: int, aggregation: str) -> SplitResult:
    """Score a split. ``sum`` counts passing attempts (paper-faithful); ``majority`` counts tasks won
    by a strict majority of their attempts (robust to a single flaky attempt; pairs with early-stop)."""

    split_records = [record for record in records if record.split == split]
    if aggregation == "sum":
        passed = sum(1 for record in split_records if record.passed)
        return SplitResult(split=split, passed=passed, failed=len(split_records) - passed)
    threshold = repeats // 2 + 1
    passes_by_task: dict[str, int] = {}
    tasks: set[str] = set()
    for record in split_records:
        tasks.add(record.task_id)
        if record.passed:
            passes_by_task[record.task_id] = passes_by_task.get(record.task_id, 0) + 1
    passed = sum(1 for task_id in tasks if passes_by_task.get(task_id, 0) >= threshold)
    return SplitResult(split=split, passed=passed, failed=len(tasks) - passed)
