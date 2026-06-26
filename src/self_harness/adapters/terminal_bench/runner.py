from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from self_harness.adapters.terminal_bench.agent_adapter import AgentAdapter, DeepAgentAdapter
from self_harness.adapters.terminal_bench.agent_render import render_agent_config
from self_harness.adapters.terminal_bench.harbor_artifacts import HarborTrialRecord, discover_trials
from self_harness.adapters.terminal_bench.harbor_command import HarborCommandSpec, build_harbor_run_command
from self_harness.adapters.terminal_bench.harbor_output import parse_harbor_output
from self_harness.exceptions import EvaluationError, PaperFidelityError
from self_harness.image_policy import (
    ImagePolicy,
    ImagePolicyDecision,
    ImagePolicyError,
    ensure_image_allowed,
    validate_image_digest,
)
from self_harness.types import (
    FailureCategory,
    HarnessSpec,
    RunRecord,
    Task,
    TraceEvent,
    VerifierOutcome,
    stable_json_dumps,
)

RunnerMode = Literal["dry-run", "live"]


@dataclass(frozen=True)
class HarborRunner:
    """Experimental Terminal-Bench/Harbor runner with deterministic dry-run mode."""

    dataset: str
    mode: RunnerMode = "dry-run"
    fixture_dir: Path | None = None
    harbor_executable: str = "harbor"
    corpus_cache: Path | None = None
    model: str = "anthropic/claude-haiku-4-5"
    n_concurrent: int = 1
    cloud_env: str | None = None
    agent_adapter: AgentAdapter = field(default_factory=DeepAgentAdapter)
    keep_run_dir: Path | None = None
    image_policy: ImagePolicy | None = None
    trusted_image: str | None = None
    trusted_image_digest: str | None = None
    require_image_digest: bool = False

    def __post_init__(self) -> None:
        validate_harbor_image_trust(
            self.image_policy,
            trusted_image=self.trusted_image,
            trusted_image_digest=self.trusted_image_digest,
            require_image_digest=self.require_image_digest,
        )

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        if task.failure_mode != "terminal_bench":
            raise EvaluationError(f"HarborRunner only supports terminal_bench tasks, got {task.failure_mode}")
        agent_config = render_agent_config(harness)
        if self.mode == "dry-run":
            return self._dry_run(task, agent_config, attempt_index)
        return self._live_run(task, harness, agent_config, attempt_index)

    def _dry_run(self, task: Task, agent_config: dict[str, Any], attempt_index: int) -> RunRecord:
        fixture_path = self._fixture_path(task)
        fixture = _read_fixture(fixture_path)
        _validate_fixture_task_source(task, fixture, fixture_path)
        passed = _fixture_passed(fixture, agent_config)
        terminal_cause = _fixture_str(
            fixture,
            "terminal_cause",
            FailureCategory.VERIFIER_PASS.value if passed else FailureCategory.VERIFIER_FAIL.value,
        )
        trace = [
            TraceEvent(
                kind="agent-config",
                message="rendered agent config",
                metadata={"config_hash": agent_config["config_hash"]},
            )
        ]
        for event in fixture.get("trace", []):
            if isinstance(event, dict):
                trace.append(
                    TraceEvent(
                        kind=str(event.get("kind", "fixture")),
                        message=str(event.get("message", "")),
                        metadata=event.get("metadata") if isinstance(event.get("metadata"), dict) else None,
                    )
                )
        return RunRecord(
            task_id=task.id,
            split=task.split,
            passed=passed,
            trace=trace,
            outcome=VerifierOutcome(
                passed=passed,
                terminal_cause=terminal_cause,
                causal_status=_fixture_str(fixture, "causal_status", "confirmed" if passed else "rejected"),
                mechanism=_fixture_str(fixture, "mechanism", "dry-run-fixture"),
                message=_fixture_str(fixture, "message", "dry-run verifier replay"),
            ),
            attempt_index=attempt_index,
            metadata=_record_metadata(task),
        )

    def _live_run(
        self,
        task: Task,
        harness: HarnessSpec,
        agent_config: dict[str, Any],
        attempt_index: int,
    ) -> RunRecord:
        with tempfile.TemporaryDirectory(prefix=f"self-harness-harbor-{task.id}-{attempt_index}-") as tmp:
            workdir = Path(tmp)
            invocation = self.agent_adapter.materialize(harness, workdir)
            command = build_harbor_run_command(
                HarborCommandSpec(
                    dataset=self.dataset,
                    agent_name=invocation.agent_name,
                    model=self.model,
                    n_concurrent=self.n_concurrent,
                    cache_dir=self.corpus_cache,
                    cloud_env=self.cloud_env,
                    task_ids=(task.id,),
                    agent_config_path=invocation.config_path,
                ),
                harbor_executable=self.harbor_executable,
            )
            try:
                completed = subprocess.run(command, cwd=workdir, capture_output=True, text=True, check=False)
            except FileNotFoundError:
                return _environment_error_record(
                    task,
                    attempt_index,
                    f"missing Harbor executable: {self.harbor_executable}",
                )
            preserved_run_dir = _preserve_run_dir(workdir, self.keep_run_dir, task.id, attempt_index)
            artifact_record = _artifact_record_for_task(preserved_run_dir, task.id) if preserved_run_dir else None
            parsed = parse_harbor_output(
                completed.stdout,
                completed.stderr,
                returncode=completed.returncode,
                task_id=task.id,
            )
            validate_harbor_live_container_digest(
                self.image_policy,
                trusted_image=self.trusted_image,
                trusted_image_digest=self.trusted_image_digest,
                parsed_digest=parsed.container_digest,
                require_image_digest=self.require_image_digest,
            )
            return RunRecord(
                task_id=task.id,
                split=task.split,
                passed=artifact_record.passed if artifact_record else parsed.passed,
                trace=[
                    TraceEvent(
                        kind="agent-config",
                        message="rendered agent config",
                        metadata={
                            "config_hash": agent_config["config_hash"],
                            "agent": invocation.agent_name,
                            "config_path": str(invocation.config_path) if invocation.config_path else None,
                        },
                    ),
                    TraceEvent(
                        kind="harbor",
                        message=f"harbor exited {completed.returncode}",
                        metadata={
                            "command": command,
                            "stdout": completed.stdout[:4096],
                            "stderr": completed.stderr[:4096],
                            "trace_path": parsed.trace_path,
                        },
                    ),
                    *_artifact_trace_events(artifact_record),
                ],
                outcome=VerifierOutcome(
                    passed=artifact_record.passed if artifact_record else parsed.passed,
                    terminal_cause=artifact_record.terminal_cause if artifact_record else parsed.terminal_cause,
                    causal_status=parsed.causal_status,
                    mechanism=artifact_record.mechanism if artifact_record else parsed.mechanism,
                    message=parsed.verifier_output or _harbor_status_message(parsed.passed, completed.returncode),
                ),
                attempt_index=attempt_index,
                metadata=_record_metadata(task, parsed.container_digest, artifact_record),
            )

    def _fixture_path(self, task: Task) -> Path:
        if self.fixture_dir is None:
            raise EvaluationError("dry-run HarborRunner requires fixture_dir")
        fixture_name = task.metadata.get("dry_run_fixture")
        if isinstance(fixture_name, str) and fixture_name:
            return self.fixture_dir / fixture_name
        return self.fixture_dir / f"{task.id}.json"


