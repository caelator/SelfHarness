import json
from pathlib import Path

from self_harness.audit import load_audit_run, summarize_audit_run
from self_harness.cli import main

MANIFEST = Path("tests/fixtures/terminal_bench/manifest.json")


def test_harbor_ingest_writes_schema_14_audit(tmp_path: Path) -> None:
    run_dir = tmp_path / "harbor-run"
    out_dir = tmp_path / "audit"
    _write_trial(run_dir, "held-out-smoke", 0, reward=1.0)

    code = main(["harbor-ingest", str(run_dir), "--manifest", str(MANIFEST), "--out", str(out_dir)])
    audit = load_audit_run(out_dir)
    summary = summarize_audit_run(out_dir)
    evaluations = [
        json.loads(line)
        for line in (out_dir / "rounds" / "0" / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert code == 0
    assert audit.manifest["schema_version"] == "1.4"
    assert audit.manifest["reproduction_claimed"] is False
    assert audit.manifest["harbor_artifact_validation_status"] == "candidate"
    assert summary.final_held_out_score == 1.0
    assert any(row.get("reward_source") == "reward.json" for row in evaluations)
    assert any(row.get("trajectory_event_count") == 1 for row in evaluations)


def test_harbor_inspect_cli_writes_redacted_tree(tmp_path: Path) -> None:
    run_dir = tmp_path / "harbor-run"
    run_dir.mkdir()
    run_dir.joinpath("reward.txt").write_text("1\n", encoding="utf-8")
    out_path = tmp_path / "inspection.json"

    code = main(["harbor-inspect", str(run_dir), "--out", str(out_path)])
    inspection = json.loads(out_path.read_text(encoding="utf-8"))

    assert code == 0
    assert inspection["files"][0]["path"] == "reward.txt"
    assert "sha256" in inspection["files"][0]


def _write_trial(root: Path, task_id: str, attempt: int, *, reward: float) -> None:
    trial = root / task_id / str(attempt)
    trial.mkdir(parents=True)
    trial.joinpath("metadata.json").write_text(json.dumps({"task_id": task_id}), encoding="utf-8")
    trial.joinpath("reward.json").write_text(json.dumps({"reward": reward}), encoding="utf-8")
    trial.joinpath("trajectory.jsonl").write_text(
        json.dumps({"kind": "assistant", "message": "finished"}) + "\n",
        encoding="utf-8",
    )
