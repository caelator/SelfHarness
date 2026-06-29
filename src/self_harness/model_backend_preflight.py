from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from self_harness.adapters.llm.paper_models import (
    GLM5_SPEC,
    MINIMAX_M25_SPEC,
    QWEN35_35B_A3B_SPEC,
    ChatCompletionTransport,
    GLMClient,
    MiniMaxClient,
    OpenAICompatiblePaperModelClient,
    PaperModelBackendSpec,
    QwenClient,
)
from self_harness.exceptions import LLMClientError
from self_harness.types import stable_json_dumps

MODEL_BACKEND_PREFLIGHT_SCHEMA_VERSION = "1.0"
MODEL_BACKEND_PREFLIGHT_BOUNDARY = (
    "paper model backend preflight only; validates operator-provided MiniMax M2.5, "
    "Qwen3.5-35B-A3B, and GLM-5.2 chat-completions reachability or replay fixtures, "
    "does not run Terminal-Bench, does not evaluate harnesses, and is not benchmark "
    "reproduction evidence"
)

ClientFactory = Callable[
    [ChatCompletionTransport | None, Callable[[dict[str, int]], None] | None],
    OpenAICompatiblePaperModelClient,
]


class ModelBackendPreflightError(ValueError):
    """Raised when model backend preflight inputs are malformed or unsafe."""


@dataclass(frozen=True)
class ModelBackendRuntime:
    backend_id: str
    spec: PaperModelBackendSpec
    client_factory: ClientFactory


@dataclass(frozen=True)
class ModelBackendPreflightCheck:
    name: str
    backend: str
    status: str
    detail: str
    required: bool
    metadata: dict[str, object]


