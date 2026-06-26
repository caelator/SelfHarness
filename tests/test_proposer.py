from self_harness.harness import initial_harness
from self_harness.proposer import HeuristicProposer
from self_harness.types import FailurePattern, FailureSignature, ProposalBudget, ProposerContext, Split


def test_proposer_rejects_held_out_patterns() -> None:
    pattern = FailurePattern(
        id="held_out__x",
        split=Split.HELD_OUT,
        signature=FailureSignature("cause", "agent_causal", "missing_artifact"),
        support=1,
        task_ids=["task"],
        symptoms=[],
        verifier_evidence=[],
    )
    context = ProposerContext(
        held_in_patterns=[pattern],
        passing_summaries=[],
        attempted_edits=[],
        editable_surfaces=["bootstrap"],
        harness=initial_harness(),
        round_index=0,
        budget=ProposalBudget(),
    )

    try:
        HeuristicProposer().propose(context)
    except AssertionError:
        pass
    else:
        raise AssertionError("expected proposer to reject held-out patterns")
