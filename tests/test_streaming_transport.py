from __future__ import annotations

import io

import pytest

from self_harness.adapters.llm.messages import (
    StreamingAnthropicAgentTransport,
    _StreamBuilder,
)
from self_harness.exceptions import LLMClientError


def _events_to_sse(events: list[dict]) -> bytes:
    import json

    lines = []
    for e in events:
        lines.append(f"event: {e['type']}")
        lines.append(f"data: {json.dumps(e)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _text_stream_events() -> list[dict]:
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ", world"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]


def test_stream_builder_accumulates_text_and_emits_deltas() -> None:
    deltas: list[str] = []
    builder = _StreamBuilder(on_text_delta=deltas.append, on_tool_start=None)
    for e in _text_stream_events():
        builder.handle(e)
    turn = builder.finish()
    assert turn.text() == "Hello, world"
    assert turn.stop_reason == "end_turn"
    assert turn.usage["total_tokens"] == 8
    assert deltas == ["Hello", ", world"]  # streamed incrementally, in order


def test_stream_builder_accumulates_tool_use_args() -> None:
    starts: list[str] = []
    builder = _StreamBuilder(on_text_delta=None, on_tool_start=starts.append)
    events = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t1", "name": "bash", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"command":'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": ' "ls -la"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    ]
    for e in events:
        builder.handle(e)
    turn = builder.finish()
    assert starts == ["bash"]
    tool_uses = turn.tool_uses()
    assert len(tool_uses) == 1
    assert tool_uses[0]["name"] == "bash"
    assert tool_uses[0]["input"] == {"command": "ls -la"}
    assert turn.stop_reason == "tool_use"


def test_stream_builder_raises_on_mid_stream_error() -> None:
    builder = _StreamBuilder(on_text_delta=None, on_tool_start=None)
    with pytest.raises(LLMClientError, match="streaming error"):
        builder.handle({"type": "error", "error": {"message": "overloaded"}})


class _FakeResponse:
    """Mimics a urllib response: iterating yields raw bytes lines."""

    def __init__(self, body: bytes) -> None:
        self._buf = io.BytesIO(body)

    def __iter__(self):
        return iter(self._buf.readlines())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_consume_stream_assembles_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = StreamingAnthropicAgentTransport(
        base_url="https://example/api/anthropic", api_key="k", model="glm-5.2"
    )
    body = _events_to_sse(_text_stream_events())
    turn = transport._consume_stream(_FakeResponse(body))
    assert turn.text() == "Hello, world"
    assert turn.stop_reason == "end_turn"


def test_consume_stream_skips_pings_and_done(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = StreamingAnthropicAgentTransport(
        base_url="https://example/api/anthropic", api_key="k", model="glm-5.2"
    )
    body = (
        b": ping\n\n"
        + _events_to_sse(_text_stream_events())
        + b"data: [DONE]\n\n"
    )
    turn = transport._consume_stream(_FakeResponse(body))
    assert turn.text() == "Hello, world"