def validate_harbor_image_trust(
    policy: ImagePolicy | None,
    *,
    trusted_image: str | None,
    trusted_image_digest: str | None,
    require_image_digest: bool,
) -> None:
    if trusted_image_digest is not None:
        validate_image_digest(trusted_image_digest)
    if policy is not None:
        if not trusted_image:
            raise _image_policy_error("image-missing", "--trust-container-image is required with --image-policy")
        ensure_image_allowed(policy, trusted_image, trusted_image_digest, require_digest=require_image_digest)
    elif require_image_digest and trusted_image_digest is None:
        raise _image_policy_error(
            "missing-digest",
            "--trust-container-image-digest is required with --require-image-digest",
        )


def validate_harbor_live_container_digest(
    policy: ImagePolicy | None,
    *,
    trusted_image: str | None,
    trusted_image_digest: str | None,
    parsed_digest: str | None,
    require_image_digest: bool,
) -> None:
    if trusted_image_digest is not None:
        if parsed_digest is None:
            raise _image_policy_error("missing-digest", "Harbor output did not include a container image digest")
        if parsed_digest != trusted_image_digest:
            raise _image_policy_error("digest-mismatch", "Harbor container image digest did not match trusted digest")
    if policy is not None:
        if not trusted_image:
            raise _image_policy_error("image-missing", "--trust-container-image is required with --image-policy")
        ensure_image_allowed(policy, trusted_image, parsed_digest, require_digest=require_image_digest)
    elif require_image_digest and parsed_digest is None:
        raise _image_policy_error("missing-digest", "Harbor output did not include a container image digest")


