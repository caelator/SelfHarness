from __future__ import annotations

from typing import Protocol

from self_harness.proposer_policy import ProposalPolicy, ensure_diverse, select_actionable_patterns
from self_harness.types import (
    FailurePattern,
    HarnessOp,
    HarnessPatch,
    Proposal,
    ProposerContext,
    Split,
)


class Proposer(Protocol):
    def propose(self, context: ProposerContext) -> list[Proposal]:
        ...


class HeuristicProposer:
    """Deterministic demo proposer with the same data boundary as an LLM proposer."""

    def __init__(self, policy: ProposalPolicy | None = None) -> None:
        self.policy = policy or ProposalPolicy()

    def propose(self, context: ProposerContext) -> list[Proposal]:
        assert all(pattern.split == Split.HELD_IN for pattern in context.held_in_patterns)
        assert all(summary.split == Split.HELD_IN for summary in context.passing_summaries)

        proposals: list[Proposal] = []
        for pattern in select_actionable_patterns(
            context.held_in_patterns,
            context.editable_surfaces,
            self.policy,
        ):
            proposals.extend(_proposals_for_pattern(pattern, context.round_index))

        filtered: list[Proposal] = []
        for proposal in sorted(proposals, key=lambda item: (-item.priority, item.id)):
            payload_size = sum(len(str(op.payload).encode("utf-8")) for op in proposal.patch.ops)
            if payload_size <= context.budget.max_payload_bytes:
                filtered.append(proposal)
            if len(filtered) >= context.budget.max_proposals:
                break
        return ensure_diverse(filtered, self.policy)


def _proposals_for_pattern(pattern: FailurePattern, round_index: int) -> list[Proposal]:
    mechanism = pattern.signature.mechanism
    if mechanism == "missing_artifact":
        return [
            _proposal(
                round_index,
                pattern,
                "bootstrap_broad",
                90,
                "AppendToSurface",
                "bootstrap",
                "Create required output artifacts immediately for every task before doing analysis.",
                "Force artifact creation earlier.",
                ["May distract long-context tasks that need planning before artifact creation."],
            ),
            _proposal(
                round_index,
                pattern,
                "bootstrap_targeted",
                80,
                "AppendToSurface",
                "bootstrap",
                (
                    "When the task explicitly names a required output file, create that artifact early "
                    "and update it after verification."
                ),
                "Create explicit required artifacts without changing unrelated tasks.",
                ["May be too narrow for implicit artifact requirements."],
            ),
        ]
    if mechanism == "repeated_failed_command":
        return [
            _proposal(
                round_index,
                pattern,
                "failure_recovery",
                70,
                "AppendToSurface",
                "failure_recovery",
                "After a command fails, change strategy before retrying; do not repeat the exact failed command.",
                "Break repeated command failure loops.",
                ["Could add unnecessary branching for transient failures."],
            )
        ]
    if mechanism == "late_verification":
        return [
            _proposal(
                round_index,
                pattern,
                "verification",
                60,
                "AppendToSurface",
                "verification",
                (
                    "Run targeted verification as soon as a candidate artifact or fix exists, "
                    "while there is still time to recover."
                ),
                "Move verification earlier in the work loop.",
                ["Could spend too much budget on early checks."],
            )
        ]
    if mechanism == "environment_persistence":
        return [
            _proposal(
                round_index,
                pattern,
                "execution",
                50,
                "AppendToSurface",
                "execution",
                (
                    "When environment setup changes PATH, installs tools, or exports variables, "
                    "persist the change and verify it in a fresh shell."
                ),
                "Preserve environment state across command boundaries.",
                ["Could be unnecessary on stateless tasks."],
            )
        ]
    return []


def _proposal(
    round_index: int,
    pattern: FailurePattern,
    suffix: str,
    priority: int,
    op_name: str,
    surface: str,
    payload: str,
    expected_effect: str,
    risks: list[str],
) -> Proposal:
    return Proposal(
        id=f"r{round_index:02d}__{pattern.id}__{suffix}",
        round_index=round_index,
        pattern_id=pattern.id,
        patch=HarnessPatch([HarnessOp(op_name, surface, payload)]),
        priority=priority,
        rationale=f"Pattern {pattern.id} has support {pattern.support}.",
        expected_effect=expected_effect,
        regression_risks=risks,
    )
