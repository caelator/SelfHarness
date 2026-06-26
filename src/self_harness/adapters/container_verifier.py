from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from self_harness.adapters.base import TaskAdapter
from self_harness.adapters.in_process_python import SELECTOR_MAX_LENGTH
from self_harness.adapters.verifier_result import outcome_from_verifier_result
from self_harness.corpus import TaskCorpus
from self_harness.exceptions import ContainerVerifierError, TaskLoadError
from self_harness.image_policy import ImagePolicy, ImagePolicyError, ensure_image_allowed
from self_harness.types import (
    FailureCategory,
    HarnessSpec,
    RunRecord,
    Task,
    TraceEvent,
    VerifierOutcome,
    stable_json_dumps,
)

ContainerMode = Literal["dry-run", "live"]
DISALLOWED_CONTAINER_METADATA_KEYS = {
    "image",
    "container_image",
    "digest",
    "entrypoint",
    "command",
    "image_policy",
    "image_policy_path",
    "docker_args",
    "docker_command",
    "docker_config",
    "env_file",
    "header",
    "headers",
    "registry_auth",
    "registry_password",
    "registry_token",
    "registry_username",
}


@dataclass(frozen=True)
class ContainerCommandSpec:
    image: str
    image_digest: str | None
    command: tuple[str, ...]
    workdir: Path
    env_files: tuple[Path, ...] = field(default_factory=tuple)


def build_container_run_command(spec: ContainerCommandSpec, *, docker_executable: str = "docker") -> list[str]:
    if not spec.image:
        raise ValueError("container image must be non-empty")
    if not spec.command:
        raise ValueError("container command must be non-empty")
    image = f"{spec.image}@{spec.image_digest}" if spec.image_digest else spec.image
    command = [
        docker_executable,
        "run",
        "--rm",
        "--workdir",
        "/work",
        "-v",
        f"{spec.workdir}:/work",
    ]
    for env_file in spec.env_files:
        command.extend(["--env-file", str(env_file)])
    command.append(image)
    command.extend(spec.command)
    return command


@dataclass(frozen=True)
class ContainerVerifierTaskAdapter(TaskAdapter):
    image: str
    image_digest: str | None = None
    command: tuple[str, ...] = ("verify",)
    mode: ContainerMode = "dry-run"
    fixture_dir: Path | None = None
    docker_executable: str = "docker"
    timeout_seconds: float = 30.0
    keep_workdir: bool = False
    extra_env: tuple[tuple[str, str], ...] = ()
    extra_env_files: tuple[Path, ...] = ()
    docker_config_dir: Path | None = None
    image_policy: ImagePolicy | None = None
    require_image_digest: bool = False

    def __post_init__(self) -> None:
        _validate_image_policy(self.image_policy, self.image, self.image_digest, self.require_image_digest)

    def load(self, corpus: TaskCorpus) -> list[Task]:
        tasks = list(corpus.tasks)
        for task in tasks:
            _validate_container_task_metadata(task)
        return tasks

    def runner(self) -> ContainerVerifierRunner:
        return ContainerVerifierRunner(
            image=self.image,
            image_digest=self.image_digest,
            command=self.command,
            mode=self.mode,
            fixture_dir=self.fixture_dir,
            docker_executable=self.docker_executable,
            timeout_seconds=self.timeout_seconds,
            keep_workdir=self.keep_workdir,
            extra_env=self.extra_env,
            extra_env_files=self.extra_env_files,
            docker_config_dir=self.docker_config_dir,
            image_policy=self.image_policy,
            require_image_digest=self.require_image_digest,
        )


@dataclass(frozen=True)
class ContainerVerifierRunner:
    image: str
    image_digest: str | None = None
    command: tuple[str, ...] = ("verify",)
    mode: ContainerMode = "dry-run"
    fixture_dir: Path | None = None
    docker_executable: str = "docker"
    timeout_seconds: float = 30.0
    keep_workdir: bool = False
    extra_env: tuple[tuple[str, str], ...] = ()
    extra_env_files: tuple[Path, ...] = ()
    docker_config_dir: Path | None = None
    image_policy: ImagePolicy | None = None
    require_image_digest: bool = False

    def __post_init__(self) -> None:
        if not self.image:
            raise ContainerVerifierError("trusted container image must be non-empty")
        if not self.command:
            raise ContainerVerifierError("container command must be non-empty")
        if self.mode not in {"dry-run", "live"}:
            raise ContainerVerifierError("container verifier mode must be dry-run or live")
        if self.timeout_seconds <= 0:
            raise ContainerVerifierError("timeout_seconds must be positive")
        _validate_image_policy(self.image_policy, self.image, self.image_digest, self.require_image_digest)
        for key, value in self.extra_env:
            if not key or "\n" in key or "\r" in key or "\n" in value or "\r" in value:
                raise ContainerVerifierError("container environment must use single-line KEY=VALUE pairs")

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        del harness
        _validate_container_task_metadata(task)
        workdir = Path(tempfile.mkdtemp(prefix=f"self-harness-container-{task.id}-{attempt_index}-"))
        trace: list[TraceEvent] = [
            TraceEvent(kind="workspace", message="created fresh workdir", metadata={"workdir": str(workdir)})
        ]
        try:
            _copy_template(task.metadata.get("workspace_template"), workdir)
            env_files = self.extra_env_files
            if self.extra_env:
                env_files = (_write_env_file(workdir / ".self-harness-env", self.extra_env), *env_files)
            spec = ContainerCommandSpec(
                image=self.image,
                image_digest=self.image_digest,
                command=self.command,
                workdir=workdir,
                env_files=env_files,
            )
            command = build_container_run_command(spec, docker_executable=self.docker_executable)
            trace.append(
                TraceEvent(
                    kind="container-command",
                    message="constructed container verifier command",
                    metadata={"argv": _redact_container_command(command), "mode": self.mode},
                )
            )
            if self.mode == "dry-run":
                outcome = _dry_run_outcome(task, self.fixture_dir)
            else:
                docker_env = _docker_parent_env(self.docker_config_dir)
                outcome = _live_outcome(command, timeout_seconds=self.timeout_seconds, docker_env=docker_env)
            return RunRecord(
                task_id=task.id,
                split=task.split,
                passed=outcome.passed,
                trace=trace,
                outcome=outcome,
                attempt_index=attempt_index,
            )
        finally:
            if not self.keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)


