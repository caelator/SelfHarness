from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from self_harness.types import FailureCategory, TraceEvent

ArtifactValidationStatus = Literal["candidate", "validated", "partial"]


@dataclass(frozen=True)
class HarborArtifactProvenance:
    run_dir: str
    discovered_files: tuple[str, ...]
    validation_status: ArtifactValidationStatus
    missing_required: tuple[str, ...]


@dataclass(frozen=True)
class HarborTrialRecord:
    task_id: str
    attempt_index: int
    passed: bool
    reward_value: float | None
    reward_source: str
    terminal_cause: str
    mechanism: str
    trajectory_events: tuple[TraceEvent, ...]
    field_sources: dict[str, str]
    provenance: HarborArtifactProvenance
    image_digest: object | None = None


def inspect_run_dir(run_dir: Path) -> dict[str, Any]:
    root = Path(run_dir)
    files = []
    if root.is_dir():
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: _rel(root, item)):
            data = path.read_bytes()
            files.append(
                {
                    "path": _rel(root, path),
                    "size_bytes": len(data),
                    "sha256": sha256(data).hexdigest(),
                }
            )
    return {
        "schema_version": "1.0",
        "run_dir": str(root),
        "files": files,
    }


def discover_trials(run_dir: Path) -> list[HarborTrialRecord]:
    root = Path(run_dir)
    if not root.is_dir():
        return []
    trial_dirs = _trial_dirs(root)
    if not trial_dirs:
        trial_dirs = [root]
    records: list[HarborTrialRecord] = []
    for index, trial_dir in enumerate(trial_dirs):
        records.append(_trial_record(root, trial_dir, fallback_attempt=index))
    return records


def parse_reward(path: Path) -> tuple[float | None, str]:
    if not path.exists():
        return None, "missing"
    if path.suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None, "reward.json"
        if isinstance(value, int | float):
            return float(value), "reward.json"
        if isinstance(value, dict):
            for key in ["reward", "score", "pass_rate", "mean"]:
                item = value.get(key)
                if isinstance(item, int | float):
                    return float(item), "reward.json"
            passed = value.get("passed")
            if isinstance(passed, bool):
                return (1.0 if passed else 0.0), "reward.json"
        return None, "reward.json"
    try:
        return float(path.read_text(encoding="utf-8").strip()), "reward.txt"
    except ValueError:
        return None, "reward.txt"


def parse_trajectory_log(path: Path) -> tuple[list[TraceEvent], str]:
    if not path.exists():
        return [], "missing"
    events: list[TraceEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            events.append(TraceEvent(kind="trajectory", message=line))
            continue
        if isinstance(row, dict):
            metadata = {str(key): item for key, item in row.items()}
            events.append(
                TraceEvent(
                    kind=str(row.get("kind", row.get("role", "trajectory"))),
                    message=str(row.get("message", row.get("content", ""))),
                    metadata=metadata,
                )
            )
    return events, "trajectory.jsonl"


def _trial_record(root: Path, trial_dir: Path, *, fallback_attempt: int) -> HarborTrialRecord:
    reward_path = _first_existing([trial_dir / "reward.json", trial_dir / "reward.txt"])
    reward_value, reward_source = parse_reward(reward_path or trial_dir / "reward.json")
    trajectory_path = _first_existing(
        [
            trial_dir / "trajectory.jsonl",
            trial_dir / "agent" / "trajectory.jsonl",
            trial_dir / "agent" / "trajectory.json",
        ]
    )
    trajectory_events, trajectory_source = parse_trajectory_log(trajectory_path or trial_dir / "trajectory.jsonl")
    metadata = _metadata(trial_dir)
    task_id = _task_id(trial_dir, root, metadata=metadata)
    attempt_index = _attempt_index(trial_dir, fallback_attempt)
    image_digest = _image_digest(metadata)
    missing = []
    if reward_source == "missing":
        missing.append("reward")
    if trajectory_source == "missing":
        missing.append("trajectory")
    validation_status: ArtifactValidationStatus = "candidate" if not missing else "partial"
    provenance = HarborArtifactProvenance(
        run_dir=str(root),
        discovered_files=tuple(item["path"] for item in inspect_run_dir(trial_dir)["files"]),
        validation_status=validation_status,
        missing_required=tuple(missing),
    )
    passed = reward_value is not None and reward_value > 0
    terminal_cause = FailureCategory.VERIFIER_PASS.value if passed else FailureCategory.VERIFIER_FAIL.value
    return HarborTrialRecord(
        task_id=task_id,
        attempt_index=attempt_index,
        passed=passed,
        reward_value=reward_value,
        reward_source=reward_source,
        terminal_cause=terminal_cause,
        mechanism="harbor-artifact-reward",
        trajectory_events=tuple(trajectory_events),
        field_sources={
            "passed": reward_source if reward_value is not None else "missing",
            "reward_value": reward_source,
            "terminal_cause": "inferred" if reward_value is not None else "missing",
            "trajectory_events": trajectory_source,
            "image_digest": "metadata.json" if image_digest is not None else "missing",
        },
        provenance=provenance,
        image_digest=image_digest,
    )


def _trial_dirs(root: Path) -> list[Path]:
    markers = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name not in {"reward.json", "reward.txt", "trajectory.jsonl", "trajectory.json"}:
            continue
        markers.append(path.parent)
    return sorted(set(markers), key=lambda item: item.relative_to(root).as_posix())


def _task_id(trial_dir: Path, root: Path, *, metadata: dict[str, Any]) -> str:
    for key in ["task_id", "id", "name"]:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    relative = trial_dir.relative_to(root).parts
    return relative[-1] if relative else root.name


def _metadata(trial_dir: Path) -> dict[str, Any]:
    metadata_path = _first_existing([trial_dir / "metadata.json", trial_dir / "task.json"])
    if metadata_path is None:
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _image_digest(metadata: Mapping[str, Any]) -> object | None:
    for key in ["image_digest", "container_image_digest", "container_digest"]:
        if key in metadata:
            value: object = metadata[key]
            return value
    return None


def _attempt_index(trial_dir: Path, fallback: int) -> int:
    for part in reversed(trial_dir.parts):
        if part.isdigit():
            return int(part)
        if part.startswith("attempt-") and part.removeprefix("attempt-").isdigit():
            return int(part.removeprefix("attempt-"))
    return fallback


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
