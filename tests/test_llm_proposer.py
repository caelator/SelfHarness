import json

from self_harness.harness import initial_harness
from self_harness.llm_proposer import LLMProposer
from self_harness.proposer_policy import ProposalPolicy
from self_harness.types import (
    FailurePattern,
    FailureSignature,
    PassingSummary,
    ProposalBudget,
    ProposerContext,
    Split,
)


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.system_prompt = ""
        self.user_prompt = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return self.response


def test_llm_proposer_parses_valid_json() -> None:
    client = FakeClient(
        json.dumps(
            {
                "proposals": [
                    {
                        "id_suffix": "targeted-artifact",
                        "pattern_id": "held_in__missing",
                        "priority": 10,
                        "ops": [
                            {
                                "op": "AppendToSurface",
                                "surface": "bootstrap",
                                "payload": "Create explicitly required artifacts early.",
                            }
                        ],
                        "rationale": "addresses missing artifacts",
                        "expected_effect": "artifact appears before verification",
                        "regression_risks": ["could be too eager"],
                    }
                ]
            }
        )
    )

    proposals = LLMProposer(client).propose(_context())

    assert len(proposals) == 1
    assert proposals[0].id == "r00__llm__targeted-artifact"
    assert proposals[0].patch.ops[0].surface == "bootstrap"


def test_llm_proposer_returns_empty_for_malformed_json() -> None:
    assert LLMProposer(FakeClient("not-json")).propose(_context()) == []


def test_llm_proposer_drops_invalid_ops_and_keeps_valid_subset() -> None:
    client = FakeClient(
        json.dumps(
            {
                "proposals": [
                    {
                        "id_suffix": "bad-op",
                        "pattern_id": "held_in__missing",
                        "priority": 20,
                        "ops": [{"op": "Nope", "surface": "bootstrap", "payload": "x"}],
                        "rationale": "bad",
                        "expected_effect": "bad",
                        "regression_risks": [],
                    },
                    {
                        "id_suffix": "good-op",
                        "pattern_id": "held_in__missing",
                        "priority": 10,
                        "ops": [{"op": "AppendToSurface", "surface": "bootstrap", "payload": "x"}],
                        "rationale": "good",
                        "expected_effect": "good",
                        "regression_risks": [],
                    },
                ]
            }
        )
    )

    proposals = LLMProposer(client).propose(_context())

    assert [proposal.id for proposal in proposals] == ["r00__llm__good-op"]


def test_llm_proposer_applies_budget_and_diversity() -> None:
    client = FakeClient(
        json.dumps(
            {
                "proposals": [
                    _proposal_row("a", "x", 20),
                    _proposal_row("b", "x", 10),
                    _proposal_row("c", "y", 5),
                ]
            }
        )
    )
    context = _context(budget=ProposalBudget(max_proposals=3, max_payload_bytes=100))
    proposer = LLMProposer(client, policy=ProposalPolicy(require_distinct_surfaces=True))

    proposals = proposer.propose(context)

    assert [proposal.id for proposal in proposals] == ["r00__llm__a", "r00__llm__b", "r00__llm__c"]
    assert proposals[0].invalid_reason is None
    assert proposals[1].invalid_reason == "diversity_collision"
    assert proposals[2].invalid_reason == "diversity_collision"


def test_llm_proposer_prompt_uses_held_in_context_only() -> None:
    client = FakeClient(json.dumps({"proposals": []}))
    context = _context()

    LLMProposer(client).propose(context)

    assert "held-out-secret" not in client.user_prompt
    assert "held_in_failure_patterns" in client.user_prompt
    assert "held_in_passing_summaries" in client.user_prompt


def test_llm_proposer_marks_fabricated_pattern_id_as_ungrounded() -> None:
    client = FakeClient(
        json.dumps(
            {
                "proposals": [
                    {
                        "id_suffix": "fabricated",
                        "pattern_id": "held_out__fabricated",
                        "priority": 10,
                        "ops": [
                            {
                                "op": "AppendToSurface",
                                "surface": "bootstrap",
                                "payload": "leaked held-out strategy",
                            }
                        ],
                        "rationale": "not grounded",
                        "expected_effect": "bad",
                        "regression_risks": [],
                    }
                ]
            }
        )
    )

    proposals = LLMProposer(client).propose(_context())

    assert len(proposals) == 1
    assert proposals[0].pattern_id == "held_out__fabricated"
    assert proposals[0].invalid_reason == "ungrounded_proposal"


def _context(budget: ProposalBudget | None = None) -> ProposerContext:
    return ProposerContext(
        held_in_patterns=[
            FailurePattern(
                id="held_in__missing",
                split=Split.HELD_IN,
                signature=FailureSignature("missing_required_artifact", "agent_causal", "missing_artifact"),
                support=1,
                task_ids=["task-a"],
                symptoms=["artifact missing"],
                verifier_evidence=["required file missing"],
            )
        ],
        passing_summaries=[
            PassingSummary(
                task_id="task-pass",
                split=Split.HELD_IN,
                attempt_index=0,
                trace_messages=["passed without secret"],
                verifier_message="Verifier passed.",
            )
        ],
        attempted_edits=[],
        editable_surfaces=["bootstrap"],
        harness=initial_harness(),
        round_index=0,
        budget=budget or ProposalBudget(),
    )


def _proposal_row(id_suffix: str, payload: str, priority: int) -> dict[str, object]:
    return {
        "id_suffix": id_suffix,
        "pattern_id": "held_in__missing",
        "priority": priority,
        "ops": [{"op": "AppendToSurface", "surface": "bootstrap", "payload": payload}],
        "rationale": "r",
        "expected_effect": "e",
        "regression_risks": [],
    }
