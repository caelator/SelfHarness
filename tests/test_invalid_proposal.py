import json
from pathlib import Path

from self_harness.config import EngineConfig
from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.types import HarnessPatch, Proposal, ProposerContext


class EmptyPatchProposer:
    def propose(self, context: ProposerContext) -> list[Proposal]:
        return [
            Proposal(
                id=f"r{context.round_index:02d}__empty",
                round_index=context.round_index,
                pattern_id="synthetic",
                patch=HarnessPatch([]),
                priority=1,
                rationale="exercise invalid proposal audit path",
                expected_effect="none",
                regression_risks=[],
            )
        ]


class PreInvalidatedProposer:
    def propose(self, context: ProposerContext) -> list[Proposal]:
        return [
            Proposal(
                id=f"r{context.round_index:02d}__invalidated",
                round_index=context.round_index,
                pattern_id="synthetic",
                patch=HarnessPatch([]),
                priority=1,
                rationale="exercise proposer-side invalidation",
                expected_effect="none",
                regression_risks=[],
                invalid_reason="diversity_collision",
            )
        ]


def test_invalid_proposal_is_audited(tmp_path: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(),
        proposer=EmptyPatchProposer(),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1),
    )

    summary = engine.run()
    rows = [
        json.loads(line)
        for line in (tmp_path / "rounds" / "0" / "proposals.jsonl").read_text().splitlines()
    ]

    assert summary[0].accepted == 0
    assert summary[0].rejected == 1
    assert rows[0]["status"] == "invalid"
    assert rows[0]["decision_reason"] == "proposal does not modify any editable surface"
    assert rows[0]["schema_version"] == "1.2"


def test_proposer_side_invalid_reason_is_audited(tmp_path: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(),
        proposer=PreInvalidatedProposer(),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1),
    )

    engine.run()
    rows = [
        json.loads(line)
        for line in (tmp_path / "rounds" / "0" / "proposals.jsonl").read_text().splitlines()
    ]

    assert rows[0]["status"] == "invalid"
    assert rows[0]["decision_reason"] == "diversity_collision"
