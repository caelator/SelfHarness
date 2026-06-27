import json
from pathlib import Path

from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.proposer import HeuristicProposer


def test_demo_end_to_end_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    summary = _run(first)
    _run(second)

    assert summary[0].accepted >= 1
    assert summary[0].rejected >= 1
    assert summary[-1].after_held_in == "8/8"
    assert summary[-1].after_held_out == "2/2"
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["evaluation_repeats"] == 2
    assert manifest["schema_version"] == "1.2"
    assert manifest["surface_kinds"]["skills"] == "list"
    assert "AppendToListSurface" in manifest["op_whitelist"]
    assert {"tools", "skills", "memory_sources", "subagents"}.issubset(set(manifest["surface_whitelist"]))
    proposals = [
        json.loads(line)
        for line in (first / "rounds" / "0" / "proposals.jsonl").read_text().splitlines()
    ]
    assert all("changed_surfaces" in proposal for proposal in proposals)
    assert all("decision_reason" in proposal for proposal in proposals)
    assert all(proposal["schema_version"] == "1.2" for proposal in proposals)
    assert all(proposal["evaluation_repeats"] == 2 for proposal in proposals)
    evaluation_rows = [
        json.loads(line)
        for line in (first / "rounds" / "0" / "evaluations.jsonl").read_text().splitlines()
    ]
    attempt_rows = [row for row in evaluation_rows if row["task_id"] != "__split_total__"]
    assert {row["attempt_index"] for row in attempt_rows} == {0, 1}
    assert all(row["schema_version"] == "1.2" for row in evaluation_rows)
    assert all(row["evaluation_repeats"] == 2 for row in evaluation_rows)
    assert all("failure_category" in row for row in evaluation_rows)
    lineage = json.loads((first / "lineage.json").read_text())
    assert all(row["schema_version"] == "1.2" for row in lineage)
    assert _tree_bytes(first) == _tree_bytes(second)


def _run(out_dir: Path):
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        seed=0,
    )
    return engine.run(max_rounds=3)


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }
