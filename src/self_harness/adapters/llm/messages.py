from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from self_harness.exceptions import LLMClientError
from self_harness.types import stable_json_dumps

DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 120.0


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
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
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
        self.anthropic_version = anthropic_version
        self.timeout_seconds = timeout_seconds

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> MessagesTurn:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [dict(message) for message in messages],
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]

        request = urllib.request.Request(
            _messages_url(self.base_url),
            data=(stable_json_dumps(payload) + "\n").encode("utf-8"),
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
        if not isinstance(data, Mapping):
            raise LLMClientError("messages response must be a JSON object")
        return _parse_messages_response(data)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "self-harness-agentic-runner/1.0",
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
        }


def _messages_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/v1/messages"):
        return stripped
    return f"{stripped}/v1/messages"


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
