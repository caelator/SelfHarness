import json
from pathlib import Path

from self_harness.adapters.terminal_bench.harbor_artifacts import (
    discover_trials,
    inspect_run_dir,
    parse_reward,
    parse_trajectory_log,
)


def test_inspect_run_dir_reports_stable_tree_hashes(tmp_path: Path) -> None:
    (tmp_path / "trial").mkdir()
    (tmp_path / "trial" / "reward.txt").write_text("1\n", encoding="utf-8")

    inspection = inspect_run_dir(tmp_path)

    assert inspection["schema_version"] == "1.0"
    assert inspection["files"][0]["path"] == "trial/reward.txt"
    assert inspection["files"][0]["size_bytes"] == 2
    assert isinstance(inspection["files"][0]["sha256"], str)


def test_parse_reward_handles_json_number_object_and_text(tmp_path: Path) -> None:
    number = tmp_path / "number.json"
    obj = tmp_path / "object.json"
    text = tmp_path / "reward.txt"
    number.write_text("1.0", encoding="utf-8")
    obj.write_text(json.dumps({"reward": 0.5}), encoding="utf-8")
    text.write_text("0\n", encoding="utf-8")

    assert parse_reward(number) == (1.0, "reward.json")
    assert parse_reward(obj) == (0.5, "reward.json")
    assert parse_reward(text) == (0.0, "reward.txt")
    assert parse_reward(tmp_path / "missing.json") == (None, "missing")


def test_parse_trajectory_log_preserves_generic_events(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    path.write_text(
        json.dumps({"kind": "tool", "message": "ran verifier", "exit_code": 0}) + "\n",
        encoding="utf-8",
    )

    events, source = parse_trajectory_log(path)

    assert source == "trajectory.jsonl"
    assert events[0].kind == "tool"
    assert events[0].message == "ran verifier"
    assert events[0].metadata["exit_code"] == 0


def test_discover_trials_returns_source_attributed_records(tmp_path: Path) -> None:
    trial = tmp_path / "held-out-smoke" / "0"
    trial.mkdir(parents=True)
    trial.joinpath("metadata.json").write_text(json.dumps({"task_id": "held-out-smoke"}), encoding="utf-8")
    trial.joinpath("reward.json").write_text(json.dumps({"reward": 1.0}), encoding="utf-8")
    trial.joinpath("trajectory.jsonl").write_text(
        json.dumps({"kind": "assistant", "message": "done"}) + "\n",
        encoding="utf-8",
    )

    records = discover_trials(tmp_path)

    assert len(records) == 1
    assert records[0].task_id == "held-out-smoke"
    assert records[0].attempt_index == 0
    assert records[0].passed
    assert records[0].field_sources["reward_value"] == "reward.json"
    assert records[0].field_sources["trajectory_events"] == "trajectory.jsonl"
    assert records[0].provenance.validation_status == "candidate"


def test_discover_trials_marks_partial_when_reward_missing(tmp_path: Path) -> None:
    trial = tmp_path / "held-out-smoke" / "0"
    trial.mkdir(parents=True)
    trial.joinpath("metadata.json").write_text(json.dumps({"task_id": "held-out-smoke"}), encoding="utf-8")
    trial.joinpath("trajectory.jsonl").write_text("plain text event\n", encoding="utf-8")

    records = discover_trials(tmp_path)

    assert records[0].reward_value is None
    assert records[0].provenance.validation_status == "partial"
    assert records[0].provenance.missing_required == ("reward",)
