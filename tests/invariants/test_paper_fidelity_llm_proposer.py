import json

from self_harness.harness import initial_harness
from self_harness.llm_proposer import LLMProposer
from self_harness.types import (
    FailurePattern,
    FailureSignature,
    PassingSummary,
    ProposalBudget,
    ProposerContext,
    Split,
)


class RecordingClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.system_prompt = ""
        self.user_prompt = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return self.response


def test_llm_proposer_renders_complete_held_in_evidence_bundle() -> None:
    client = RecordingClient(json.dumps({"proposals": []}))

    LLMProposer(client).propose(_context())

    payload = json.loads(client.user_prompt)
    pattern = payload["held_in_failure_patterns"][0]
    assert pattern["id"] == "held_in__missing"
    assert pattern["support"] == 2
    assert pattern["task_ids"] == ["task-a", "task-b"]
    assert pattern["symptoms"] == ["artifact missing", "final answer absent"]
    assert pattern["verifier_evidence"] == ["required file missing", "verifier exited 1"]
    assert pattern["mechanism"] == "missing_artifact"
    assert "held-out-secret" not in client.user_prompt


def test_llm_proposer_marks_duplicate_primary_target_as_diversity_collision() -> None:
    client = RecordingClient(
        json.dumps(
            {
                "proposals": [
                    _proposal_row("one", "create artifact early", 20),
                    _proposal_row("two", "create artifact after reading task", 10),
                ]
            }
        )
    )

    proposals = LLMProposer(client).propose(_context())

    assert [proposal.invalid_reason for proposal in proposals] == [None, "diversity_collision"]


def test_llm_proposer_marks_fabricated_pattern_id_as_ungrounded() -> None:
    row = _proposal_row("fabricated", "x", 20)
    row["pattern_id"] = "held_out__fabricated"
    client = RecordingClient(json.dumps({"proposals": [row]}))

    proposals = LLMProposer(client).propose(_context())

    assert len(proposals) == 1
    assert proposals[0].invalid_reason == "ungrounded_proposal"


def _context() -> ProposerContext:
    return ProposerContext(
        held_in_patterns=[
            FailurePattern(
                id="held_in__missing",
                split=Split.HELD_IN,
                signature=FailureSignature("missing-artifact", "confirmed", "missing_artifact"),
                support=2,
                task_ids=["task-a", "task-b"],
                symptoms=["artifact missing", "final answer absent"],
                verifier_evidence=["required file missing", "verifier exited 1"],
            )
        ],
        passing_summaries=[
            PassingSummary(
                task_id="task-pass",
                split=Split.HELD_IN,
                attempt_index=0,
                trace_messages=["passed cleanly"],
                verifier_message="Verifier passed.",
            )
        ],
        attempted_edits=[],
        editable_surfaces=["bootstrap"],
        harness=initial_harness(),
        round_index=0,
        budget=ProposalBudget(),
    )


def _proposal_row(id_suffix: str, payload: str, priority: int) -> dict[str, object]:
    return {
        "id_suffix": id_suffix,
        "pattern_id": "held_in__missing",
        "priority": priority,
        "ops": [{"op": "AppendToSurface", "surface": "bootstrap", "payload": payload}],
        "rationale": "grounded",
        "expected_effect": "improve artifact creation",
        "regression_risks": [],
    }
