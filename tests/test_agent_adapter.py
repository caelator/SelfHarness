import json
from pathlib import Path

from self_harness.adapters.terminal_bench.agent_adapter import ClaudeCodeAgentAdapter, DeepAgentAdapter
from self_harness.harness import initial_harness


def test_claude_code_agent_adapter_uses_named_harbor_agent(tmp_path: Path) -> None:
    invocation = ClaudeCodeAgentAdapter().materialize(initial_harness(), tmp_path)

    assert invocation.agent_name == "claude-code"
    assert invocation.config_path is None


def test_deep_agent_adapter_writes_rendered_harness_config(tmp_path: Path) -> None:
    invocation = DeepAgentAdapter().materialize(initial_harness(), tmp_path)
    config = json.loads(invocation.config_path.read_text(encoding="utf-8")) if invocation.config_path else {}

    assert invocation.agent_name == "deepagent"
    assert invocation.config_path == tmp_path / "self-harness-agent-config.json"
    assert config["adapter"] == "self-harness-terminal-bench-dry-run"
    assert isinstance(config["config_hash"], str)