def _read_fixture(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluationError(f"missing dry-run Harbor fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"invalid dry-run Harbor fixture JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"dry-run Harbor fixture must be an object: {path}")
    return value


def _fixture_str(fixture: dict[str, Any], key: str, default: str) -> str:
    value = fixture.get(key, default)
    return value if isinstance(value, str) and value else default


def _fixture_passed(fixture: dict[str, Any], agent_config: dict[str, Any]) -> bool:
    required = fixture.get("pass_if_config_contains")
    if isinstance(required, str):
        return required in stable_json_dumps(agent_config)
    if isinstance(required, list) and all(isinstance(item, str) for item in required):
        config_text = stable_json_dumps(agent_config)
        return all(item in config_text for item in required)
    return bool(fixture.get("passed"))


def _harbor_status_message(passed: bool, returncode: int) -> str:
    return "harbor run passed" if passed else f"harbor run failed with exit {returncode}"


def _validate_fixture_task_source(task: Task, fixture: dict[str, Any], fixture_path: Path) -> None:
    fixture_task_id = fixture.get("task_id")
    if isinstance(fixture_task_id, str) and fixture_task_id != task.id:
        raise PaperFidelityError(
            f"dry-run fixture task_id mismatch for {fixture_path}: expected {task.id}, got {fixture_task_id}"
        )
    captured_fixture = fixture.get("capture_source") == "single-task-harbor-run"
    actual_hash = fixture.get("task_source_hash")
    if not captured_fixture and actual_hash is None:
        return
    expected_hash = task.metadata.get("task_source_hash")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise PaperFidelityError(f"task {task.id} is missing task_source_hash required for captured fixture replay")
    if not isinstance(actual_hash, str) or not actual_hash:
        raise PaperFidelityError(f"captured dry-run fixture is missing task_source_hash: {fixture_path}")
    if actual_hash != expected_hash:
        raise PaperFidelityError(
            f"captured dry-run fixture task_source_hash mismatch for {task.id}: "
            f"expected {expected_hash}, got {actual_hash}"
        )


def _record_metadata(
    task: Task,
    container_digest: str | None = None,
    artifact_record: HarborTrialRecord | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    source_hash = task.metadata.get("task_source_hash")
    if isinstance(source_hash, str):
        metadata["task_source_hash"] = source_hash
    if container_digest is not None:
        metadata["container_image_digest"] = container_digest
    if artifact_record is not None:
        metadata["harbor_artifact_provenance"] = artifact_record.provenance
        metadata["reward_value"] = artifact_record.reward_value
        metadata["reward_source"] = artifact_record.reward_source
        metadata["trajectory_event_count"] = len(artifact_record.trajectory_events)
    return metadata


def _preserve_run_dir(workdir: Path, keep_run_dir: Path | None, task_id: str, attempt_index: int) -> Path | None:
    if keep_run_dir is None:
        return None
    destination = keep_run_dir / task_id / str(attempt_index)
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(workdir, destination)
    return destination


def _artifact_record_for_task(run_dir: Path, task_id: str) -> HarborTrialRecord | None:
    records = discover_trials(run_dir)
    for record in records:
        if record.task_id == task_id:
            return record
    return records[0] if records else None


def _artifact_trace_events(record: HarborTrialRecord | None) -> list[TraceEvent]:
    if record is None:
        return []
    return [
        TraceEvent(
            kind="harbor-artifacts",
            message="parsed preserved Harbor artifacts",
            metadata={
                "validation_status": record.provenance.validation_status,
                "missing_required": list(record.provenance.missing_required),
                "reward_source": record.reward_source,
            },
        ),
        *record.trajectory_events,
    ]


def _image_policy_error(code: str, message: str) -> ImagePolicyError:
    return ImagePolicyError(ImagePolicyDecision(allowed=False, code=code, message=message))


def _environment_error_record(task: Task, attempt_index: int, message: str) -> RunRecord:
    return RunRecord(
        task_id=task.id,
        split=task.split,
        passed=False,
        trace=[TraceEvent(kind="harbor", message=message)],
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value,
            causal_status="environment",
            mechanism="missing-harbor-executable",
            message=message,
        ),
        attempt_index=attempt_index,
        metadata=_record_metadata(task),
    )
