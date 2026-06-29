"""Interactive dev CLI built on the Self-Harness agentic engine.

`self-harness code` opens a multi-turn coding session in the current directory. It can use GLM 5.2 via
the native agentic loop or a headless local coding CLI backend, driven by the *evolving* harness (so the
CLI gets better as the harness improves). Failing check commands and admitted semantic UX failures are
harvested into the shared inbox, feeding the continuous self-improvement loop — the flywheel that
distinguishes this from a static-harness CLI.
"""

from __future__ import annotations

from self_harness.cli_agent.harvest import FailureHarvester
from self_harness.cli_agent.repl import run_repl
from self_harness.cli_agent.session import HeadlessCliSession, InteractiveSession, TurnResult
from self_harness.cli_agent.ux_harvest import SecondaryModelJudge, UxFailureHarvester

__all__ = [
    "FailureHarvester",
    "HeadlessCliSession",
    "InteractiveSession",
    "SecondaryModelJudge",
    "TurnResult",
    "UxFailureHarvester",
    "run_repl",
]
