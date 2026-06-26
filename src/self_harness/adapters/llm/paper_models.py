from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from self_harness.exceptions import LLMClientError
from self_harness.llm_proposer import LLMClient


class ChatCompletionTransport(Protocol):
    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        ...


@dataclass(frozen=True)
class PaperModelBackendSpec:
    provider: str
    paper_model_name: str
    default_model: str
    credential_env: str | None
    endpoint_env: str
    access_mode: str
    paper_reference: str


MINIMAX_M25_SPEC = PaperModelBackendSpec(
    provider="MiniMax",
    paper_model_name="MiniMax M2.5",
    default_model="minimax-m2.5",
    credential_env="MINIMAX_API_KEY",
    endpoint_env="MINIMAX_BASE_URL",
    access_mode="hosted_api",
    paper_reference="Self-Harness Appendix A.1 model inference services",
)

QWEN35_35B_A3B_SPEC = PaperModelBackendSpec(
    provider="Qwen",
    paper_model_name="Qwen3.5-35B-A3B",
    default_model="qwen3.5-35b-a3b",
    credential_env=None,
    endpoint_env="QWEN_SGLANG_BASE_URL",
    access_mode="operator_provisioned_sglang",
    paper_reference="Self-Harness Appendix A.1 model inference services",
)

GLM5_SPEC = PaperModelBackendSpec(
    provider="Z.ai",
    paper_model_name="GLM-5.2",
    default_model="glm-5.2",
    credential_env="ZAI_API_KEY",
    endpoint_env="ZAI_BASE_URL",
    access_mode="zai_hosted_api",
    paper_reference="Self-Harness Appendix A.1 model inference services; Z.ai GLM-5.2 chat completions API",
)


class OpenAICompatiblePaperModelClient(LLMClient):
    """Offline-testable chat-completions client contract for paper model backends."""

    def __init__(
        self,
        spec: PaperModelBackendSpec,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        transport: ChatCompletionTransport | None = None,
        on_usage: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if temperature < 0:
            raise ValueError("temperature must be non-negative")
        self.spec = spec
        self.model = model or spec.default_model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.transport = transport
        self.on_usage = on_usage

    def request_payload(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self.transport is None:
            raise LLMClientError(
                f"{self.__class__.__name__} requires an operator-supplied chat-completions transport; "
                f"credential_env={self.spec.credential_env!r}, endpoint_env={self.spec.endpoint_env!r}"
            )
        response = self.transport.create_chat_completion(self.request_payload(system_prompt, user_prompt))
        self._report_usage(response)
        return _chat_completion_text(response)

    def _report_usage(self, response: Mapping[str, object]) -> None:
        if self.on_usage is None:
            return
        usage = response.get("usage")
        if not isinstance(usage, Mapping):
            return
        counts: dict[str, int] = {}
        for source_key, target_key in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = usage.get(source_key)
            if isinstance(value, int):
                counts[target_key] = value
        if counts:
            self.on_usage(counts)


class MiniMaxClient(OpenAICompatiblePaperModelClient):
    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        transport: ChatCompletionTransport | None = None,
        on_usage: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        super().__init__(
            MINIMAX_M25_SPEC,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            transport=transport,
            on_usage=on_usage,
        )


class QwenClient(OpenAICompatiblePaperModelClient):
    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        transport: ChatCompletionTransport | None = None,
        on_usage: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        super().__init__(
            QWEN35_35B_A3B_SPEC,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            transport=transport,
            on_usage=on_usage,
        )


class GLMClient(OpenAICompatiblePaperModelClient):
    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        transport: ChatCompletionTransport | None = None,
        on_usage: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        super().__init__(
            GLM5_SPEC,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            transport=transport,
            on_usage=on_usage,
        )


def _chat_completion_text(response: Mapping[str, object]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMClientError("chat completion response did not contain choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise LLMClientError("chat completion choice must be an object")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise LLMClientError("chat completion choice did not contain a message object")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise LLMClientError("chat completion message did not contain text content")
    return content
