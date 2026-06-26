from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import time

from self_harness.adapters.terminal_bench.corpus import load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.runner import HarborRunner
from self_harness.exceptions import PaperFidelityError
from self_harness.harness import initial_harness
from self_harness.types import TraceEvent, write_stable_json


@dataclass(frozen=True)
class CaptureManifest:
    corpus_id: str
    task_id: str
    task_source_hash: str
    fixture_path: str
    harbor_version: str
    container_image_digest: str
    benchmark_protocol: str = "terminal-bench@2.0"
    capture_kind: str = "single-task"
    capture_source: str = "single-task-harbor-run"
    reproduction_claimed: bool = False


def capture_single_task(
    dataset: str,
    manifest: Path,
    task_id: str,
    fixture_out_dir: Path,
    *,
    harbor_executable: str = "harbor",
    corpus_cache: Path | None = None,
) -> CaptureManifest:
    corpus = load_terminal_bench_manifest(manifest)
    task = next((item for item in corpus.tasks if item.id == task_id), None)
    if task is None:
        raise ValueError(f"task not found in manifest: {task_id}")
    task_source_hash = task.metadata.get("task_source_hash")
    if not isinstance(task_source_hash, str) or not task_source_hash:
        raise PaperFidelityError(f"Terminal-Bench task {task.id} is missing task_source_hash")
    runner = HarborRunner(
        dataset=dataset,
        mode="live",
        harbor_executable=harbor_executable,
        corpus_cache=corpus_cache,
    )
    record = runner.run(task, initial_harness(), attempt_index=0)
    fixture_out_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_out_dir / f"{task.id}.json"
    write_stable_json(
        fixture_path,
        {
            "passed": record.passed,
            "terminal_cause": record.outcome.terminal_cause,
            "causal_status": record.outcome.causal_status,
            "mechanism": record.outcome.mechanism,
            "message": record.outcome.message,
            "trace": [_trace_event_row(event) for event in record.trace],
            "benchmark_protocol": dataset,
            "corpus_id": corpus.corpus_id,
            "task_id": task.id,
            "task_source_hash": task_source_hash,
            "capture_source": "single-task-harbor-run",
            "harbor_version": "captured-live",
            "container_image_digest_or_unknown": "unknown",
            "reproduction_claimed": False,
            "captured_at_epoch": int(time()),
        },
    )
    capture_manifest = CaptureManifest(
        corpus_id=corpus.corpus_id,
        task_id=task.id,
        task_source_hash=task_source_hash,
        fixture_path=str(fixture_path),
        harbor_version="captured-live",
        container_image_digest="unknown",
    )
    write_stable_json(fixture_out_dir / "capture_manifest.json", capture_manifest)
    return capture_manifest


def validate_capture_claims(value: object) -> None:
    import json

    if isinstance(value, Path):
        loaded = json.loads(value.read_text(encoding="utf-8"))
        validate_capture_claims(loaded)
        return
    if not isinstance(value, dict):
        raise ValueError("capture claim artifact must be an object")
    if value.get("benchmark_protocol") == "terminal-bench@2.0" and value.get("reproduction_claimed") is True:
        raise PaperFidelityError("terminal-bench@2.0 capture artifacts may not claim reproduction")


def _trace_event_row(event: TraceEvent) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": event.kind,
        "message": event.message,
    }
    if event.metadata is not None:
        row["metadata"] = event.metadata
    return row
