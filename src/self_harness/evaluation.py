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
) -> EvaluationResult:
    if repeats < 1:
        raise ValueError("evaluation repeats must be at least 1")
    records: list[RunRecord] = []
    for attempt_index in range(repeats):
        for task in tasks:
            record = runner.run(task, harness, attempt_index=attempt_index)
            if record.attempt_index != attempt_index:
                record = replace(record, attempt_index=attempt_index)
            records.append(record)
    return EvaluationResult(
        held_in=_split_result(records, Split.HELD_IN),
        held_out=_split_result(records, Split.HELD_OUT),
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


def _split_result(records: list[RunRecord], split: Split) -> SplitResult:
    split_records = [record for record in records if record.split == split]
    passed = sum(1 for record in split_records if record.passed)
    return SplitResult(split=split, passed=passed, failed=len(split_records) - passed)
