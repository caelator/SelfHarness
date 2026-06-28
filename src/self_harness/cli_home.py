"""Interactive home menu, settings editor, and descriptive help for the SelfHarness CLI.

Typing bare ``self-harness`` lands here: a numbered menu that reaches every everyday capability —
the coding agent, the continuous self-improvement loop, the web console, settings (including the API
key), and a plain-language help system — without anyone needing to remember a single flag. Everything
the menu does is also available non-interactively (``self-harness settings ...``, ``self-harness help
[topic]``) so scripts and power users are not forced through the menu.

This module owns presentation only; it calls back into ``cli`` for the actual run entry points.
"""

from __future__ import annotations

import sys
from pathlib import Path

from self_harness import user_config

# ---------------------------------------------------------------------------------------------------
# Help content — plain-language, grouped, not the raw argparse dump.
# ---------------------------------------------------------------------------------------------------

_TAGLINE = "SelfHarness — a GLM 5.2 coding agent that improves its own harness as you use it."

HELP_TOPICS: dict[str, str] = {
    "overview": f"""\
{_TAGLINE}

What it is
  A terminal coding assistant powered by GLM 5.2, wrapped in a "self-harness" loop: the
  instructions that steer the model (its harness) are not fixed — the system mines the agent's
  own failures and rewrites the harness to do better, keeping only changes that measurably help.

The two things you'll use most
  • Code   — an interactive coding agent that works in your current folder (writes files, runs
             commands, fixes tests). Like Claude Code / Codex, but on GLM 5.2.
  • Loop   — the continuous self-improvement loop. It practices on a task corpus, learns from
             failures (including ones harvested from your real coding sessions), and evolves the
             harness. Runs in the background; safe to leave on.

Everything else
  • Console  — a local web dashboard for launching/inspecting runs and the harness's evolution.
  • Settings — your API key, model, and defaults (no need to set environment variables).
  • Help     — this system. `self-harness help <topic>` for any topic below.

Topics: overview, code, loop, console, settings, key, flywheel, safety, commands
""",
    "code": """\
Code — the interactive coding agent
  Run it:   self-harness code         (or pick [1] Code from the menu)
  It opens a chat in your CURRENT directory. GLM 5.2 can read files, write files, and run shell
  commands to accomplish what you ask — then verifies its own work.

  In the chat:
    • Type plainly: "add a --json flag and run the tests".
    • @path/to/file        inline a file's contents into your message.
    • /help                list in-chat commands
    • /sessions, --resume  past sessions are saved; resume the most recent with `code --resume`.
    • /exit                leave.

  Auto-run: commands execute directly on your machine (no sandbox). Use it on code you trust.
  Flywheel: failing tests/builds it hits are harvested so the Loop can learn from them.
""",
    "loop": """\
Loop — continuous self-improvement
  Start/stop it from the menu ([2] Loop) or run the web console and click "Start continuous loop".
  What it does, on repeat:
    1. Runs GLM 5.2 against a corpus of coding tasks (Codex CLI judges each result).
    2. Finds patterns in what failed.
    3. Proposes edits to the harness (the agent's own steering instructions).
    4. Keeps an edit ONLY if it helps held-in tasks without hurting a held-out yardstick.
    5. Bakes accepted edits into the next iteration — so it's monotonic by construction.
  It also drains failures harvested from your real `code` sessions, so it gets better at YOUR work.
  Leave it running; it never regresses the held-out set.
""",
    "console": """\
Console — the web dashboard
  Run it:   self-harness ui      (then open the printed http://127.0.0.1:8765 URL)
  A single-page operator console (works offline) to launch agentic runs, watch the harness evolve
  round by round, hand GLM a one-off dev task, chat with it, and start/stop the continuous loop.
""",
    "settings": """\
Settings — configuration without environment variables
  Open the editor:   self-harness settings        (or [4] Settings in the menu)
  Non-interactive:
    self-harness settings show              show all settings (API key masked)
    self-harness settings get <key>
    self-harness settings set <key> <value>
    self-harness settings path              print the config file location
  Stored in ~/.config/self-harness/config.json (owner-only, 0600).
  Keys: api_key, base_url, model, max_steps, tool_timeout_seconds, auto_promote, harvest,
        share_central_harness.
""",
    "key": """\
API key — connecting to GLM 5.2
  SelfHarness talks to GLM 5.2 via a Z.ai coding-plan key.
  Set it the easy way:   self-harness settings set api_key <YOUR_KEY>
  or interactively:      self-harness settings   →  set the API key
  Resolution order: ZAI_API_KEY environment variable → saved config file. So an exported env var
  still wins if you use one, but you don't need it — the saved key is read automatically.
  The key is stored owner-only and never printed in full (only a short fingerprint).
""",
    "flywheel": """\
The flywheel — why this gets better the more you use it
  1. You code with `self-harness code`. When GLM hits a failing test/build, that failure is
     captured into a shared inbox (runs/inbox/).
  2. The Loop drains the inbox, turning real failures into practice tasks.
  3. It evolves the harness to handle them — keeping only changes that measurably help.
  4. Your next coding session uses the improved harness.
  The coding agent and the loop share one harness file, so improvements flow straight back to you.
""",
    "safety": """\
Safety — what to know
  • Host execution: the coding agent and agentic runs execute model-generated shell commands
    directly on your machine (only a temp working dir + per-command timeout). Run on trusted code.
  • The continuous loop only PROMOTES a harness edit if it does not regress the held-out task set,
    so it cannot make the agent worse on that yardstick.
  • Your API key is stored owner-only (0600) and shown only as a fingerprint.
  • This is real agentic evaluation, NOT a benchmark reproduction; the tool never claims otherwise.
""",
    "commands": """\
All commands
  Everyday:
    self-harness                 open the home menu (this)
    self-harness code            interactive coding agent (current folder)
    self-harness loop            start the continuous self-improvement loop
    self-harness ui              the web console
    self-harness settings        view/change configuration (incl. API key)
    self-harness help [topic]    this help system

  Advanced / power-user (run `self-harness <cmd> -h` for flags):
    demo, glm-agentic-demo, python-demo, http-demo, local-demo, container-demo,
    model-preflight, validate-tasks, inspect-harness,
    audit-summary, audit-verify, audit-verify-live, audit-migrate, audit-trajectory, audit-diff,
    benchmark-report, corpus-keygen, corpus-sign, corpus-fingerprint, corpus-keyring,
    operator-promotion, capture-manifest, capture-extract, capture-admit, verify-attestation,
    harbor-inspect, harbor-ingest, terminal-bench, terminal-bench-preflight, terminal-bench-capture
""",
}

