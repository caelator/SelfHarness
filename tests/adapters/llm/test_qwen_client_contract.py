from __future__ import annotations

from collections.abc import Mapping

from self_harness.adapters.llm.paper_models import QWEN35_35B_A3B_SPEC, QwenClient


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, object]] = []

    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append(payload)
        return {"choices": [{"message": {"content": "qwen proposal"}}]}


def test_qwen_client_contract_targets_operator_provisioned_sglang() -> None:
    transport = FakeTransport()
    client = QwenClient(transport=transport)

    assert client.spec == QWEN35_35B_A3B_SPEC
    assert client.spec.credential_env is None
    assert client.spec.access_mode == "operator_provisioned_sglang"
    assert client.complete("system", "user") == "qwen proposal"
    assert transport.calls[0]["model"] == "qwen3.5-35b-a3b"
    assert transport.calls[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
