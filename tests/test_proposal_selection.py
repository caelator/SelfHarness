"""Tests for research-backed proposal selection (RHO-inspired)."""

from __future__ import annotations

from self_harness.proposal_selection import self_validate
from self_harness.types import (
    FailurePattern,
    FailureSignature,
    HarnessOp,
    HarnessPatch,
    Proposal,
    Split,
)


def _make_pattern(mechanism: str = "missing_artifact", support: int = 1) -> FailurePattern:
    return FailurePattern(
        id="held_in__test",
        split=Split.HELD_IN,
        signature=FailureSignature(
            terminal_cause="test_cause",
            causal_status="test_status",
            mechanism=mechanism,
        ),
        support=support,
        task_ids=["t1"],
        symptoms=["Create required output artifacts immediately"],
        verifier_evidence=["missing output file"],
    )


def _make_proposal(
    surface: str = "bootstrap",
    rationale: str = "Force artifact creation earlier",
    expected_effect: str = "Improve artifact creation",
    pattern_id: str = "held_in__test",
) -> Proposal:
    return Proposal(
        id="prop_test_0",
        round_index=0,
        pattern_id=pattern_id,
        patch=HarnessPatch(ops=[HarnessOp(op="AppendToSurface", surface=surface, payload="test")]),
        priority=50,
        rationale=rationale,
        expected_effect=expected_effect,
        regression_risks=[],
    )


def test_self_validate_addresses_correct_surface():
    pattern = _make_pattern(mechanism="missing_artifact")
    proposal = _make_proposal(surface="bootstrap")
    result = self_validate(proposal, pattern)
    assert result.score > 0.4
    assert "addressable surface" in result.reason


def test_self_validate_wrong_surface_scores_lower():
    pattern = _make_pattern(mechanism="missing_artifact")
    # missing_artifact maps to "bootstrap" surface, not "verification"
    proposal = _make_proposal(surface="verification")
    result = self_validate(proposal, pattern)
    assert result.score < 0.5


def test_self_validate_rationale_symptom_match():
    pattern = _make_pattern()
    proposal = _make_proposal(rationale="Handle missing output artifacts by creating them early")
    result = self_validate(proposal, pattern)
    assert result.score > 0.5
    assert "symptom" in result.reason.lower()


def test_self_validate_high_support_pattern():
    pattern = _make_pattern(support=5)
    proposal = _make_proposal()
    result = self_validate(proposal, pattern)
    assert result.score > 0.5


def test_self_validate_no_pattern():
    proposal = _make_proposal()
    result = self_validate(proposal, None)
    assert result.score == 0.5


def test_self_validate_expected_effect_improvement():
    pattern = _make_pattern()
    proposal = _make_proposal(expected_effect="Fix the missing artifact problem")
    result = self_validate(proposal, pattern)
    assert result.score > 0.3
    # "fix" is in the improvement words