_HELP_ALIASES = {
    "": "overview",
    "home": "overview",
    "intro": "overview",
    "agent": "code",
    "coding": "code",
    "improve": "loop",
    "continuous": "loop",
    "web": "console",
    "ui": "console",
    "config": "settings",
    "configuration": "settings",
    "apikey": "key",
    "api-key": "key",
    "api_key": "key",
    "all": "commands",
    "list": "commands",
}


def print_help(topic: str | None = None) -> int:
    """Print a help topic (default: overview). Unknown topics list the available ones."""

    key = (topic or "overview").strip().lower()
    key = _HELP_ALIASES.get(key, key)
    if key not in HELP_TOPICS:
        print(f"No help topic '{topic}'. Available topics:")
        print("  " + ", ".join(HELP_TOPICS))
        return 1
    print(HELP_TOPICS[key])
    return 0


# ---------------------------------------------------------------------------------------------------
# Settings — interactive editor + non-interactive get/set/show/path.
# ---------------------------------------------------------------------------------------------------

_SETTING_LABELS = {
    "api_key": "GLM 5.2 API key",
    "base_url": "API base URL",
    "model": "Model id",
    "max_steps": "Coding agent: max steps per turn",
    "tool_timeout_seconds": "Coding agent: per-command timeout (s)",
    "auto_promote": "Loop: auto-integrate accepted edits into source",
    "harvest": "Coding agent: harvest failures for the loop",
    "share_central_harness": "Code: share one central harness across all projects",
}


def run_settings(argv: list[str]) -> int:
    """Dispatch `self-harness settings [show|get|set|path|unset]`; no args → interactive editor."""

    cfg = user_config.load_config()
    if not argv:
        return _settings_interactive(cfg)

    action = argv[0]
    rest = argv[1:]
    if action == "path":
        print(cfg.path)
        return 0
    if action == "show":
        _print_settings(cfg)
        return 0
    if action == "get":
        if not rest:
            print("usage: self-harness settings get <key>", file=sys.stderr)
            return 2
        value = cfg.get(rest[0])
        if rest[0] in ("api_key",) and isinstance(value, str):
            value = user_config.mask_secret(value)
        print("" if value is None else value)
        return 0
    if action == "set":
        if len(rest) < 2:
            print("usage: self-harness settings set <key> <value>", file=sys.stderr)
            return 2
        try:
            cfg.set(rest[0], " ".join(rest[1:]))
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        cfg.save()
        shown = user_config.mask_secret(str(cfg.get(rest[0]))) if rest[0] == "api_key" else cfg.get(rest[0])
        print(f"set {rest[0]} = {shown}")
        return 0
    if action == "unset":
        if not rest:
            print("usage: self-harness settings unset <key>", file=sys.stderr)
            return 2
        cfg.unset(rest[0])
        cfg.save()
        print(f"unset {rest[0]}")
        return 0
    print(f"unknown settings action: {action} (use show|get|set|unset|path)", file=sys.stderr)
    return 2