@dataclass(frozen=True)
class ModelBackendPreflightReport:
    schema_version: str
    ok: bool
    mode: str
    backends: tuple[str, ...]
    checks: tuple[ModelBackendPreflightCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str
    evaluated_at: str | None = None


class ReplayChatCompletionTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response

    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return self.response


class UrlLibChatCompletionTransport:
    """Tiny OpenAI-compatible HTTP transport used only by explicit live preflight."""

    def __init__(self, *, base_url: str, api_key: str | None = None, timeout_seconds: float = 30.0) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be non-empty")
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = urllib.request.Request(
            _chat_completions_url(self.base_url),
            data=(stable_json_dumps(payload) + "\n").encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = _read_error_body(exc)
            detail = f"chat completion HTTP error: status={exc.code}"
            if body:
                detail = f"{detail}; body={body}"
            raise LLMClientError(detail) from exc
        except urllib.error.URLError as exc:
            raise LLMClientError(f"chat completion request failed: {exc.reason}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMClientError("chat completion response was not valid JSON") from exc
        if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
            raise LLMClientError("chat completion response must be a JSON object with string keys")
        return cast(dict[str, object], data)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "self-harness-model-backend-preflight/1.0",
        }
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


class AnthropicMessagesTransport:
    """Anthropic-compatible Messages transport for the Z.ai GLM coding plan.

    The coding plan is served on Z.ai's Anthropic-compatible endpoint
    (``https://api.z.ai/api/anthropic/v1/messages``) rather than the OpenAI-style
    ``/chat/completions`` endpoint, which requires prepaid balance and otherwise returns
    ``code 1113``. This transport implements the same ``ChatCompletionTransport`` contract used by
    the paper-model clients: it accepts an OpenAI-shaped chat-completion request and returns an
    OpenAI-shaped response, translating to and from the Anthropic Messages wire format in between so
    ``GLMClient`` and the rest of the harness need no changes.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        anthropic_version: str = "2023-06-01",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be non-empty")
        if not api_key.strip():
            raise ValueError("api_key must be non-empty")
        self.base_url = base_url
        self.api_key = api_key
        self.anthropic_version = anthropic_version
        self.timeout_seconds = timeout_seconds

    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = urllib.request.Request(
            _messages_url(self.base_url),
            data=(stable_json_dumps(_to_messages_request(payload)) + "\n").encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = _read_error_body(exc)
            detail = f"messages HTTP error: status={exc.code}"
            if body:
                detail = f"{detail}; body={body}"
            raise LLMClientError(detail) from exc
        except urllib.error.URLError as exc:
            raise LLMClientError(f"messages request failed: {exc.reason}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMClientError("messages response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise LLMClientError("messages response must be a JSON object")
        return _from_messages_response(cast(dict[str, object], data))

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "self-harness-model-backend-preflight/1.0",
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
        }


def _messages_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/v1/messages"):
        return stripped
    return f"{stripped}/v1/messages"


def _to_messages_request(payload: Mapping[str, object]) -> dict[str, object]:
    """Translate an OpenAI chat-completion request into an Anthropic Messages request."""

    messages_in = payload.get("messages")
    system_parts: list[str] = []
    turns: list[dict[str, object]] = []
    if isinstance(messages_in, list):
        for message in messages_in:
            if not isinstance(message, Mapping):
                continue
            role = message.get("role")
            content = message.get("content")
            text = content if isinstance(content, str) else stable_json_dumps(content)
            if role == "system":
                system_parts.append(text)
            else:
                # Anthropic only accepts "user" and "assistant" roles.
                turns.append({"role": "assistant" if role == "assistant" else "user", "content": text})
    if not turns:
        turns.append({"role": "user", "content": ""})

    request: dict[str, object] = {
        "model": payload.get("model"),
        "messages": turns,
        "max_tokens": payload.get("max_tokens", 4096),
    }
    if system_parts:
        request["system"] = "\n\n".join(system_parts)
    temperature = payload.get("temperature")
    if isinstance(temperature, (int, float)):
        request["temperature"] = float(temperature)
    return request


def _from_messages_response(data: Mapping[str, object]) -> dict[str, object]:
    """Translate an Anthropic Messages response into an OpenAI chat-completion response."""

    content = data.get("content")
    text_parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
    text = "".join(text_parts)

    usage_out: dict[str, int] = {}
    usage_in = data.get("usage")
    if isinstance(usage_in, Mapping):
        input_tokens = usage_in.get("input_tokens")
        output_tokens = usage_in.get("output_tokens")
        if isinstance(input_tokens, int):
            usage_out["prompt_tokens"] = input_tokens
        if isinstance(output_tokens, int):
            usage_out["completion_tokens"] = output_tokens
        if "prompt_tokens" in usage_out and "completion_tokens" in usage_out:
            usage_out["total_tokens"] = usage_out["prompt_tokens"] + usage_out["completion_tokens"]

    response: dict[str, object] = {
        "model": data.get("model", ""),
        "choices": [{"message": {"role": "assistant", "content": text}}],
    }
    if usage_out:
        response["usage"] = usage_out
    return response


def is_anthropic_messages_endpoint(base_url: str) -> bool:
    """Z.ai coding-plan subscriptions are served on the Anthropic-compatible endpoint."""

    lowered = base_url.lower()
    return "/anthropic" in lowered or lowered.rstrip("/").endswith("/v1/messages")


def build_zai_transport(*, base_url: str, api_key: str | None) -> ChatCompletionTransport:
    """Select the right Z.ai transport for the configured endpoint.

    The coding plan uses the Anthropic-compatible Messages endpoint
    (``.../api/anthropic``); the pay-as-you-go PaaS plan uses the OpenAI-compatible
    ``/chat/completions`` endpoint. The endpoint URL determines which wire format to speak.
    """

    if is_anthropic_messages_endpoint(base_url):
        if not api_key:
            raise LLMClientError("Z.ai coding-plan endpoint requires an API key (ZAI_API_KEY)")
        return AnthropicMessagesTransport(base_url=base_url, api_key=api_key)
    return UrlLibChatCompletionTransport(base_url=base_url, api_key=api_key)


def build_minimax_transport(*, base_url: str, api_key: str | None) -> ChatCompletionTransport:
    if not api_key:
        raise LLMClientError("MiniMax endpoint requires an API key (MINIMAX_API_KEY)")
    return UrlLibChatCompletionTransport(base_url=base_url, api_key=api_key)


def build_qwen_transport(*, base_url: str, api_key: str | None = None) -> ChatCompletionTransport:
    return UrlLibChatCompletionTransport(base_url=base_url, api_key=api_key)


def evaluate_model_backend_preflight(
    *,
    mode: str,
    backend_ids: Sequence[str],
    env: Mapping[str, str],
    replay_path: Path | None = None,
    today: str | None = None,
    transport_overrides: Mapping[str, ChatCompletionTransport] | None = None,
) -> ModelBackendPreflightReport:
    if mode not in {"dry-run", "replay", "live"}:
        raise ModelBackendPreflightError(f"unsupported model backend preflight mode: {mode}")
    selected = _selected_backends(backend_ids)
    checks: list[ModelBackendPreflightCheck] = []
    for backend in selected:
        if mode == "dry-run":
            checks.append(_dry_run_check(backend))
        elif mode == "replay":
            checks.append(_replay_check(backend, replay_path))
        else:
            checks.append(_live_check(backend, env=env, transport_overrides=transport_overrides or {}))

    ok = all(check.status == "pass" for check in checks if check.required)
    report_without_hash = {
        "schema_version": MODEL_BACKEND_PREFLIGHT_SCHEMA_VERSION,
        "ok": ok,
        "mode": mode,
        "backends": [backend.backend_id for backend in selected],
        "checks": [_check_to_jsonable(check) for check in checks],
        "reproduction_claimed": False,
        "boundary": MODEL_BACKEND_PREFLIGHT_BOUNDARY,
        "evaluated_at": today,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return ModelBackendPreflightReport(
        schema_version=MODEL_BACKEND_PREFLIGHT_SCHEMA_VERSION,
        ok=ok,
        mode=mode,
        backends=tuple(backend.backend_id for backend in selected),
        checks=tuple(checks),
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=MODEL_BACKEND_PREFLIGHT_BOUNDARY,
        evaluated_at=today,
    )


def model_backend_preflight_report_to_jsonable(report: ModelBackendPreflightReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "mode": report.mode,
        "backends": list(report.backends),
        "checks": [_check_to_jsonable(check) for check in report.checks],
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
        "evaluated_at": report.evaluated_at,
    }


def _minimax_client(
    transport: ChatCompletionTransport | None,
    on_usage: Callable[[dict[str, int]], None] | None,
) -> OpenAICompatiblePaperModelClient:
    return MiniMaxClient(transport=transport, on_usage=on_usage)


def _qwen_client(
    transport: ChatCompletionTransport | None,
    on_usage: Callable[[dict[str, int]], None] | None,
) -> OpenAICompatiblePaperModelClient:
    return QwenClient(transport=transport, on_usage=on_usage)


def _glm_client(
    transport: ChatCompletionTransport | None,
    on_usage: Callable[[dict[str, int]], None] | None,
) -> OpenAICompatiblePaperModelClient:
    return GLMClient(transport=transport, on_usage=on_usage)


_BACKENDS: dict[str, ModelBackendRuntime] = {
    "minimax": ModelBackendRuntime("minimax", MINIMAX_M25_SPEC, _minimax_client),
    "qwen": ModelBackendRuntime("qwen", QWEN35_35B_A3B_SPEC, _qwen_client),
    "glm": ModelBackendRuntime("glm", GLM5_SPEC, _glm_client),
}
_BACKEND_ORDER = ("minimax", "qwen", "glm")


def _selected_backends(backend_ids: Sequence[str]) -> tuple[ModelBackendRuntime, ...]:
    if not backend_ids or "all" in backend_ids:
        return tuple(_BACKENDS[backend_id] for backend_id in _BACKEND_ORDER)
    selected: list[ModelBackendRuntime] = []
    for backend_id in backend_ids:
        backend = _BACKENDS.get(backend_id)
        if backend is None:
            raise ModelBackendPreflightError(f"unknown model backend: {backend_id}")
        selected.append(backend)
    return tuple(selected)


def _dry_run_check(backend: ModelBackendRuntime) -> ModelBackendPreflightCheck:
    return ModelBackendPreflightCheck(
        name=f"{backend.backend_id}_backend_reachable",
        backend=backend.backend_id,
        status="not-run",
        detail="dry-run mode does not contact model providers",
        required=True,
        metadata=_backend_metadata(backend),
    )


def _replay_check(backend: ModelBackendRuntime, replay_path: Path | None) -> ModelBackendPreflightCheck:
    try:
        response = _load_replay_response(backend, replay_path)
        usage: dict[str, int] = {}
        client = backend.client_factory(ReplayChatCompletionTransport(response), usage.update)
        content = client.complete(_system_prompt(), _user_prompt())
        return ModelBackendPreflightCheck(
            name=f"{backend.backend_id}_backend_reachable",
            backend=backend.backend_id,
            status="pass",
            detail="chat completion replay parsed successfully",
            required=True,
            metadata={
                **_backend_metadata(backend),
                "usage": usage,
                "response_text_sha256": sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    except LLMClientError as exc:
        return _failed_check(backend, f"chat completion replay failed: {exc}")


def _live_check(
    backend: ModelBackendRuntime,
    *,
    env: Mapping[str, str],
    transport_overrides: Mapping[str, ChatCompletionTransport],
) -> ModelBackendPreflightCheck:
    missing = _missing_live_environment(backend.spec, env, backend.backend_id)
    if missing:
        return _failed_check(backend, "missing live environment variable(s): " + ", ".join(missing))
    usage: dict[str, int] = {}
    try:
        transport = transport_overrides.get(backend.backend_id)
        if transport is None:
            base_url = env.get(backend.spec.endpoint_env) or _default_endpoint(backend.backend_id)
            transport = build_zai_transport(
                base_url=base_url,
                api_key=env.get(backend.spec.credential_env) if backend.spec.credential_env is not None else None,
            )
        client = backend.client_factory(transport, usage.update)
        content = client.complete(_system_prompt(), _user_prompt())
        return ModelBackendPreflightCheck(
            name=f"{backend.backend_id}_backend_reachable",
            backend=backend.backend_id,
            status="pass",
            detail="live chat completion parsed successfully",
            required=True,
            metadata={
                **_backend_metadata(backend),
                "usage": usage,
                "response_text_sha256": sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    except Exception as exc:
        return _failed_check(backend, f"live chat completion failed: {exc}")


def _failed_check(backend: ModelBackendRuntime, detail: str) -> ModelBackendPreflightCheck:
    return ModelBackendPreflightCheck(
        name=f"{backend.backend_id}_backend_reachable",
        backend=backend.backend_id,
        status="fail",
        detail=detail,
        required=True,
        metadata=_backend_metadata(backend),
    )


_DEFAULT_ENDPOINTS: dict[str, str] = {
    # The GLM coding plan only requires ZAI_API_KEY; default to its Anthropic-compatible endpoint.
    "glm": "https://api.z.ai/api/anthropic",
}


def _default_endpoint(backend_id: str) -> str:
    endpoint = _DEFAULT_ENDPOINTS.get(backend_id)
    if endpoint is None:
        raise ModelBackendPreflightError(f"no default endpoint for backend: {backend_id}")
    return endpoint


def _missing_live_environment(spec: PaperModelBackendSpec, env: Mapping[str, str], backend_id: str) -> tuple[str, ...]:
    required: list[str] = []
    if not env.get(spec.endpoint_env) and backend_id not in _DEFAULT_ENDPOINTS:
        required.append(spec.endpoint_env)
    if spec.credential_env is not None and not env.get(spec.credential_env):
        required.append(spec.credential_env)
    return tuple(required)


def _load_replay_response(backend: ModelBackendRuntime, replay_path: Path | None) -> Mapping[str, object]:
    path = _replay_fixture_path(backend, replay_path)
    data = _load_json_object(path, description="model backend replay fixture")
    if _contains_reproduction_claim(data):
        raise ModelBackendPreflightError("model backend replay fixture unexpectedly claims benchmark reproduction")
    schema_version = data.get("schema_version")
    if schema_version != MODEL_BACKEND_PREFLIGHT_SCHEMA_VERSION:
        raise ModelBackendPreflightError(f"unsupported model backend replay schema_version: {schema_version!r}")
    fixture_backend = data.get("backend")
    if fixture_backend != backend.backend_id:
        raise ModelBackendPreflightError(
            f"model backend replay fixture backend mismatch: expected {backend.backend_id!r}, got {fixture_backend!r}"
        )
    response = data.get("response")
    if not isinstance(response, dict) or not all(isinstance(key, str) for key in response):
        raise ModelBackendPreflightError("model backend replay fixture response must be an object")
    return cast(dict[str, object], response)


def _replay_fixture_path(backend: ModelBackendRuntime, replay_path: Path | None) -> Path:
    if replay_path is None:
        return Path("tests") / "fixtures" / "model_backend" / f"{backend.backend_id}_chat_completion_replay.json"
    if replay_path.is_dir():
        return replay_path / f"{backend.backend_id}_chat_completion_replay.json"
    return replay_path


def _load_json_object(path: Path, *, description: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelBackendPreflightError(f"missing {description}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ModelBackendPreflightError(f"invalid {description} JSON: {path}") from exc
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise ModelBackendPreflightError(f"{description} must be a JSON object with string keys: {path}")
    return cast(dict[str, object], data)


def _backend_metadata(backend: ModelBackendRuntime) -> dict[str, object]:
    return {
        "provider": backend.spec.provider,
        "paper_model_name": backend.spec.paper_model_name,
        "default_model": backend.spec.default_model,
        "access_mode": backend.spec.access_mode,
        "credential_env": backend.spec.credential_env,
        "endpoint_env": backend.spec.endpoint_env,
        "paper_reference": backend.spec.paper_reference,
    }


def _check_to_jsonable(check: ModelBackendPreflightCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "backend": check.backend,
        "status": check.status,
        "detail": check.detail,
        "required": check.required,
        "metadata": check.metadata,
    }


def _contains_reproduction_claim(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("reproduction_claimed") is True:
            return True
        return any(_contains_reproduction_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_reproduction_claim(item) for item in value)
    return False


def _chat_completions_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    return f"{stripped}/chat/completions"


def _read_error_body(exc: urllib.error.HTTPError, *, limit: int = 300) -> str:
    """Return the provider's error body (e.g. Z.ai 'insufficient balance') for diagnostics.

    Surfacing the body distinguishes an actionable funding/quota issue from an auth or endpoint
    misconfiguration, both of which can present as the same HTTP status.
    """

    try:
        raw = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    return raw[:limit].replace("\n", " ")


def _system_prompt() -> str:
    return "You are running a Self-Harness paper model backend preflight."


def _user_prompt() -> str:
    return "Reply with a short non-empty readiness acknowledgement."
