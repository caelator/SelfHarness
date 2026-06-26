from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from self_harness.exceptions import LLMClientError, LLMRequestError
from self_harness.llm_proposer import LLMClient


class AnthropicClaudeClient(LLMClient):
    """Reference Anthropic Claude adapter for the provider-neutral LLM proposer."""

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        max_retries: int = 3,
        max_tokens: int = 4096,
        retry_delay_seconds: float = 0.25,
        on_usage: Callable[[dict[str, int]], None] | None = None,
        client: Any | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise LLMClientError(f"missing Anthropic API key environment variable: {api_key_env}")
        self.model = model
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.retry_delay_seconds = retry_delay_seconds
        self.on_usage = on_usage
        self._client = client if client is not None else self._build_client(api_key)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        attempt = 0
        while True:
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                self._report_usage(response)
                return _response_text(response)
            except Exception as exc:  # noqa: BLE001 - provider SDKs expose several transient exception classes.
                status_code = _status_code(exc)
                if _retryable(status_code) and attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds * (2**attempt))
                    attempt += 1
                    continue
                if status_code is not None and 400 <= status_code < 500:
                    raise LLMRequestError(
                        f"Anthropic request failed with status {status_code}",
                        status_code=status_code,
                    ) from exc
                raise LLMClientError(f"Anthropic completion failed: {exc}") from exc

    def _build_client(self, api_key: str) -> Any:
        try:
            from anthropic import Anthropic  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise LLMClientError("install self-harness[anthropic] to use AnthropicClaudeClient") from exc
        return Anthropic(api_key=api_key)

    def _report_usage(self, response: Any) -> None:
        if self.on_usage is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        counts: dict[str, int] = {}
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if isinstance(input_tokens, int):
            counts["input_tokens"] = input_tokens
        if isinstance(output_tokens, int):
            counts["output_tokens"] = output_tokens
        if counts:
            self.on_usage(counts)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    content = getattr(response, "content", [])
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(str(block["text"]))
    if not parts:
        raise LLMClientError("Anthropic response did not contain text content")
    return "".join(parts)


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    return value if isinstance(value, int) else None


def _retryable(status_code: int | None) -> bool:
    return status_code == 429 or (status_code is not None and status_code >= 500)