def _print_settings(cfg: user_config.UserConfig) -> None:
    print(f"Settings  ({cfg.path})")
    red = cfg.redacted()
    for key in _SETTING_LABELS:
        label = _SETTING_LABELS[key]
        value = red.get(key, "(default)")
        print(f"  {key:<22} {value!s:<34} {label}")


def _settings_interactive(cfg: user_config.UserConfig) -> int:
    if not _interactive():
        _print_settings(cfg)
        print("\n(non-interactive: use `self-harness settings set <key> <value>`)")
        return 0
    while True:
        print()
        _print_settings(cfg)
        print("\nEnter a key to change (e.g. api_key), or 'q' to go back.")
        try:
            choice = input("settings › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in {"q", "quit", "exit", ""}:
            return 0
        if choice not in _SETTING_LABELS:
            print(f"unknown key: {choice}")
            continue
        secret = choice == "api_key"
        prompt = f"new value for {choice}" + (" (hidden)" if secret else "") + ": "
        try:
            if secret:
                import getpass

                value = getpass.getpass(prompt)
            else:
                value = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            continue
        if not value:
            print("(unchanged)")
            continue
        try:
            cfg.set(choice, value)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}")
            continue
        cfg.save()
        print(f"saved {choice}.")


# ---------------------------------------------------------------------------------------------------
# Home menu.
# ---------------------------------------------------------------------------------------------------

_MENU = """\
SelfHarness ▸ GLM 5.2
  [1] Code      — interactive coding agent (current folder)
  [2] Loop      — start/stop continuous self-improvement
  [3] Console   — open the web dashboard
  [4] Settings  — API key, model, defaults
  [5] Help      — what everything does
  [q] Quit"""


def run_home() -> int:
    """The interactive home menu shown for bare ``self-harness``."""

    if not _interactive():
        # Piped / non-tty: don't hang on input() — show help so the invocation is still useful.
        print(_TAGLINE)
        print()
        print_help("overview")
        return 0

    cfg = user_config.load_config()
    if not user_config.resolve_api_key(config=cfg):
        print("⚠ No GLM 5.2 API key set yet — choose [4] Settings to add it, or [5] Help → key.\n")

    while True:
        print()
        print(_MENU)
        try:
            choice = input("choose › ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in {"q", "quit", "exit"}:
            return 0
        if choice in {"1", "code", "c"}:
            _menu_code()
        elif choice in {"2", "loop", "l"}:
            _menu_loop()
        elif choice in {"3", "console", "ui"}:
            _menu_console()
        elif choice in {"4", "settings", "s"}:
            run_settings([])
        elif choice in {"5", "help", "h", "?"}:
            _menu_help()
        else:
            print(f"'{choice}' is not an option. Pick 1-5 or q.")


def _menu_code() -> None:
    from self_harness import cli

    print("\nLaunching the coding agent in:", Path.cwd())
    print("(type /exit inside to return to this menu)\n")
    try:
        cli.run_code_default()
    except Exception as exc:  # noqa: BLE001 - never let a sub-action crash the whole menu.
        print(f"error launching coding agent: {exc}")


def _menu_console() -> None:
    from self_harness import cli

    print("\nStarting the web console. Press Ctrl-C to stop it and return here.\n")
    try:
        cli.run_console_default()
    except KeyboardInterrupt:
        print("\n(console stopped)")
    except Exception as exc:  # noqa: BLE001
        print(f"error starting console: {exc}")


def _menu_loop() -> None:
    from self_harness import cli

    print()
    try:
        cli.run_loop_default()
    except KeyboardInterrupt:
        print("\n(loop stopped)")
    except Exception as exc:  # noqa: BLE001
        print(f"error running loop: {exc}")


def _menu_help() -> None:
    while True:
        print("\nHelp topics:", ", ".join(HELP_TOPICS))
        try:
            topic = input("help topic (enter=overview, q=back) › ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if topic in {"q", "quit", "exit"}:
            return
        print()
        print_help(topic or "overview")


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
