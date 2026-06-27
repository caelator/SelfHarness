from pathlib import Path

import pytest

from self_harness.audit import write_audit_trajectory
from self_harness.config import EngineConfig
from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.proposer import HeuristicProposer


def test_audit_trajectory_output_is_stable_under_ambient_environment_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TZ", "UTC")
    _run_demo(first)
    write_audit_trajectory(first)

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    _run_demo(second)
    write_audit_trajectory(second)

    assert (first / "trajectory.jsonl").read_bytes() == (second / "trajectory.jsonl").read_bytes()


def _run_demo(out_dir: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, seed=0),
    )
    engine.run()
