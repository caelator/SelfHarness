from __future__ import annotations

from collections.abc import Mapping

import pytest

from self_harness.adapters.llm.paper_models import GLM5_SPEC, GLMClient
from self_harness.exceptions import LLMClientError


class FakeTransport:
    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        assert payload["model"] == "glm-5.2"
        return {"choices": [{"message": {"content": "glm proposal"}}]}


def test_glm_client_contract_uses_zai_credentials_boundary() -> None:
    client = GLMClient(transport=FakeTransport())

    assert client.spec == GLM5_SPEC
    assert client.spec.credential_env == "ZAI_API_KEY"
    assert client.spec.endpoint_env == "ZAI_BASE_URL"
    assert client.spec.access_mode == "zai_hosted_api"
    assert client.complete("system", "user") == "glm proposal"


def test_paper_model_client_without_transport_does_not_contact_provider() -> None:
    client = GLMClient()

    with pytest.raises(LLMClientError, match="operator-supplied chat-completions transport"):
        client.complete("system", "user")
