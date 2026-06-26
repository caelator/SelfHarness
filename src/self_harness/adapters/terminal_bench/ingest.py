from __future__ import annotations

from collections import Counter
from pathlib import Path

from self_harness.adapters.terminal_bench.corpus import load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.harbor_artifacts import HarborTrialRecord, discover_trials
from self_harness.harness import EDITABLE_SURFACES, OP_WHITELIST, SURFACE_KINDS, harness_hash, initial_harness
from self_harness.types import Split, write_jsonl, write_stable_json

HARBOR_INGEST_SCHEMA_VERSION = "1.4"


def ingest_harbor_run(
    run_dir: Path,
    manifest: Path,
    out_dir: Path,
    *,
    dataset: str = "terminal-bench@2.0",
) -> Path:
    corpus = load_terminal_bench_manifest(manifest)
    split_by_task = {task.id: task.split for task in corpus.tasks}
    records = discover_trials(run_dir)
    unknown = sorted({record.task_id for record in records if record.task_id not in split_by_task})
    if unknown:
        raise ValueError(f"artifact task id(s) missing from manifest: {', '.join(unknown)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rounds" / "0").mkdir(parents=True, exist_ok=True)
    validation_status = _overall_status(records)
    evaluation_repeats = _evaluation_repeats(records)
    manifest_row = {
        "protocol_hash": "harbor-artifact-ingest-v1",
        "protocol_version": "harbor-artifact-ingest-v1",
        "schema_version": HARBOR_INGEST_SCHEMA_VERSION,
        "model_id": "harbor-artifact-ingest",
        "decoding_budget": {},
        "evaluation_repeats": evaluation_repeats,
        "seed": 0,
        "surface_whitelist": sorted(EDITABLE_SURFACES),
        "surface_kinds": {surface: SURFACE_KINDS[surface] for surface in sorted(SURFACE_KINDS)},
        "op_whitelist": sorted(OP_WHITELIST),
        "benchmark_protocol": dataset,
        "benchmark_dataset_version": corpus.corpus_id,
        "benchmark_dataset": corpus.corpus_id,
        "harbor_version": "artifact-ingest",
        "container_image_digest": "not-recorded",
        "reproduction_claimed": False,
        "harbor_artifact_validation_status": validation_status,
    }
    harness = initial_harness()
    lineage = [
        {
            "round": 0,
            "harness_before_hash": harness_hash(harness),
            "harness_after_hash": harness_hash(harness),
            "ops_applied": [],
            "reverse_ops": [],
            "accepted_proposal_ids": [],
            "schema_version": HARBOR_INGEST_SCHEMA_VERSION,
        }
    ]
    round_dir = out_dir / "rounds" / "0"
    write_stable_json(out_dir / "manifest.json", manifest_row)
    write_stable_json(out_dir / "lineage.json", lineage)
    write_stable_json(round_dir / "harness_before.json", harness)
    write_stable_json(round_dir / "harness_after.json", harness)
    write_jsonl(round_dir / "proposals.jsonl", [])
    write_jsonl(round_dir / "evaluations.jsonl", _evaluation_rows(records, split_by_task, evaluation_repeats))
    return out_dir


def _evaluation_rows(
    records: list[HarborTrialRecord],
    split_by_task: dict[str, Split],
    evaluation_repeats: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    totals: Counter[Split] = Counter()
    passes: Counter[Split] = Counter()
    sorted_records = sorted(
        records,
        key=lambda item: (split_by_task[item.task_id].value, item.task_id, item.attempt_index),
    )
    for record in sorted_records:
        split = split_by_task[record.task_id]
        totals[split] += 1
        if record.passed:
            passes[split] += 1
        rows.append(
            {
                "proposal_id": "__baseline__",
                "schema_version": HARBOR_INGEST_SCHEMA_VERSION,
                "split": split.value,
                "task_id": record.task_id,
                "attempt_index": record.attempt_index,
                "arm": "baseline",
                "verifier_pass": 1 if record.passed else 0,
                "verifier_fail": 0 if record.passed else 1,
                "terminal_cause": record.terminal_cause,
                "failure_category": record.terminal_cause,
                "mechanism": record.mechanism,
                "evaluation_repeats": evaluation_repeats,
                "harbor_artifact_provenance": record.provenance,
                "reward_value": record.reward_value,
                "reward_source": record.reward_source,
                "trajectory_event_count": len(record.trajectory_events),
            }
        )
    for split in [Split.HELD_IN, Split.HELD_OUT]:
        total = totals[split]
        passed = passes[split]
        rows.append(
            {
                "proposal_id": "__baseline__",
                "schema_version": HARBOR_INGEST_SCHEMA_VERSION,
                "split": split.value,
                "task_id": "__split_total__",
                "attempt_index": None,
                "arm": "baseline",
                "verifier_pass": passed,
                "verifier_fail": total - passed,
                "score": 0.0 if total == 0 else passed / total,
                "terminal_cause": None,
                "failure_category": None,
                "mechanism": None,
                "evaluation_repeats": evaluation_repeats,
            }
        )
    return rows


def _overall_status(records: list[HarborTrialRecord]) -> str:
    if not records:
        return "partial"
    if any(record.provenance.validation_status == "partial" for record in records):
        return "partial"
    return "candidate"


def _evaluation_repeats(records: list[HarborTrialRecord]) -> int:
    if not records:
        return 1
    by_task: Counter[str] = Counter(record.task_id for record in records)
    return max(by_task.values())
