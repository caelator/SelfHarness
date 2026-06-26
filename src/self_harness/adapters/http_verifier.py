from __future__ import annotations

import json
import shutil
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from self_harness.adapters.base import TaskAdapter
from self_harness.adapters.in_process_python import SELECTOR_MAX_LENGTH
from self_harness.adapters.verifier_result import outcome_from_verifier_result
from self_harness.corpus import TaskCorpus
from self_harness.exceptions import HttpVerifierError, TaskLoadError
from self_harness.types import (
    FailureCategory,
    HarnessSpec,
    RunRecord,
    Task,
    TraceEvent,
    VerifierOutcome,
    stable_json_dumps,
)

DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
DISALLOWED_URL_METADATA_KEYS = {
    "auth_header",
    "auth_headers",
    "ca_bundle",
    "client_cert",
    "client_key",
    "endpoint",
    "header",
    "headers",
    "secret_header",
    "secret_headers",
    "tls_ca_bundle",
    "tls_client_cert",
    "tls_client_key",
    "url",
    "verifier_endpoint",
    "verifier_url",
}


@dataclass(frozen=True)
class HttpVerifierTaskAdapter(TaskAdapter):
    """Load tasks for an operator-supplied trusted HTTP verifier endpoint."""

    verifier_url: str
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    keep_workdir: bool = False
    extra_headers: tuple[tuple[str, str], ...] = ()
    tls_ca_bundle: Path | None = None
    tls_client_cert: Path | None = None
    tls_client_key: Path | None = None

    def load(self, corpus: TaskCorpus) -> list[Task]:
        tasks = list(corpus.tasks)
        for task in tasks:
            _validate_http_task_metadata(task)
        return tasks

    def runner(self) -> HttpVerifierRunner:
        return HttpVerifierRunner(
            verifier_url=self.verifier_url,
            timeout_seconds=self.timeout_seconds,
            keep_workdir=self.keep_workdir,
            extra_headers=self.extra_headers,
            tls_ca_bundle=self.tls_ca_bundle,
            tls_client_cert=self.tls_client_cert,
            tls_client_key=self.tls_client_key,
        )


@dataclass(frozen=True)
class HttpVerifierRunner:
    """POST structured task attempts to a trusted verifier endpoint."""

    verifier_url: str
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    keep_workdir: bool = False
    extra_headers: tuple[tuple[str, str], ...] = ()
    tls_ca_bundle: Path | None = None
    tls_client_cert: Path | None = None
    tls_client_key: Path | None = None

    def __post_init__(self) -> None:
        if not self.verifier_url:
            raise HttpVerifierError("trusted verifier URL must be non-empty")
        if self.timeout_seconds <= 0:
            raise HttpVerifierError("timeout_seconds must be positive")
        if (self.tls_client_cert is None) != (self.tls_client_key is None):
            raise HttpVerifierError("HTTP verifier mTLS requires both client cert and client key")
        for key, value in self.extra_headers:
            if not key or "\n" in key or "\r" in key or "\n" in value or "\r" in value:
                raise HttpVerifierError("HTTP verifier headers must be single-line KEY: VALUE pairs")

    def run(self, task: Task, harness: HarnessSpec, attempt_index: int = 0) -> RunRecord:
        del harness
        _validate_http_task_metadata(task)
        workdir = Path(tempfile.mkdtemp(prefix=f"self-harness-http-{task.id}-{attempt_index}-"))
        trace: list[TraceEvent] = [
            TraceEvent(kind="workspace", message="created fresh workdir", metadata={"workdir": str(workdir)})
        ]
        try:
            _copy_template(task.metadata.get("workspace_template"), workdir)
            request_body = _request_body(task, workdir, attempt_index)
            try:
                ssl_context = _build_ssl_context(
                    ca_bundle=self.tls_ca_bundle,
                    client_cert=self.tls_client_cert,
                    client_key=self.tls_client_key,
                )
                response = _post_json(
                    self.verifier_url,
                    request_body,
                    timeout_seconds=self.timeout_seconds,
                    extra_headers=self.extra_headers,
                    ssl_context=ssl_context,
                )
            except TimeoutError as exc:
                trace.append(_transport_event("verify", "timeout"))
                return _transport_record(
                    task,
                    attempt_index,
                    trace,
                    FailureCategory.TIMEOUT,
                    "http-timeout",
                    exc.__class__.__name__,
                )
            except urllib.error.HTTPError as exc:
                trace.append(_transport_event("verify", "http-status-error", status_code=exc.code))
                return _transport_record(
                    task,
                    attempt_index,
                    trace,
                    FailureCategory.ENVIRONMENT_ERROR,
                    "http-status-error",
                    f"HTTP {exc.code}",
                )
            except urllib.error.URLError as exc:
                mechanism = "tls-error" if _url_error_is_tls(exc, self.verifier_url) else "url-error"
                outcome_mechanism = "http-tls-error" if mechanism == "tls-error" else "http-url-error"
                reason = exc.reason
                return _transport_record(
                    task,
                    attempt_index,
                    [*trace, _transport_event("verify", mechanism)],
                    FailureCategory.ENVIRONMENT_ERROR,
                    outcome_mechanism,
                    reason.__class__.__name__,
                )
            except ssl.SSLError as exc:
                trace.append(_transport_event("verify", "tls-error"))
                return _transport_record(
                    task,
                    attempt_index,
                    trace,
                    FailureCategory.ENVIRONMENT_ERROR,
                    "http-tls-error",
                    exc.__class__.__name__,
                )
            outcome = _outcome_from_response(response)
            trace.append(
                TraceEvent(
                    kind="verify",
                    message="HTTP verifier completed",
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


def _validate_http_task_metadata(task: Task) -> None:
    disallowed = _disallowed_metadata_keys(task.metadata, DISALLOWED_URL_METADATA_KEYS)
    if disallowed:
        raise TaskLoadError(f"task {task.id} must not carry HTTP verifier trust material in metadata")
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


def _request_body(task: Task, workdir: Path, attempt_index: int) -> dict[str, object]:
    selector = task.metadata.get("verifier_selector")
    return {
        "attempt_index": attempt_index,
        "split": task.split.value,
        "task_id": task.id,
        "task_metadata": _jsonable_metadata(task.metadata),
        "verifier_selector": selector if isinstance(selector, str) else None,
        "workdir": str(workdir),
    }


def _jsonable_metadata(metadata: dict[str, Any]) -> dict[str, object]:
    try:
        json.loads(stable_json_dumps(metadata))
    except (TypeError, ValueError) as exc:
        raise TaskLoadError("task metadata must be JSON serializable for HTTP verifier requests") from exc
    return dict(metadata)


def _post_json(
    url: str,
    body: dict[str, object],
    *,
    timeout_seconds: float,
    extra_headers: tuple[tuple[str, str], ...],
    ssl_context: ssl.SSLContext | None,
) -> object:
    payload = stable_json_dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    headers.update(dict(extra_headers))
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl_context) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise TimeoutError("HTTP verifier timed out") from exc
    except json.JSONDecodeError as exc:
        raise HttpVerifierError("HTTP verifier response must be JSON") from exc


def _outcome_from_response(response: object) -> VerifierOutcome:
    if not isinstance(response, dict):
        raise HttpVerifierError("HTTP verifier response must be a JSON object")
    return outcome_from_verifier_result(
        response,
        default_mechanism="http-verifier",
        error_factory=HttpVerifierError,
    )


def _transport_event(kind: str, mechanism: str, *, status_code: int | None = None) -> TraceEvent:
    metadata: dict[str, object] = {"mechanism": mechanism}
    if status_code is not None:
        metadata["status_code"] = status_code
    return TraceEvent(kind=kind, message=f"HTTP verifier transport failure: {mechanism}", metadata=metadata)


def _transport_record(
    task: Task,
    attempt_index: int,
    trace: list[TraceEvent],
    category: FailureCategory,
    mechanism: str,
    message: str,
) -> RunRecord:
    return RunRecord(
        task_id=task.id,
        split=task.split,
        passed=False,
        trace=trace,
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=category.value,
            causal_status="environment",
            mechanism=mechanism,
            message=message,
        ),
        attempt_index=attempt_index,
    )


