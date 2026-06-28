from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.evaluation import acceptance_rule, evaluate
from self_harness.harness import apply_patch, initial_harness
from self_harness.types import HarnessOp, HarnessPatch


def test_acceptance_accepts_strict_non_regression() -> None:
    spec = initial_harness()
    baseline = evaluate(DeterministicRunner(), spec, demo_tasks(), repeats=2)
    candidate, _ = apply_patch(
        spec,
        HarnessPatch(
            [
                HarnessOp(
                    "AppendToSurface",
                    "bootstrap",
                    (
                        "When the task explicitly names a required output file, create that artifact early "
                        "and update it after verification."
                    ),
                )
            ]
        ),
    )

    candidate_result = evaluate(DeterministicRunner(), candidate, demo_tasks(), repeats=2)
    decision = acceptance_rule(baseline, candidate_result)

    # Default aggregation is paper-faithful "sum": passed counts passing ATTEMPTS (4 tasks × 2 = 8).
    assert baseline.held_in.total == 8
    assert candidate_result.held_in.passed == 2
    assert candidate_result.evaluation_repeats == 2
    assert decision.accepted


def test_acceptance_rejects_held_out_regression() -> None:
    spec = initial_harness()
    baseline = evaluate(DeterministicRunner(), spec, demo_tasks(), repeats=2)
    candidate, _ = apply_patch(
        spec,
        HarnessPatch(
            [
                HarnessOp(
                    "AppendToSurface",
                    "bootstrap",
                    "Create required output artifacts immediately for every task before doing analysis.",
                )
            ]
        ),
    )

    decision = acceptance_rule(baseline, evaluate(DeterministicRunner(), candidate, demo_tasks(), repeats=2))

    assert not decision.accepted
    assert "regresses" in decision.reason


def test_acceptance_rejects_tie() -> None:
    spec = initial_harness()
    baseline = evaluate(DeterministicRunner(), spec, demo_tasks(), repeats=2)

    decision = acceptance_rule(baseline, evaluate(DeterministicRunner(), spec, demo_tasks(), repeats=2))

    assert not decision.accepted
    assert "ties" in decision.reason


# ---- majority vote + early stop --------------------------------------------------------------------


class _ScriptedRunner:
    """Runs each task per a fixed pass/fail script; records how many attempts it was actually asked for."""

    def __init__(self, script: dict[str, list[bool]]) -> None:
        self.script = script
        self.calls: dict[str, int] = {}

    def run(self, task, harness, attempt_index: int = 0):  # type: ignore[no-untyped-def]
        from self_harness.types import RunRecord, Split, VerifierOutcome

        self.calls[task.id] = self.calls.get(task.id, 0) + 1
        passed = self.script[task.id][attempt_index]
        return RunRecord(
            task_id=task.id,
            split=Split.HELD_IN,
            passed=passed,
            trace=[],
            outcome=VerifierOutcome(
                passed=passed,
                terminal_cause="verifier-pass" if passed else "verifier-fail",
                causal_status="n/a",
                mechanism="none",
                message="",
            ),
            attempt_index=attempt_index,
        )


def _task(task_id: str):  # type: ignore[no-untyped-def]
    from self_harness.types import Split, Task

    return Task(id=task_id, split=Split.HELD_IN, failure_mode="x", description="d")


def test_majority_vote_early_stops_when_two_agree() -> None:
    # First two attempts agree (pass,pass) → the third must NOT run; task counts as passed.
    runner = _ScriptedRunner({"t": [True, True, False]})
    result = evaluate(runner, initial_harness(), [_task("t")], repeats=3, aggregation="majority")
    assert runner.calls["t"] == 2  # early-stopped after 2
    assert result.held_in.passed == 1
    assert result.held_in.total == 1


def test_majority_vote_runs_third_when_split() -> None:
    # First two disagree (pass,fail) → the third is the tiebreaker and MUST run.
    runner = _ScriptedRunner({"t": [True, False, False]})
    result = evaluate(runner, initial_harness(), [_task("t")], repeats=3, aggregation="majority")
    assert runner.calls["t"] == 3
    assert result.held_in.passed == 0  # 1/3 pass < majority → task fails


def test_majority_vote_counts_2_of_3_as_pass() -> None:
    runner = _ScriptedRunner({"t": [False, True, True]})
    result = evaluate(runner, initial_harness(), [_task("t")], repeats=3, aggregation="majority")
    assert runner.calls["t"] == 3  # needed all 3 (F then T,T reaches majority on the 3rd)
    assert result.held_in.passed == 1
