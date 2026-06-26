from __future__ import annotations

from dataclasses import dataclass

import pytest

from self_harness.adapters.llm.anthropic import AnthropicClaudeClient
from self_harness.exceptions import LLMClientError, LLMRequestError


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeTextBlock:
    text: str


@dataclass
class FakeResponse:
    content: list[FakeTextBlock]
    usage: FakeUsage | None = None


class FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"status {status_code}")


class FakeMessages:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAnthropic:
    def __init__(self, results: list[object]) -> None:
        self.messages = FakeMessages(results)


def test_anthropic_client_completes_and_reports_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    usage_events: list[dict[str, int]] = []
    fake = FakeAnthropic([FakeResponse([FakeTextBlock("ok")], FakeUsage(3, 5))])
    client = AnthropicClaudeClient(
        "claude-test",
        client=fake,
        on_usage=usage_events.append,
    )

    output = client.complete("system", "user")

    assert output == "ok"
    assert usage_events == [{"input_tokens": 3, "output_tokens": 5}]
    assert fake.messages.calls[0]["model"] == "claude-test"
    assert fake.messages.calls[0]["system"] == "system"


def test_anthropic_client_retries_429_and_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake = FakeAnthropic(
        [
            FakeStatusError(429),
            FakeStatusError(500),
            FakeResponse([FakeTextBlock("recovered")]),
        ]
    )
    client = AnthropicClaudeClient("claude-test", client=fake, retry_delay_seconds=0)

    assert client.complete("system", "user") == "recovered"
    assert len(fake.messages.calls) == 3


def test_anthropic_client_surfaces_400_as_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicClaudeClient("claude-test", client=FakeAnthropic([FakeStatusError(400)]))

    with pytest.raises(LLMRequestError) as exc:
        client.complete("system", "user")

    assert exc.value.status_code == 400


def test_anthropic_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LLMClientError):
        AnthropicClaudeClient("claude-test", client=FakeAnthropic([]))
