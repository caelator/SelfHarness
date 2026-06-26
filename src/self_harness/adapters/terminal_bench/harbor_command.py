from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

HARBOR_PROTOCOL_VERSION = "2.0"


@dataclass(frozen=True)
class HarborCommandSpec:
    dataset: str
    agent_name: str
    model: str
    n_concurrent: int = 1
    cache_dir: Path | None = None
    cloud_env: str | None = None
    task_ids: tuple[str, ...] = ()
    agent_config_path: Path | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.dataset:
            raise ValueError("dataset must be non-empty")
        if not self.agent_name:
            raise ValueError("agent_name must be non-empty")
        if not self.model:
            raise ValueError("model must be non-empty")
        if self.n_concurrent < 1:
            raise ValueError("n_concurrent must be at least 1")


def build_harbor_run_command(
    spec: HarborCommandSpec,
    *,
    harbor_executable: str = "harbor",
) -> list[str]:
    command = [
        harbor_executable,
        "run",
        "--dataset",
        spec.dataset,
        "--agent",
        spec.agent_name,
        "--model",
        spec.model,
        "--n-concurrent",
        str(spec.n_concurrent),
    ]
    if spec.cache_dir is not None:
        command.extend(["--cache-dir", str(spec.cache_dir)])
    if spec.cloud_env is not None:
        command.extend(["--env", spec.cloud_env])
    if spec.agent_config_path is not None:
        command.extend(["--agent-config", str(spec.agent_config_path)])
    for task_id in spec.task_ids:
        command.extend(["--task", task_id])
    command.extend(spec.extra_args)
    return command
