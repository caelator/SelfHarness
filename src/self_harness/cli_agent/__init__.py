"""Interactive GLM 5.2 dev CLI built on the Self-Harness agentic engine.

`self-harness code` opens a multi-turn coding session in the current directory: GLM 5.2 acts with the
bash/read_file/write_file tools against the real repo, driven by the *evolving* harness (so the CLI gets
better as the harness improves). Failing check commands are harvested into the shared inbox, feeding the
continuous self-improvement loop — the flywheel that distinguishes this from a static-harness CLI.
"""

from __future__ import annotations

from self_harness.cli_agent.harvest import FailureHarvester
from self_harness.cli_agent.repl import run_repl
from self_harness.cli_agent.session import InteractiveSession, TurnResult

__all__ = ["FailureHarvester", "InteractiveSession", "TurnResult", "run_repl"]
