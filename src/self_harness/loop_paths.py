"""Single source of truth for where the continuous self-improvement loop runs.

The loop operates on the central SelfHarness checkout (its corpus + evolving harness live there). Both the
foreground runner and the background daemon resolve the root the same way so a backgrounded loop and a
``loop status`` from another directory agree on the same pidfile, log, and runs directory.
"""

from __future__ import annotations

from pathlib import Path


def loop_root() -> Path:
    """The directory the loop runs in: the central checkout if it has a corpus, else the cwd."""

    central = Path.home() / "Documents" / "SelfHarness"
    if (central / "examples" / "agentic_corpus.json").is_file():
        return central
    return Path.cwd()
