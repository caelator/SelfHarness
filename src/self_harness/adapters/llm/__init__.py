"""Optional LLM provider adapters."""

from self_harness.adapters.llm.anthropic import AnthropicClaudeClient
from self_harness.adapters.llm.paper_models import (
    GLM5_SPEC,
    MINIMAX_M25_SPEC,
    QWEN35_35B_A3B_SPEC,
    GLMClient,
    MiniMaxClient,
    OpenAICompatiblePaperModelClient,
    PaperModelBackendSpec,
    QwenClient,
)

__all__ = [
    "AnthropicClaudeClient",
    "GLM5_SPEC",
    "GLMClient",
    "MINIMAX_M25_SPEC",
    "MiniMaxClient",
    "OpenAICompatiblePaperModelClient",
    "PaperModelBackendSpec",
    "QWEN35_35B_A3B_SPEC",
    "QwenClient",
]
