from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from self_harness.exceptions import LLMClientError
from self_harness.types import stable_json_dumps

DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 120.0
_ANTHROPIC_OUTPUT_CONFIG_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
DEFAULT_RATE_LIMIT_MARKERS = ("[1302]", "rate limit", "too many requests", "overloaded")


@dataclass(frozen=True)
class RateLimitPolicy:
    """Transparent retry/pacing policy for GLM/Z.ai message requests."""

    max_attempts: int = 4
    base_backoff_seconds: float = 5.0
    max_backoff_seconds: float = 60.0
    min_interval_seconds: float = 1.5
    retryable_statuses: frozenset[int] = frozenset({429})
    retryable_markers: tuple[str, ...] = DEFAULT_RATE_LIMIT_MARKERS


@dataclass
class RateLimitPacer:
    """In-memory request pacer shared across turns of one interactive GLM session."""

    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    _next_allowed_at: float = field(default=0.0, init=False)

    def wait_if_needed(self) -> None:
        delay = self._next_allowed_at - self.clock()
        if delay > 0:
            self.sleeper(delay)

    def mark_request_sent(self, policy: RateLimitPolicy) -> None:
        if policy.min_interval_seconds <= 0:
            return
        self._next_allowed_at = max(self._next_allowed_at, self.clock() + policy.min_interval_seconds)

    def cool_down(self, delay_seconds: float) -> None:
        if delay_seconds <= 0:
            return
        self._next_allowed_at = max(self._next_allowed_at, self.clock() + delay_seconds)

    def reset(self) -> None:
        self._next_allowed_at = 0.0


@dataclass(frozen=True)
class MessagesTurn:
    """One model turn: the stop reason, the raw content blocks, and token usage."""

    stop_reason: str
    content: list[dict[str, Any]]
    usage: dict[str, int]

    def text(self) -> str:
        return "".join(
            block.get("text", "")
            for block in self.content
            if isinstance(block, Mapping) and block.get("type") == "text"
        )

    def tool_uses(self) -> list[dict[str, Any]]:
        return [
            dict(block)
            for block in self.content
            if isinstance(block, Mapping) and block.get("type") == "tool_use"
        ]


class MessagesTransport:
    """Protocol-ish marker; any object with ``create_message`` works in the agent loop."""

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
    ) -> MessagesTurn:  # pragma: no cover - interface
        raise NotImplementedError


