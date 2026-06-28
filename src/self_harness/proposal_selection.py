"""Research-backed proposal selection improvements.

Implements two techniques from RHO (Retrospective Harness Optimization,
arXiv:2606.05922):

1. **Self-validation**: before expensive full evaluation, check if a proposal
   plausibly addresses the failure pattern it targets. A proposal whose
   patch touches the pattern's addressable surface and whose rationale
   mentions the pattern's mechanism gets a validation boost.

2. **Pairwise preference**: when multiple proposals are accepted by the
   strict score gate, prefer the one that improves the most failure-cluster
   tasks (not just the highest aggregate score). This catches proposals
   that fix the root cause of multiple failures vs. ones that game one task.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from self_harness.evaluation import EvaluationResult
from self_harness.proposer_policy import ADDRESSABLE_SURFACE_BY_MECHANISM
from self_harness.types import (
    AcceptDecision,
    FailurePattern,
    HarnessPatch,
    HarnessSpec,
    Proposal,
    RunRecord,
    Split,
)


@dataclass(frozen=True)
class ProposalValidation:
    """Result of self-validating a proposal against its target pattern.

    score: 0.0–1.0 confidence that this proposal addresses the pattern.
    reason: human-readable explanation.
    """
    score: float
    reason: str


def self_validate(
    proposal: Proposal,
    pattern: FailurePattern | None,
) -> ProposalValidation:
    """Lightweight self-validation of a proposal before expensive evaluation.

    Inspired by RHO's self-validation step: check if the proposal's patch
    targets the right surface for the failure mechanism, and whether the
    rationale references the pattern's symptoms.

    This is NOT a gate — it's a priority signal used to order proposals
    for evaluation (high-validation proposals are evaluated first, so the
    engine can short-circuit if budget is tight).
    """
    if pattern is None:
        return ProposalValidation(score=0.5, reason="no target pattern")

    score = 0.0
    reasons: list[str] = []

    # Check 1: does the patch touch an addressable surface for this mechanism?
    addressable = ADDRESSABLE_SURFACE_BY_MECHANISM.get(pattern.signature.mechanism, ())
    patched_surfaces = {op.surface for op in proposal.patch.ops}
    if addressable and patched_surfaces & set(addressable):
        score += 0.4
        reasons.append(f"patch targets addressable surface for {pattern.signature.mechanism}")
    else:
        reasons.append(f"patch does not target addressable surface for {pattern.signature.mechanism}")

    # Check 2: does the rationale reference the pattern's symptoms?
    rationale_lower = proposal.rationale.lower()
    symptom_hits = sum(
        1 for symptom in pattern.symptoms
        if any(word in rationale_lower for word in symptom.lower().split() if len(word) > 3)
    )
    if symptom_hits > 0:
        score += min(0.3, symptom_hits * 0.15)
        reasons.append(f"rationale references {symptom_hits} pattern symptom(s)")

    # Check 3: does the expected effect mention improvement?
    effect_lower = proposal.expected_effect.lower()
    improvement_words = {"improve", "fix", "prevent", "ensure", "force", "require", "check"}
    if any(word in effect_lower for word in improvement_words):
        score += 0.15
        reasons.append("expected effect mentions concrete improvement")

    # Check 4: pattern support — higher support = more evidence
    if pattern.support >= 3:
        score += 0.15
        reasons.append(f"high-support pattern ({pattern.support} failures)")

    return ProposalValidation(
        score=min(1.0, score),
        reason="; ".join(reasons),
    )


def _tasks_fixed_by_candidate(
    baseline_records: Sequence[RunRecord],
    candidate_records: Sequence[RunRecord],
    split: Split = Split.HELD_IN,
) -> set[str]:
    """Find task IDs that failed in baseline but pass in candidate."""
    baseline_failed = {
        r.task_id for r in baseline_records
        if r.split == split and not r.passed
    }
    candidate_passed = {
        r.task_id for r in candidate_records
        if r.split == split and r.passed
    }
    return baseline_failed & candidate_passed


def pairwise_preference(
    accepted: list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]],
    baseline: EvaluationResult,
) -> list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]]:
    """RHO-inspired pairwise preference: rank accepted proposals by how many
    previously-failing tasks they fix, not just aggregate score.

    When two proposals both pass the strict acceptance gate, the one that
    fixes more distinct failure tasks is preferred — it's more likely to
    address a root cause rather than gaming a single task.

    Ties are broken by the original priority ordering.
    """
    def preference_key(
        item: tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision],
    ) -> tuple[int, int, str]:
        proposal, _spec, _patch, candidate_eval, _decision = item
        fixed = _tasks_fixed_by_candidate(baseline.records, candidate_eval.records)
        # Negative for descending sort: more fixed tasks first
        return (-len(fixed), -proposal.priority, proposal.id)

    return sorted(accepted, key=preference_key)
