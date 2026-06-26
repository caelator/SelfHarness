from __future__ import annotations

import importlib
import importlib.util
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from self_harness.adapters.base import TaskAdapter
from self_harness.adapters.verifier_result import VerifierResult, outcome_from_verifier_result
from self_harness.corpus import TaskCorpus
from self_harness.exceptions import InProcessVerifierError, TaskLoadError
from self_harness.types import FailureCategory, HarnessSpec, RunRecord, Task, TraceEvent, VerifierOutcome

SELECTOR_MAX_LENGTH = 256


class InProcessVerifier(Protocol):
    def __call__(self, task: Task, workdir: Path, attempt_index: int) -> VerifierResult | Mapping[str, object]:
        ...


class InProcessSetupHook(Protocol):
    def __call__(self, task: Task, workdir: Path, attempt_index: int) -> None:
        ...


@dataclass(frozen=True)
class InProcessPythonTaskAdapter(TaskAdapter):
    """Load tasks for a trusted in-process Python verifier module."""

    module_path: str
    verifier_symbol: str = "verify"
    setup_symbol: str | None = "setup"
    keep_workdir: bool = False

    def load(self, corpus: TaskCorpus) -> list[Task]:
        return list(corpus.tasks)

    def runner(self) -> InProcessPythonRunner:
        module = load_trusted_module(self.module_path)
        return InProcessPythonRunner(
            verifier=_load_verifier(module, self.verifier_symbol),
            setup_hook=_load_setup_hook(module, self.setup_symbol),
            keep_workdir=self.keep_workdir,
        )


@dataclass(frozen=True)
class InProcessPythonRunner:
    """Run trusted Python verifier callables in a fresh workspace per attempt."""

    verifier: InProcessVerifier
    setup_hook: InProcessSetupHook | None = None
    keep_workdir: bool = False

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        del harness
        _validate_verifier_selector(task)
        workdir = Path(tempfile.mkdtemp(prefix=f"self-harness-python-{task.id}-{attempt_index}-"))
        trace: list[TraceEvent] = [
            TraceEvent(kind="workspace", message="created fresh workdir", metadata={"workdir": str(workdir)})
        ]
        try:
            _copy_template(task.metadata.get("workspace_template"), workdir)
            if self.setup_hook is not None:
                try:
                    self.setup_hook(task, workdir, attempt_index)
                except Exception as exc:  # noqa: BLE001 - trusted verifier failures become verifier outcomes.
                    trace.append(_exception_event("setup", exc))
                    return _exception_record(task, attempt_index, trace, exc)
                trace.append(TraceEvent(kind="setup", message="setup hook completed", metadata={"symbol": "setup"}))
            try:
                result = self.verifier(task, workdir, attempt_index)
            except Exception as exc:  # noqa: BLE001 - trusted verifier failures become verifier outcomes.
                trace.append(_exception_event("verify", exc))
                return _exception_record(task, attempt_index, trace, exc)
            outcome = _outcome_from_result(result)
            trace.append(
                TraceEvent(
                    kind="verify",
                    message="in-process verifier completed",
                    metadata={
                        "passed": outcome.passed,
                        "terminal_cause": outcome.terminal_cause,
                        "mechanism": outcome.mechanism,
                    },
                )
            )
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


def load_trusted_module(path_or_dotted: str) -> ModuleType:
    if not path_or_dotted:
        raise InProcessVerifierError("trusted verifier module path must be non-empty")
    candidate_path = Path(path_or_dotted)
    if candidate_path.exists():
        if not candidate_path.is_file():
            raise InProcessVerifierError(f"trusted verifier module must be a file: {path_or_dotted}")
        spec = importlib.util.spec_from_file_location(
            f"_self_harness_trusted_verifier_{abs(hash(candidate_path.resolve()))}",
            candidate_path,
        )
        if spec is None or spec.loader is None:
            raise InProcessVerifierError(f"trusted verifier module could not be loaded: {path_or_dotted}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    try:
        return importlib.import_module(path_or_dotted)
    except ImportError as exc:
        raise InProcessVerifierError(f"trusted verifier module could not be imported: {path_or_dotted}") from exc


def _load_verifier(module: ModuleType, symbol: str) -> InProcessVerifier:
    if not symbol:
        raise InProcessVerifierError("verifier symbol must be non-empty")
    value = getattr(module, symbol, None)
    if not callable(value):
        raise InProcessVerifierError(f"trusted verifier module missing callable: {symbol}")
    return cast(InProcessVerifier, value)


def _load_setup_hook(module: ModuleType, symbol: str | None) -> InProcessSetupHook | None:
    if symbol is None:
        return None
    if not symbol:
        raise InProcessVerifierError("setup symbol must be non-empty when provided")
    value = getattr(module, symbol, None)
    if value is None:
        return None
    if not callable(value):
        raise InProcessVerifierError(f"trusted verifier setup symbol is not callable: {symbol}")
    return cast(InProcessSetupHook, value)


def _validate_verifier_selector(task: Task) -> None:
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


def _outcome_from_result(raw_result: VerifierResult | Mapping[str, object]) -> VerifierOutcome:
    return outcome_from_verifier_result(
        raw_result,
        default_mechanism="in-process-verifier",
        error_factory=InProcessVerifierError,
    )


def _exception_event(kind: str, exc: Exception) -> TraceEvent:
    return TraceEvent(
        kind=kind,
        message=f"{kind} hook raised {exc.__class__.__name__}",
        metadata={"exception_type": exc.__class__.__name__},
    )


def _exception_record(task: Task, attempt_index: int, trace: list[TraceEvent], exc: Exception) -> RunRecord:
    return RunRecord(
        task_id=task.id,
        split=task.split,
        passed=False,
        trace=trace,
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value,
            causal_status="environment",
            mechanism="verifier-exception",
            message=exc.__class__.__name__,
        ),
        attempt_index=attempt_index,
    )