class AnthropicAgentTransport(MessagesTransport):
    """Multi-turn, tool-calling transport for the Anthropic-compatible Messages API.

    Unlike ``AnthropicMessagesTransport`` (which flattens to a single OpenAI-shaped text reply and
    drops tool blocks), this transport preserves the full agentic protocol: it passes ``tools``
    through, keeps structured ``tool_use``/``tool_result`` content in the conversation, and returns
    the model's ``stop_reason`` plus its raw content blocks so the caller can run the tool loop. It
    targets the same Z.ai coding-plan endpoint (``.../api/anthropic``) with ``x-api-key`` and
    ``anthropic-version`` auth.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        effort: str | None = None,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        rate_limit_policy: RateLimitPolicy | None = None,
        rate_limit_pacer: RateLimitPacer | None = None,
        on_rate_limit_retry: Callable[[int, float, str], None] | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be non-empty")
        if not api_key.strip():
            raise ValueError("api_key must be non-empty")
        if not model.strip():
            raise ValueError("model must be non-empty")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.anthropic_version = anthropic_version
        self.timeout_seconds = timeout_seconds
        self.rate_limit_policy = rate_limit_policy
        self.rate_limit_pacer = rate_limit_pacer
        self.on_rate_limit_retry = on_rate_limit_retry

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> MessagesTurn:
        payload = _build_payload(self.model, system, messages, tools, max_tokens, effort=self.effort)

        def _send_once() -> MessagesTurn:
            request = urllib.request.Request(
                _messages_url(self.base_url),
                data=(stable_json_dumps(payload) + "\n").encode("utf-8"),
                headers=_anthropic_headers(self.api_key, self.anthropic_version),
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                raise LLMClientError(_http_error_detail(exc)) from exc
            except urllib.error.URLError as exc:
                raise LLMClientError(f"messages request failed: {exc.reason}") from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LLMClientError("messages response was not valid JSON") from exc
            if not isinstance(data, Mapping):
                raise LLMClientError("messages response must be a JSON object")
            return _parse_messages_response(data)

        return _with_rate_limit_retries(
            _send_once,
            policy=self.rate_limit_policy,
            pacer=self.rate_limit_pacer,
            on_retry=self.on_rate_limit_retry,
        )


class StreamingAnthropicAgentTransport(MessagesTransport):
    """Streaming variant of :class:`AnthropicAgentTransport` using Server-Sent Events.

    Identical ``create_message`` contract and return type (``MessagesTurn``) so the agent loop and all its
    callers are unaffected — the only difference is that incremental text is emitted through the injected
    ``on_text_delta`` callback as it arrives, and ``on_tool_start`` fires when the model begins a tool
    call. This is what makes the interactive CLI feel responsive (tokens stream rather than appearing all
    at once). It assembles the same final ``MessagesTurn`` from the SSE event sequence, and preserves the
    exact ``LLMClientError`` error contract — HTTP/network/JSON failures and mid-stream ``error`` events
    all raise ``LLMClientError`` so the loop converts them to a ``model_error`` result.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        effort: str | None = None,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        on_text_delta: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str], None] | None = None,
        rate_limit_policy: RateLimitPolicy | None = None,
        rate_limit_pacer: RateLimitPacer | None = None,
        on_rate_limit_retry: Callable[[int, float, str], None] | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be non-empty")
        if not api_key.strip():
            raise ValueError("api_key must be non-empty")
        if not model.strip():
            raise ValueError("model must be non-empty")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.anthropic_version = anthropic_version
        self.timeout_seconds = timeout_seconds
        self.on_text_delta = on_text_delta
        self.on_tool_start = on_tool_start
        self.rate_limit_policy = rate_limit_policy
        self.rate_limit_pacer = rate_limit_pacer
        self.on_rate_limit_retry = on_rate_limit_retry

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> MessagesTurn:
        payload = _build_payload(self.model, system, messages, tools, max_tokens, effort=self.effort)
        payload["stream"] = True
        headers = _anthropic_headers(self.api_key, self.anthropic_version)
        headers["Accept"] = "text/event-stream"

        def _send_once() -> MessagesTurn:
            request = urllib.request.Request(
                _messages_url(self.base_url),
                data=(stable_json_dumps(payload) + "\n").encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return self._consume_stream(response)
            except urllib.error.HTTPError as exc:
                raise LLMClientError(_http_error_detail(exc)) from exc
            except urllib.error.URLError as exc:
                raise LLMClientError(f"messages request failed: {exc.reason}") from exc

        return _with_rate_limit_retries(
            _send_once,
            policy=self.rate_limit_policy,
            pacer=self.rate_limit_pacer,
            on_retry=self.on_rate_limit_retry,
        )

    def _consume_stream(self, response: Any) -> MessagesTurn:
        builder = _StreamBuilder(on_text_delta=self.on_text_delta, on_tool_start=self.on_tool_start)
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                # Skip blank separators, comments/pings, and `event:` lines — `data:` carries the payload.
                continue
            data_str = line[len("data:") :].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError as exc:
                raise LLMClientError("streaming event was not valid JSON") from exc
            if isinstance(event, Mapping):
                builder.handle(event)
        return builder.finish()

    def __call__(self) -> MessagesTransport:  # convenience so it can be used as a transport_factory
        return self


def _messages_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/v1/messages"):
        return stripped
    return f"{stripped}/v1/messages"


def _build_payload(
    model: str,
    system: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    max_tokens: int,
    *,
    effort: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [dict(message) for message in messages],
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = [dict(tool) for tool in tools]
    if effort:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = effort
        if effort in _ANTHROPIC_OUTPUT_CONFIG_EFFORTS:
            payload["output_config"] = {"effort": effort}
    return payload


def _anthropic_headers(api_key: str, anthropic_version: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "self-harness-agentic-runner/1.0",
        "x-api-key": api_key,
        "anthropic-version": anthropic_version,
    }


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    body = _read_error_body(exc)
    detail = f"messages HTTP error: status={exc.code}"
    retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
    if retry_after:
        detail = f"{detail}; retry_after={retry_after}"
    if body:
        detail = f"{detail}; body={body}"
    return detail


def _with_rate_limit_retries(
    send_once: Callable[[], MessagesTurn],
    *,
    policy: RateLimitPolicy | None,
    pacer: RateLimitPacer | None,
    on_retry: Callable[[int, float, str], None] | None,
) -> MessagesTurn:
    if policy is None or policy.max_attempts <= 1:
        if pacer is not None and policy is not None:
            pacer.wait_if_needed()
            pacer.mark_request_sent(policy)
        return send_once()
    attempt = 1
    while True:
        if pacer is not None:
            pacer.wait_if_needed()
            pacer.mark_request_sent(policy)
        try:
            turn = send_once()
        except LLMClientError as exc:
            reason = _retryable_rate_limit_reason(str(exc), policy)
            if reason is None or attempt >= policy.max_attempts:
                raise
            delay = _retry_delay_seconds(str(exc), policy, attempt)
            if on_retry is not None:
                on_retry(attempt, delay, reason)
            if pacer is not None:
                pacer.cool_down(delay)
            else:
                time.sleep(delay)
            attempt += 1
            continue
        return turn


def _retryable_rate_limit_reason(text: str, policy: RateLimitPolicy) -> str | None:
    status = _status_code_from_error(text)
    if status in policy.retryable_statuses:
        return f"status {status}"
    lowered = text.lower()
    for marker in policy.retryable_markers:
        if marker.lower() in lowered:
            return marker
    return None


def _status_code_from_error(text: str) -> int | None:
    match = re.search(r"\bstatus=(\d{3})\b", text)
    return int(match.group(1)) if match else None


def _retry_delay_seconds(text: str, policy: RateLimitPolicy, failed_attempt: int) -> float:
    retry_after = _retry_after_seconds(text)
    if retry_after is not None:
        return min(policy.max_backoff_seconds, max(0.0, retry_after))
    delay = float(policy.base_backoff_seconds) * float(2 ** max(0, failed_attempt - 1))
    return float(min(policy.max_backoff_seconds, max(0.0, delay)))


def _retry_after_seconds(text: str) -> float | None:
    match = re.search(r"\bretry_after=([0-9]+(?:\.[0-9]+)?)\b", text)
    if match:
        return float(match.group(1))
    return None


class _StreamBuilder:
    """Accumulates an Anthropic SSE event stream into a single :class:`MessagesTurn`.

    Tracks content blocks by index: ``text`` blocks accumulate ``text_delta`` (emitted live via
    ``on_text_delta``); ``tool_use`` blocks accumulate ``input_json_delta`` into a JSON string that is
    parsed at ``content_block_stop``. ``message_delta`` carries the final ``stop_reason`` and output-token
    usage. A mid-stream ``error`` event raises ``LLMClientError`` to preserve the loop's error contract.
    """

    def __init__(
        self,
        *,
        on_text_delta: Callable[[str], None] | None,
        on_tool_start: Callable[[str], None] | None,
    ) -> None:
        self._on_text_delta = on_text_delta
        self._on_tool_start = on_tool_start
        self._blocks: dict[int, dict[str, Any]] = {}
        self._tool_json: dict[int, str] = {}
        self._order: list[int] = []
        self._stop_reason = "end_turn"
        self._usage: dict[str, int] = {}

    def handle(self, event: Mapping[str, Any]) -> None:
        etype = event.get("type")
        if etype == "error":
            err = event.get("error")
            message = err.get("message") if isinstance(err, Mapping) else None
            raise LLMClientError(f"streaming error: {message or 'unknown stream error'}")
        if etype == "message_start":
            msg = event.get("message")
            if isinstance(msg, Mapping):
                self._merge_usage(msg.get("usage"))
        elif etype == "content_block_start":
            self._start_block(event)
        elif etype == "content_block_delta":
            self._apply_delta(event)
        elif etype == "content_block_stop":
            self._stop_block(event)
        elif etype == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping) and isinstance(delta.get("stop_reason"), str):
                self._stop_reason = delta["stop_reason"]
            self._merge_usage(event.get("usage"))

    def _start_block(self, event: Mapping[str, Any]) -> None:
        index = _as_int(event.get("index"))
        block = event.get("content_block")
        if index is None or not isinstance(block, Mapping):
            return
        btype = block.get("type")
        if index not in self._blocks:
            self._order.append(index)
        if btype == "text":
            self._blocks[index] = {"type": "text", "text": str(block.get("text", ""))}
        elif btype == "tool_use":
            self._blocks[index] = {
                "type": "tool_use",
                "id": block.get("id"),
                "name": block.get("name"),
                "input": {},
            }
            self._tool_json[index] = ""
            if self._on_tool_start is not None and isinstance(block.get("name"), str):
                self._on_tool_start(block["name"])

    def _apply_delta(self, event: Mapping[str, Any]) -> None:
        index = _as_int(event.get("index"))
        delta = event.get("delta")
        if index is None or not isinstance(delta, Mapping):
            return
        dtype = delta.get("type")
        if dtype == "text_delta":
            text = str(delta.get("text", ""))
            block = self._blocks.get(index)
            if block is not None and block.get("type") == "text":
                block["text"] = block.get("text", "") + text
            if text and self._on_text_delta is not None:
                self._on_text_delta(text)
        elif dtype == "input_json_delta":
            self._tool_json[index] = self._tool_json.get(index, "") + str(delta.get("partial_json", ""))

    def _stop_block(self, event: Mapping[str, Any]) -> None:
        index = _as_int(event.get("index"))
        if index is None:
            return
        block = self._blocks.get(index)
        if block is not None and block.get("type") == "tool_use":
            raw = self._tool_json.get(index, "").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    block["input"] = parsed if isinstance(parsed, Mapping) else {}
                except json.JSONDecodeError:
                    block["input"] = {}

    def _merge_usage(self, usage: object) -> None:
        if not isinstance(usage, Mapping):
            return
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                self._usage[key] = value
        if "input_tokens" in self._usage and "output_tokens" in self._usage:
            self._usage["total_tokens"] = self._usage["input_tokens"] + self._usage["output_tokens"]

    def finish(self) -> MessagesTurn:
        content = [self._blocks[i] for i in self._order if i in self._blocks]
        return MessagesTurn(stop_reason=self._stop_reason, content=content, usage=dict(self._usage))


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _parse_messages_response(data: Mapping[str, Any]) -> MessagesTurn:
    raw_content = data.get("content")
    content: list[dict[str, Any]] = []
    if isinstance(raw_content, list):
        for block in raw_content:
            if isinstance(block, Mapping):
                content.append(dict(block))

    stop_reason = data.get("stop_reason")
    if not isinstance(stop_reason, str):
        stop_reason = "end_turn"

    usage_out: dict[str, int] = {}
    usage_in = data.get("usage")
    if isinstance(usage_in, Mapping):
        for source_key, target_key in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
        ):
            value = usage_in.get(source_key)
            if isinstance(value, int):
                usage_out[target_key] = value
        if "input_tokens" in usage_out and "output_tokens" in usage_out:
            usage_out["total_tokens"] = usage_out["input_tokens"] + usage_out["output_tokens"]

    return MessagesTurn(stop_reason=stop_reason, content=content, usage=usage_out)


def _read_error_body(exc: urllib.error.HTTPError, *, limit: int = 300) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    return raw[:limit].replace("\n", " ")