def parse_container_command(value: str) -> tuple[str, ...]:
    parsed = tuple(shlex.split(value))
    if not parsed:
        raise ContainerVerifierError("container command must be non-empty")
    return parsed


def _validate_container_task_metadata(task: Task) -> None:
    disallowed = _disallowed_metadata_keys(task.metadata, DISALLOWED_CONTAINER_METADATA_KEYS)
    if disallowed:
        raise TaskLoadError(f"task {task.id} must not carry container verifier trust material in metadata")
    selector = task.metadata.get("verifier_selector")
    if selector is None:
        return
    if not isinstance(selector, str) or not selector or len(selector) > SELECTOR_MAX_LENGTH:
        raise TaskLoadError(f"task {task.id} verifier_selector must be a non-empty string up to 256 characters")


def _copy_template(template: object, workdir: Path) -> None:
    if template is None:
        return
    if not isinstance(template, str):
        raise TaskLoadError("workspace_template must be a string path")
    template_path = Path(template)
    if not template_path.is_dir():
        raise TaskLoadError(f"workspace_template must be a directory: {template}")
    for child in template_path.iterdir():
        target = workdir / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def _dry_run_outcome(task: Task, fixture_dir: Path | None) -> VerifierOutcome:
    fixture = _load_fixture(task, fixture_dir)
    if fixture is None:
        return VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.VERIFIER_FAIL.value,
            causal_status="rejected",
            mechanism="container-dry-run-no-fixture",
            message="container dry-run has no verifier fixture",
        )
    return _outcome_from_result(fixture)


def _load_fixture(task: Task, fixture_dir: Path | None) -> object | None:
    if fixture_dir is None:
        return None
    selector = task.metadata.get("verifier_selector")
    names = [task.id]
    if isinstance(selector, str):
        names.insert(0, selector)
    for name in names:
        path = fixture_dir / f"{name}.json"
        if path.exists():
            return cast(object, json.loads(path.read_text(encoding="utf-8")))
    return None


def _live_outcome(command: list[str], *, timeout_seconds: float, docker_env: dict[str, str] | None) -> VerifierOutcome:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=docker_env,
        )
    except subprocess.TimeoutExpired:
        return VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.TIMEOUT.value,
            causal_status="environment",
            mechanism="container-timeout",
            message="container verifier timed out",
        )
    except OSError as exc:
        return VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value,
            causal_status="environment",
            mechanism="container-exec-error",
            message=exc.__class__.__name__,
        )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value,
            causal_status="environment",
            mechanism="container-nonzero-exit",
            message=message,
        )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ContainerVerifierError("container verifier stdout must be JSON") from exc
    return _outcome_from_result(response)


def _outcome_from_result(result: object) -> VerifierOutcome:
    if not isinstance(result, dict):
        raise ContainerVerifierError("container verifier result must be a JSON object")
    return outcome_from_verifier_result(
        result,
        default_mechanism="container-verifier",
        error_factory=ContainerVerifierError,
    )


def _write_env_file(path: Path, values: tuple[tuple[str, str], ...]) -> Path:
    payload = "".join(f"{key}={value}\n" for key, value in values)
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)
    return path


def _redact_container_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("<redacted-env-file>")
            skip_next = False
            continue
        redacted.append(item)
        if item == "--env-file":
            skip_next = True
    return redacted


def _docker_parent_env(docker_config_dir: Path | None) -> dict[str, str] | None:
    if docker_config_dir is None:
        return None
    env = dict(os.environ)
    env["DOCKER_CONFIG"] = str(docker_config_dir)
    return env


def _disallowed_metadata_keys(metadata: dict[str, object], explicit_keys: set[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for key in metadata:
        lowered = key.lower()
        if (
            lowered in explicit_keys
            or lowered.startswith("registry_")
            or lowered.startswith("auth_")
            or lowered.startswith("secret_")
            or lowered.startswith("tls_")
            or lowered.endswith("_header")
            or lowered.endswith("_headers")
        ):
            keys.append(key)
    return tuple(keys)


def _validate_image_policy(
    policy: ImagePolicy | None,
    image: str,
    image_digest: str | None,
    require_image_digest: bool,
) -> None:
    try:
        ensure_image_allowed(policy, image, image_digest, require_digest=require_image_digest)
    except ImagePolicyError as exc:
        raise ContainerVerifierError(f"container image policy rejected image: {exc.decision.code}") from exc


def fixture_payload(passed: bool, *, failure_category: str | None = None, mechanism: str = "container-fixture") -> str:
    payload = {
        "failure_category": failure_category,
        "mechanism": mechanism,
        "message": "container fixture passed" if passed else "container fixture failed",
        "passed": passed,
    }
    return stable_json_dumps(payload) + "\n"