def _url_error_is_tls(exc: urllib.error.URLError, verifier_url: str | None = None) -> bool:
    return _transport_reason_is_tls(exc.reason, assume_tls_transport=_is_https_url(verifier_url))


def _transport_reason_is_tls(
    reason: object,
    seen: set[int] | None = None,
    *,
    assume_tls_transport: bool = False,
) -> bool:
    seen = seen or set()
    if id(reason) in seen:
        return False
    seen.add(id(reason))
    if isinstance(reason, ssl.SSLError):
        return True
    reason_name = reason.__class__.__name__.lower()
    reason_text = str(reason).lower()
    tls_markers = ("ssl", "tls", "certificate", "handshake", "eof occurred in violation of protocol")
    if any(marker in reason_name or marker in reason_text for marker in tls_markers):
        return True
    if isinstance(reason, BaseException):
        nested = [
            nested_reason
            for nested_reason in (reason.__cause__, reason.__context__, *reason.args)
            if nested_reason is not None
        ]
        if any(
            _transport_reason_is_tls(
                nested_reason,
                seen,
                assume_tls_transport=assume_tls_transport,
            )
            for nested_reason in nested
        ):
            return True
    if assume_tls_transport and reason_name == "brokenpipeerror":
        return True
    return reason_name in {"connectionreseterror", "remotedisconnected", "connectionabortederror"}


def _is_https_url(value: str | None) -> bool:
    return value is not None and value.lower().startswith("https://")


def _build_ssl_context(
    *,
    ca_bundle: Path | None,
    client_cert: Path | None,
    client_key: Path | None,
) -> ssl.SSLContext | None:
    if ca_bundle is None and client_cert is None and client_key is None:
        return None
    if (client_cert is None) != (client_key is None):
        raise HttpVerifierError("HTTP verifier mTLS requires both client cert and client key")
    try:
        context = ssl.create_default_context(cafile=str(ca_bundle) if ca_bundle is not None else None)
        if client_cert is not None and client_key is not None:
            context.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
    except (OSError, ssl.SSLError) as exc:
        raise HttpVerifierError("HTTP verifier TLS material could not be loaded") from exc
    return context


def _disallowed_metadata_keys(metadata: dict[str, Any], explicit_keys: set[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for key in metadata:
        lowered = key.lower()
        if (
            lowered in explicit_keys
            or lowered.startswith("tls_")
            or lowered.startswith("auth_")
            or lowered.startswith("secret_")
            or lowered.endswith("_header")
            or lowered.endswith("_headers")
        ):
            keys.append(key)
    return tuple(keys)
