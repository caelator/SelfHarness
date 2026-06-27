from self_harness.proposer_policy import (
    ProposalPolicy,
    ensure_diverse,
    is_addressable,
    select_actionable_patterns,
)
from self_harness.types import FailurePattern, FailureSignature, HarnessOp, HarnessPatch, Proposal, Split


def test_policy_filters_by_support_and_addressability() -> None:
    actionable = _pattern("p1", "missing_artifact", support=2)
    weak = _pattern("p2", "late_verification", support=1)
    unaddressable = _pattern("p3", "unknown_mechanism", support=3)
    policy = ProposalPolicy(min_pattern_support=2)

    selected = select_actionable_patterns(
        [actionable, weak, unaddressable],
        editable_surfaces=["bootstrap", "verification"],
        policy=policy,
    )

    assert selected == [actionable]
    assert is_addressable(actionable, ["bootstrap"])
    assert not is_addressable(unaddressable, ["bootstrap", "verification"])


def test_agentic_mechanisms_are_addressable() -> None:
    # A Codex-judge failure (agent finished but output failed the success criteria) and a solver error
    # must map to editable surfaces, or the agentic self-improvement loop can never propose an edit.
    judge_fail = _pattern("p-judge", "codex-judge", support=1)
    solver_error = _pattern("p-solver", "agent-solver-error", support=1)
    editable = ["system_prompt", "verification", "execution", "failure_recovery", "bootstrap"]

    assert is_addressable(judge_fail, editable)
    assert is_addressable(solver_error, editable)
    selected = select_actionable_patterns(
        [judge_fail, solver_error],
        editable_surfaces=editable,
        policy=ProposalPolicy(min_pattern_support=1),
    )
    assert {p.id for p in selected} == {"p-judge", "p-solver"}


def test_diversity_keeps_distinct_payloads_but_drops_exact_duplicates() -> None:
    policy = ProposalPolicy(require_distinct_surfaces=True)
    first = _proposal("p1", "bootstrap", "A")
    duplicate = _proposal("p1", "bootstrap", "A")
    distinct_payload = _proposal("p1", "bootstrap", "B")

    assert ensure_diverse([first, duplicate, distinct_payload], policy) == [first, distinct_payload]


def _pattern(id_: str, mechanism: str, support: int) -> FailurePattern:
    return FailurePattern(
        id=id_,
        split=Split.HELD_IN,
        signature=FailureSignature("cause", "agent_causal", mechanism),
        support=support,
        task_ids=["task"],
        symptoms=[],
        verifier_evidence=[],
    )


def _proposal(pattern_id: str, surface: str, payload: str) -> Proposal:
    return Proposal(
        id=f"{pattern_id}-{surface}-{payload}",
        round_index=0,
        pattern_id=pattern_id,
        patch=HarnessPatch([HarnessOp("AppendToSurface", surface, payload)]),
        priority=1,
        rationale="test",
        expected_effect="test",
        regression_risks=[],
    )
