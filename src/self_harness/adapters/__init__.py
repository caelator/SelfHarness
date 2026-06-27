"""Adapters for real Self-Harness integrations."""

from self_harness.adapters.base import TaskAdapter
from self_harness.adapters.container_verifier import (
    ContainerCommandSpec,
    ContainerVerifierRunner,
    ContainerVerifierTaskAdapter,
    build_container_run_command,
)
from self_harness.adapters.http_verifier import HttpVerifierRunner, HttpVerifierTaskAdapter
from self_harness.adapters.in_process_python import (
    InProcessPythonRunner,
    InProcessPythonTaskAdapter,
    load_trusted_module,
)
from self_harness.adapters.local_subprocess import LocalSubprocessRunner, LocalSubprocessTaskAdapter, load_tasks_json
from self_harness.adapters.verifier_result import VerifierResult

__all__ = [
    "ContainerCommandSpec",
    "ContainerVerifierRunner",
    "ContainerVerifierTaskAdapter",
    "HttpVerifierRunner",
    "HttpVerifierTaskAdapter",
    "InProcessPythonRunner",
    "InProcessPythonTaskAdapter",
    "LocalSubprocessRunner",
    "LocalSubprocessTaskAdapter",
    "TaskAdapter",
    "VerifierResult",
    "build_container_run_command",
    "load_tasks_json",
    "load_trusted_module",
]
