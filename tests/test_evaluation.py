from self_harness.demo import ToyRunner, demo_tasks
from self_harness.evaluation import acceptance_rule, evaluate
from self_harness.harness import apply_patch, initial_harness
from self_harness.types import HarnessOp, HarnessPatch


def test_acceptance_accepts_strict_non_regression() -> None:
    spec = initial_harness()
    baseline = evaluate(ToyRunner(), spec, demo_tasks(), repeats=2)
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

    candidate_result = evaluate(ToyRunner(), candidate, demo_tasks(), repeats=2)
    decision = acceptance_rule(baseline, candidate_result)

    assert baseline.held_in.total == 8
    assert candidate_result.held_in.passed == 2
    assert candidate_result.evaluation_repeats == 2
    assert decision.accepted


def test_acceptance_rejects_held_out_regression() -> None:
    spec = initial_harness()
    baseline = evaluate(ToyRunner(), spec, demo_tasks(), repeats=2)
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

    decision = acceptance_rule(baseline, evaluate(ToyRunner(), candidate, demo_tasks(), repeats=2))

    assert not decision.accepted
    assert "regresses" in decision.reason


def test_acceptance_rejects_tie() -> None:
    spec = initial_harness()
    baseline = evaluate(ToyRunner(), spec, demo_tasks(), repeats=2)

    decision = acceptance_rule(baseline, evaluate(ToyRunner(), spec, demo_tasks(), repeats=2))

    assert not decision.accepted
    assert "ties" in decision.reason
