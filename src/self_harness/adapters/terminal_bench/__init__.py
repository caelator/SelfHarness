"""Experimental Terminal-Bench/Harbor adapter scaffold.

This package lets Self-Harness speak a Terminal-Bench-shaped protocol in dry-run
mode. It is not a benchmark reproduction and live Harbor runs are best-effort
until captured live fixtures exist.
"""

from self_harness.adapters.terminal_bench.agent_adapter import (
    ClaudeCodeAgentAdapter,
    DeepAgentAdapter,
    HarborAgentInvocation,
)
from self_harness.adapters.terminal_bench.agent_render import render_agent_config
from self_harness.adapters.terminal_bench.capture import CaptureManifest, capture_single_task
from self_harness.adapters.terminal_bench.corpus import TerminalBenchCorpusAdapter, load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.harbor_artifacts import (
    HarborArtifactProvenance,
    HarborTrialRecord,
    discover_trials,
    inspect_run_dir,
)
from self_harness.adapters.terminal_bench.harbor_command import HarborCommandSpec, build_harbor_run_command
from self_harness.adapters.terminal_bench.harbor_output import HarborRunResult, parse_harbor_output
from self_harness.adapters.terminal_bench.ingest import ingest_harbor_run
from self_harness.adapters.terminal_bench.preflight import PreflightCheck, PreflightReport, run_preflight
from self_harness.adapters.terminal_bench.runner import (
    HarborRunner,
    validate_harbor_image_trust,
    validate_harbor_live_container_digest,
)

__all__ = [
    "CaptureManifest",
    "ClaudeCodeAgentAdapter",
    "DeepAgentAdapter",
    "HarborRunner",
    "HarborRunResult",
    "HarborAgentInvocation",
    "HarborArtifactProvenance",
    "HarborCommandSpec",
    "HarborTrialRecord",
    "PreflightCheck",
    "PreflightReport",
    "TerminalBenchCorpusAdapter",
    "build_harbor_run_command",
    "capture_single_task",
    "discover_trials",
    "ingest_harbor_run",
    "inspect_run_dir",
    "load_terminal_bench_manifest",
    "parse_harbor_output",
    "render_agent_config",
    "run_preflight",
    "validate_harbor_image_trust",
    "validate_harbor_live_container_digest",
]
