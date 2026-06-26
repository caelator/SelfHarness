from __future__ import annotations

from collections.abc import Mapping

from self_harness.adapters.llm.paper_models import MINIMAX_M25_SPEC, MiniMaxClient


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, object]] = []

    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append(payload)
        return {
            "choices": [{"message": {"content": "minimax proposal"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }


def test_minimax_client_serializes_chat_completion_payload() -> None:
    usage_events: list[dict[str, int]] = []
    transport = FakeTransport()
    client = MiniMaxClient(
        model="minimax-m2.5-test",
        max_tokens=512,
        transport=transport,
        on_usage=usage_events.append,
    )

    assert client.spec == MINIMAX_M25_SPEC
    assert client.complete("system", "user") == "minimax proposal"
    assert transport.calls == [
        {
            "model": "minimax-m2.5-test",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "max_tokens": 512,
            "temperature": 0.0,
        }
    ]
    assert usage_events == [{"input_tokens": 11, "output_tokens": 7}]
