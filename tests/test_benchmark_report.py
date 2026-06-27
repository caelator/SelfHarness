import json
from pathlib import Path

from self_harness.config import EngineConfig
from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.proposer import HeuristicProposer
from self_harness.reporting import build_benchmark_report


def test_build_benchmark_report_from_multiple_audits(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _run_demo(left, model_id="minimax-fixture")
    _run_demo(right, model_id="glm-fixture")

    report = build_benchmark_report({"minimax": left, "glm": right})

    assert report["schema_version"] == "1.0"
    assert report["reproduction_claimed"] is False
    assert sorted(report["provenance_per_model"]) == ["glm", "minimax"]
    assert report["per_model_summary"]["minimax"]["rounds"] == 1
    assert report["split_gains"]["minimax"]["held_in_pass_delta"] == 8
    assert report["per_task_breakdown"]["glm"]


def test_benchmark_report_cli_writes_stable_json(tmp_path: Path) -> None:
    from self_harness.cli import main

    left = tmp_path / "left"
    right = tmp_path / "right"
    out = tmp_path / "report.json"
    _run_demo(left, model_id="qwen-fixture")
    _run_demo(right, model_id="glm-fixture")

    code = main(
        [
            "benchmark-report",
            "--audit-dir",
            f"qwen:{left}",
            "--audit-dir",
            f"glm:{right}",
            "--out",
            str(out),
        ]
    )
    report = json.loads(out.read_text(encoding="utf-8"))

    assert code == 0
    assert report["schema_version"] == "1.0"
    assert report["reproduction_claimed"] is False


def _run_demo(out_dir: Path, *, model_id: str) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=0),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, seed=0, model_id=model_id),
    )
    engine.run()
