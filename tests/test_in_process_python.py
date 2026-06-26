import json
from pathlib import Path

import pytest

from self_harness.adapters.in_process_python import InProcessPythonRunner, InProcessPythonTaskAdapter
from self_harness.cli import main
from self_harness.config import EngineConfig
from self_harness.corpus import TaskCorpus
from self_harness.engine import SelfHarnessEngine
from self_harness.evaluation import evaluate
from self_harness.exceptions import InProcessVerifierError, TaskLoadError
from self_harness.harness import initial_harness
from self_harness.proposer import HeuristicProposer
from self_harness.types import Split, Task

FIXTURE_MODULE = Path("tests/fixtures/in_process_verifier.py")


def test_in_process_runner_maps_structured_pass_and_failure() -> None:
    runner = _runner()
    harness = initial_harness()

    passed = runner.run(_task("pass", "pass"), harness)
    failed = runner.run(_task("fail", "fail"), harness)

    assert passed.passed
    assert passed.outcome.terminal_cause == "verifier-pass"
    assert passed.outcome.mechanism == "in-process-verifier"
    assert not failed.passed
    assert failed.outcome.terminal_cause == "assertion-fail"
    assert failed.outcome.mechanism == "fixture-assertion"


def test_in_process_runner_uses_setup_hook_and_selector_passthrough() -> None:
    record = _runner().run(_task("needs-setup", "needs-setup"), initial_harness(), attempt_index=3)

    assert record.passed
    assert record.outcome.message == "attempt=3"
    assert [event.kind for event in record.trace] == ["workspace", "setup", "verify"]


def test_in_process_runner_uses_fresh_workdir_per_attempt() -> None:
    result = evaluate(_runner(), initial_harness(), [_task("fresh", "pass")], repeats=2)

    workdirs = {
        event.metadata["workdir"]
        for record in result.records
        for event in record.trace
        if event.metadata and "workdir" in event.metadata
    }

    assert len(workdirs) == 2
    assert {record.attempt_index for record in result.records} == {0, 1}


def test_in_process_runner_fails_closed_on_unknown_failure_category() -> None:
    with pytest.raises(InProcessVerifierError, match="invalid-failure-category"):
        _runner().run(_task("unknown", "unknown-category"), initial_harness())


def test_in_process_runner_maps_setup_and_verify_exceptions_to_environment_errors() -> None:
    runner = _runner()

    setup_record = runner.run(_task("setup", "setup-exception"), initial_harness())
    verify_record = runner.run(_task("verify", "verify-exception"), initial_harness())

    assert setup_record.outcome.terminal_cause == "environment-error"
    assert setup_record.outcome.mechanism == "verifier-exception"
    assert verify_record.outcome.terminal_cause == "environment-error"
    assert verify_record.outcome.message == "RuntimeError"


def test_in_process_runner_validates_selector_shape() -> None:
    task = Task(
        id="bad-selector",
        split=Split.HELD_IN,
        failure_mode="in_process_python",
        description="bad selector",
        metadata={"verifier_selector": "x" * 257},
    )

    with pytest.raises(TaskLoadError):
        _runner().run(task, initial_harness())


def test_in_process_engine_artifacts_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    _run_engine(first)
    _run_engine(second)

    assert _tree_bytes(first) == _tree_bytes(second)


def test_python_demo_cli_requires_trusted_module_and_runs(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    out_dir = tmp_path / "run"

    with pytest.raises(SystemExit) as exc:
        main(["python-demo", str(corpus), "--out", str(out_dir)])
    assert exc.value.code == 2

    code = main(
        [
            "python-demo",
            str(corpus),
            "--trust-verifier-module",
            str(FIXTURE_MODULE),
            "--rounds",
            "1",
            "--evaluation-repeats",
            "2",
            "--out",
            str(out_dir),
        ]
    )
    output = capsys.readouterr().out
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert code == 0
    assert "not a benchmark reproduction" in output
    assert manifest["model_id"] == "in-process-python-verifier"


def _runner() -> InProcessPythonRunner:
    return InProcessPythonTaskAdapter(module_path=str(FIXTURE_MODULE)).runner()


def _run_engine(out_dir: Path) -> None:
    adapter = InProcessPythonTaskAdapter(module_path=str(FIXTURE_MODULE))
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="python-fixture",
        tasks=[
            _task("held-in-pass", "needs-setup", split=Split.HELD_IN),
            _task("held-out-pass", "pass", split=Split.HELD_OUT),
        ],
    )
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2, model_id="in-process-python-verifier"),
    )
    engine.run()


def _write_corpus(path: Path) -> None:
    payload = {
        "corpus_version": "1",
        "corpus_id": "python-cli-fixture",
        "tasks": [
            _task_row("held-in-pass", "held_in", "needs-setup"),
            _task_row("held-out-pass", "held_out", "pass"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _task(id_: str, selector: str, *, split: Split = Split.HELD_IN) -> Task:
    return Task(
        id=id_,
        split=split,
        failure_mode="in_process_python",
        description=id_,
        metadata={"verifier_selector": selector},
    )


def _task_row(id_: str, split: str, selector: str) -> dict[str, object]:
    return {
        "id": id_,
        "split": split,
        "failure_mode": "in_process_python",
        "description": id_,
        "metadata": {"verifier_selector": selector},
    }


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }
