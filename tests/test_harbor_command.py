from pathlib import Path

import pytest

from self_harness.adapters.terminal_bench.harbor_command import HarborCommandSpec, build_harbor_run_command


def test_harbor_command_matches_documented_local_example() -> None:
    command = build_harbor_run_command(
        HarborCommandSpec(
            dataset="terminal-bench@2.0",
            agent_name="claude-code",
            model="anthropic/claude-opus-4-1",
            n_concurrent=4,
        )
    )

    assert command == [
        "harbor",
        "run",
        "--dataset",
        "terminal-bench@2.0",
        "--agent",
        "claude-code",
        "--model",
        "anthropic/claude-opus-4-1",
        "--n-concurrent",
        "4",
    ]


def test_harbor_command_matches_documented_cloud_env_example() -> None:
    command = build_harbor_run_command(
        HarborCommandSpec(
            dataset="terminal-bench@2.0",
            agent_name="claude-code",
            model="anthropic/claude-opus-4-1",
            n_concurrent=100,
            cloud_env="daytona",
        )
    )

    assert command == [
        "harbor",
        "run",
        "--dataset",
        "terminal-bench@2.0",
        "--agent",
        "claude-code",
        "--model",
        "anthropic/claude-opus-4-1",
        "--n-concurrent",
        "100",
        "--env",
        "daytona",
    ]


def test_harbor_command_supports_task_and_agent_config_extensions(tmp_path: Path) -> None:
    command = build_harbor_run_command(
        HarborCommandSpec(
            dataset="terminal-bench@2.0",
            agent_name="deepagent",
            model="anthropic/claude-haiku-4-5",
            cache_dir=tmp_path / "cache",
            task_ids=("task-a",),
            agent_config_path=tmp_path / "agent.json",
        ),
        harbor_executable="/tmp/harbor",
    )

    assert command[:10] == [
        "/tmp/harbor",
        "run",
        "--dataset",
        "terminal-bench@2.0",
        "--agent",
        "deepagent",
        "--model",
        "anthropic/claude-haiku-4-5",
        "--n-concurrent",
        "1",
    ]
    assert ["--task", "task-a"] == command[-2:]
    assert "--agent-config" in command


def test_harbor_command_rejects_invalid_concurrency() -> None:
    with pytest.raises(ValueError):
        HarborCommandSpec(
            dataset="terminal-bench@2.0",
            agent_name="claude-code",
            model="anthropic/claude-opus-4-1",
            n_concurrent=0,
        )
