import json
from pathlib import Path

import pytest

from self_harness.adapters.local_subprocess import LocalSubprocessRunner, load_tasks_json
from self_harness.evaluation import evaluate
from self_harness.exceptions import TaskLoadError
from self_harness.harness import initial_harness
from self_harness.types import Split, Task


def test_load_tasks_json(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "write-answer",
                        "split": "held_in",
                        "failure_mode": "missing_artifact",
                        "description": "write an answer file",
                        "metadata": {
                            "solve_command": "printf ok > answer.txt",
                            "verify_command": "test -f answer.txt",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    tasks = load_tasks_json(path)

    assert tasks[0].id == "write-answer"
    assert tasks[0].split == Split.HELD_IN
    assert tasks[0].metadata["verify_command"] == "test -f answer.txt"


def test_load_tasks_json_rejects_invalid_shape(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(TaskLoadError):
        load_tasks_json(path)


def test_local_subprocess_runner_pass_fail_and_timeout() -> None:
    runner = LocalSubprocessRunner()
    harness = initial_harness()
    passing = _task("passing", "printf ok > answer.txt", "test -f answer.txt")
    failing = _task("failing", "true", "test -f answer.txt")
    timeout = _task("timeout", "sleep 2", "true", timeout_seconds=1)

    pass_record = runner.run(passing, harness)
    fail_record = runner.run(failing, harness)
    timeout_record = runner.run(timeout, harness)

    assert pass_record.passed
    assert pass_record.outcome.terminal_cause == "verifier-pass"
    assert not fail_record.passed
    assert fail_record.outcome.terminal_cause == "missing-artifact"
    assert not timeout_record.passed
    assert timeout_record.outcome.terminal_cause == "timeout"


def test_local_subprocess_runner_classifies_failure_categories() -> None:
    runner = LocalSubprocessRunner()
    harness = initial_harness()
    tasks = [
        _task("pass", "true", "true"),
        _task("generic-fail", "true", "python3 -c 'import sys; sys.exit(2)'"),
        _task("missing", "true", "test -f answer.txt"),
        _task("assertion", "true", "python3 -c 'assert False, \"expected ok\"'"),
        _task("environment", "true", "__self_harness_missing_command__"),
        _task("timeout", "sleep 2", "true", timeout_seconds=1),
    ]

    categories = {runner.run(task, harness).outcome.terminal_cause for task in tasks}

    assert categories == {
        "verifier-pass",
        "verifier-fail",
        "missing-artifact",
        "assertion-fail",
        "environment-error",
        "timeout",
    }


def test_local_subprocess_runner_uses_fresh_workdir_per_attempt() -> None:
    task = _task("fresh", "true", "true")
    result = evaluate(LocalSubprocessRunner(), initial_harness(), [task], repeats=2)

    workdirs = {
        event.metadata["workdir"]
        for record in result.records
        for event in record.trace
        if event.metadata and "workdir" in event.metadata
    }

    assert len(workdirs) == 2


def _task(id_: str, solve: str, verify: str, timeout_seconds: int = 5) -> Task:
    return Task(
        id=id_,
        split=Split.HELD_IN,
        failure_mode="local_subprocess",
        description=id_,
        metadata={
            "solve_command": solve,
            "verify_command": verify,
            "timeout_seconds": timeout_seconds,
        },
    )
