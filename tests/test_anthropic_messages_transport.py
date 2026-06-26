from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from self_harness.adapters.llm.paper_models import GLMClient
from self_harness.exceptions import LLMClientError
from self_harness.model_backend_preflight import (
    AnthropicMessagesTransport,
    UrlLibChatCompletionTransport,
    build_zai_transport,
    is_anthropic_messages_endpoint,
)


def test_endpoint_detection_distinguishes_coding_plan_from_paas() -> None:
    assert is_anthropic_messages_endpoint("https://api.z.ai/api/anthropic")
    assert is_anthropic_messages_endpoint("https://api.z.ai/api/anthropic/v1/messages")
    assert not is_anthropic_messages_endpoint("https://api.z.ai/api/paas/v4")


def test_build_zai_transport_selects_by_endpoint() -> None:
    coding = build_zai_transport(base_url="https://api.z.ai/api/anthropic", api_key="k")
    paas = build_zai_transport(base_url="https://api.z.ai/api/paas/v4", api_key="k")

    assert isinstance(coding, AnthropicMessagesTransport)
    assert isinstance(paas, UrlLibChatCompletionTransport)


def test_build_zai_transport_requires_key_for_coding_plan() -> None:
    with pytest.raises(LLMClientError):
        build_zai_transport(base_url="https://api.z.ai/api/anthropic", api_key=None)


@contextmanager
def _messages_server(captured: dict[str, object]):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            captured["request"] = body
            captured["x_api_key"] = self.headers.get("x-api-key")
            captured["anthropic_version"] = self.headers.get("anthropic-version")
            captured["path"] = self.path
            payload = json.dumps(
                {
                    "model": "glm-5.2",
                    "content": [{"type": "text", "text": '{"proposals": []}'}],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}/api/anthropic"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_messages_transport_translates_request_and_response() -> None:
    captured: dict[str, object] = {}
    with _messages_server(captured) as base_url:
        transport = AnthropicMessagesTransport(base_url=base_url, api_key="secret-key")
        response = transport.create_chat_completion(
            {
                "model": "glm-5.2",
                "max_tokens": 256,
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": "system rules"},
                    {"role": "user", "content": "user prompt"},
                ],
            }
        )

    # Request was translated to the Anthropic Messages wire format.
    request = captured["request"]
    assert isinstance(request, Mapping)
    assert request["system"] == "system rules"
    assert request["messages"] == [{"role": "user", "content": "user prompt"}]
    assert request["max_tokens"] == 256
    assert captured["x_api_key"] == "secret-key"
    assert captured["anthropic_version"] == "2023-06-01"
    assert str(captured["path"]).endswith("/v1/messages")

    # Response was translated back to the OpenAI chat-completion shape.
    assert response["choices"][0]["message"]["content"] == '{"proposals": []}'
    assert response["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def test_glm_client_drives_messages_transport_end_to_end() -> None:
    captured: dict[str, object] = {}
    with _messages_server(captured) as base_url:
        usage: dict[str, int] = {}
        transport = AnthropicMessagesTransport(base_url=base_url, api_key="secret-key")
        client = GLMClient(transport=transport, max_tokens=128, temperature=0.0, on_usage=usage.update)
        text = client.complete("be terse", "return json")

    assert text == '{"proposals": []}'
    assert usage == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
