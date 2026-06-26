from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from self_harness.adapters.base import TaskAdapter
from self_harness.adapters.terminal_bench.provenance import source_hash
from self_harness.corpus import CORPUS_VERSION, TaskCorpus, TaskLoadReason
from self_harness.exceptions import TaskLoadError
from self_harness.types import Split, Task

FAILURE_MODE = "terminal_bench"


@dataclass(frozen=True)
class TerminalBenchCorpusAdapter(TaskAdapter):
    """Experimental ingestion adapter for Terminal-Bench-shaped manifests."""

    manifest_path: Path

    def corpus(self) -> TaskCorpus:
        return load_terminal_bench_manifest(self.manifest_path)

    def load(self, corpus: TaskCorpus) -> list[Task]:
        return list(corpus.tasks)

    def runner(self) -> NoReturn:  # pragma: no cover - caller supplies HarborRunner explicitly.
        raise NotImplementedError("TerminalBenchCorpusAdapter only handles ingestion; use HarborRunner for execution")


def load_terminal_bench_manifest(path: Path) -> TaskCorpus:
    data = _read_manifest(path)
    dataset_id = _required_str(data, "dataset")
    dataset_version = _required_str(data, "dataset_version")
    tasks_raw = data.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise TaskLoadError(
            "Terminal-Bench manifest must include a non-empty tasks list",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    tasks = [_task_from_row(row, index, dataset_id, dataset_version) for index, row in enumerate(tasks_raw)]
    return TaskCorpus(
        corpus_version=CORPUS_VERSION,
        corpus_id=f"{dataset_id}@{dataset_version}",
        tasks=tasks,
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskLoadError(
            f"missing Terminal-Bench manifest: {path}",
            reason=TaskLoadReason.MISSING_FILE.value,
        ) from exc
    except json.JSONDecodeError as exc:
        raise TaskLoadError(
            f"invalid Terminal-Bench manifest JSON: {path}",
            reason=TaskLoadReason.INVALID_JSON.value,
        ) from exc
    if not isinstance(value, dict):
        raise TaskLoadError(
            "Terminal-Bench manifest must be a JSON object",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    return value


def _task_from_row(row: object, index: int, dataset_id: str, dataset_version: str) -> Task:
    if not isinstance(row, dict):
        raise TaskLoadError(
            f"Terminal-Bench task row {index} must be an object",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    task_id = _required_str(row, "id")
    split = Split(_required_str(row, "split"))
    instruction = _required_str(row, "instruction")
    verifier_script = _required_str(row, "verifier_script")
    oracle_solution = _required_str(row, "oracle_solution")
    metadata = {
        "benchmark_protocol": "terminal-bench@2.0",
        "benchmark_dataset": dataset_id,
        "benchmark_dataset_version": dataset_version,
        "instruction": instruction,
        "verifier_script": verifier_script,
        "oracle_solution": oracle_solution,
        "task_source_hash": source_hash(row),
    }
    fixture = row.get("dry_run_fixture")
    if isinstance(fixture, str) and fixture:
        metadata["dry_run_fixture"] = fixture
    return Task(
        id=task_id,
        split=split,
        failure_mode=FAILURE_MODE,
        description=instruction,
        metadata=metadata,
    )


def _required_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise TaskLoadError(
            f"Terminal-Bench manifest missing string field: {key}",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    return value
