import json
from pathlib import Path

from self_harness.audit import load_audit_run, summarize_audit_run, write_audit_trajectory
from self_harness.config import EngineConfig
from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine, proposer_request_sha256
from self_harness.llm_proposer import LLMProposer
from self_harness.readiness import audit_tree_hash
from self_harness.testing import MockLLMClient


def test_mock_llm_proposer_runs_through_engine_loop(tmp_path: Path) -> None:
    client = MockLLMClient(seed=12)
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=LLMProposer(client),
        out_dir=tmp_path,
        config=EngineConfig(rounds=2, seed=0, schema_version="1.4", model_id="mock-llm-proposer"),
    )

    summaries = engine.run()
    audit = load_audit_run(tmp_path)
    summary = summarize_audit_run(tmp_path)
    proposals = audit.rounds[0].proposals
    evaluations = audit.rounds[0].evaluations

    assert summaries[0].proposals >= 1
    assert summary.final_held_in_score == 1.0
    assert summary.final_held_out_score == 1.0
    assert any(str(row["id"]).startswith("r00__llm__") for row in proposals)
    assert {row["schema_version"] for row in proposals} == {"1.4"}
    assert {row["schema_version"] for row in evaluations} == {"1.4"}
    assert "held_in_failure_patterns" in client.last_user_prompt


def test_mock_llm_canonical_audit_hash_matches_fixture(tmp_path: Path) -> None:
    _run_mock_llm_canonical(tmp_path)

    canonical_hash = audit_tree_hash(tmp_path)
    expected_hash = Path("tests/fixtures/canonical_llm_audit_hash.txt").read_text(encoding="utf-8").strip()

    assert canonical_hash == expected_hash


def test_opt_in_proposer_request_log_writes_raw_rows(tmp_path: Path) -> None:
    client = MockLLMClient(seed=0)
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=LLMProposer(client),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, seed=0, schema_version="1.4", model_id="mock-llm-proposer"),
    )
    engine.enable_proposer_request_log()

    engine.run()

    rows = [
        json.loads(line)
        for line in (tmp_path / "proposer_llm_request_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    proposals = [
        json.loads(line)
        for line in (tmp_path / "rounds" / "0" / "proposals.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["round_index"] == 0
    assert rows[0]["proposer_client"] == "primary"
    assert rows[0]["request_sha256"] == proposer_request_sha256(client.last_system_prompt, client.last_user_prompt)
    assert rows[0]["committed_proposals"] == len(proposals)
    assert rows[0]["attempted_proposals"] >= rows[0]["committed_proposals"]


def test_mock_llm_canonical_audit_hash_is_stable_under_ambient_environment_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TZ", "UTC")
    _run_mock_llm_canonical(first)

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    _run_mock_llm_canonical(second)

    assert audit_tree_hash(first) == audit_tree_hash(second)


def test_ungrounded_mock_llm_proposal_is_audited_as_invalid(tmp_path: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=LLMProposer(MockLLMClient(mode="ungrounded")),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, seed=0, schema_version="1.4", model_id="mock-llm-proposer"),
    )

    engine.run()
    proposals = [json.loads(line) for line in (tmp_path / "rounds" / "0" / "proposals.jsonl").read_text().splitlines()]

    assert len(proposals) == 1
    assert proposals[0]["status"] == "invalid"
    assert proposals[0]["decision_reason"] == "ungrounded_proposal"


def _run_mock_llm_canonical(out_dir: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=LLMProposer(MockLLMClient(seed=0)),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, seed=0, schema_version="1.4", model_id="mock-llm-proposer"),
    )
    engine.run()
    write_audit_trajectory(out_dir)
