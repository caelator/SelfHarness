import json
from pathlib import Path

from self_harness.adapters.terminal_bench.agent_render import render_agent_config
from self_harness.adapters.terminal_bench.corpus import load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.runner import HarborRunner
from self_harness.audit import summarize_audit_run
from self_harness.cli import main
from self_harness.harness import apply_patch, initial_harness
from self_harness.types import HarnessOp, HarnessPatch, Split

FIXTURE_DIR = Path("tests/fixtures/terminal_bench")
MANIFEST = FIXTURE_DIR / "manifest.json"


def test_terminal_bench_manifest_loads_task_corpus() -> None:
    corpus = load_terminal_bench_manifest(MANIFEST)

    assert corpus.corpus_id == "terminal-bench@2.0-fixture"
    assert [task.split for task in corpus.tasks] == [Split.HELD_IN, Split.HELD_OUT]
    assert corpus.tasks[0].failure_mode == "terminal_bench"
    assert corpus.tasks[0].metadata["instruction"] == "Create /app/answer.txt before finishing."
    assert isinstance(corpus.tasks[0].metadata["task_source_hash"], str)


def test_agent_render_is_stable_and_reflects_harness_edits() -> None:
    harness = initial_harness()
    rendered = render_agent_config(harness)
    edited, _reverse = apply_patch(
        harness,
        HarnessPatch(
            [
                HarnessOp(
                    "AppendToSurface",
                    "bootstrap",
                    "When the task explicitly names a required output file, create it early.",
                )
            ]
        ),
    )
    edited_rendered = render_agent_config(edited)

    assert rendered == render_agent_config(harness)
    assert rendered["config_hash"] != edited_rendered["config_hash"]
    assert edited_rendered["instructions"][1]["surface"] == "bootstrap"


def test_harbor_runner_dry_run_uses_rendered_harness_to_close_loop() -> None:
    corpus = load_terminal_bench_manifest(MANIFEST)
    task = corpus.tasks[0]
    runner = HarborRunner(dataset="terminal-bench@2.0", fixture_dir=FIXTURE_DIR)
    harness = initial_harness()
    edited, _reverse = apply_patch(
        harness,
        HarnessPatch(
            [
                HarnessOp(
                    "AppendToSurface",
                    "bootstrap",
                    "When the task explicitly names a required output file, create it early.",
                )
            ]
        ),
    )

    before = runner.run(task, harness)
    after = runner.run(task, edited)

    assert not before.passed
    assert before.outcome.terminal_cause == "missing-artifact"
    assert after.passed
    assert after.metadata["task_source_hash"] == task.metadata["task_source_hash"]


def test_terminal_bench_cli_dry_run_emits_schema_13_audit(tmp_path: Path) -> None:
    out_dir = tmp_path / "tb"

    code = main(
        [
            "terminal-bench",
            "--mode",
            "dry-run",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--fixture-dir",
            str(FIXTURE_DIR),
            "--rounds",
            "2",
            "--out",
            str(out_dir),
        ]
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    evaluations = [
        json.loads(line)
        for line in (out_dir / "rounds" / "0" / "evaluations.jsonl").read_text().splitlines()
    ]
    summary = summarize_audit_run(out_dir)

    assert code == 0
    assert manifest["schema_version"] == "1.3"
    assert manifest["benchmark_protocol"] == "terminal-bench@2.0"
    assert manifest["reproduction_claimed"] is False
    assert summary.benchmark_protocol == "terminal-bench@2.0"
    assert summary.reproduction_claimed is False
    assert any(row.get("task_source_hash") for row in evaluations if row["task_id"] != "__split_total__")
