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
from self_harness.console_style import console

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

  Run it in the background (survives closing the terminal):
    self-harness loop --background     start detached
    self-harness loop status           is it running? + recent activity
    self-harness loop stop             stop gracefully (finishes the current run first)
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
        console.status(f"No help topic '{topic}'. Available topics:", "warn")
        console.line("  " + ", ".join(HELP_TOPICS), "accent")
        return 1
    _render_help_body(HELP_TOPICS[key])
    return 0


def _render_help_body(body: str) -> None:
    """Print a help topic, styling section headings (unindented non-blank lines) and command examples."""

    for line in body.splitlines():
        if not line.strip():
            console.blank()
        elif not line.startswith(" "):
            # Top-level heading line (e.g. "What it is", "Code — the interactive coding agent").
            console.line(line, "heading")
        else:
            console.line(line)


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
        print(cfg.path)  # machine-readable: keep plain
        return 0
    if action == "show":
        _print_settings(cfg)
        return 0
    if action == "get":
        if not rest:
            console.error("usage: self-harness settings get <key>")
            return 2
        value = cfg.get(rest[0])
        if rest[0] in ("api_key",) and isinstance(value, str):
            value = user_config.mask_secret(value)
        print("" if value is None else value)  # machine-readable: keep plain
        return 0
    if action == "set":
        if len(rest) < 2:
            console.error("usage: self-harness settings set <key> <value>")
            return 2
        try:
            cfg.set(rest[0], " ".join(rest[1:]))
        except (KeyError, ValueError) as exc:
            console.error(str(exc))
            return 2
        cfg.save()
        shown = user_config.mask_secret(str(cfg.get(rest[0]))) if rest[0] == "api_key" else cfg.get(rest[0])
        console.status(f"set {rest[0]} = {shown}", "success")
        return 0
    if action == "unset":
        if not rest:
            console.error("usage: self-harness settings unset <key>")
            return 2
        cfg.unset(rest[0])
        cfg.save()
        console.status(f"unset {rest[0]}", "success")
        return 0
    console.error(f"unknown settings action: {action} (use show|get|set|unset|path)")
    return 2


def _print_settings(cfg: user_config.UserConfig) -> None:
    console.heading("Settings")
    console.line(str(cfg.path), "system")
    red = cfg.redacted()
    rows: list[tuple[str, ...]] = []
    for key in _SETTING_LABELS:
        value = red.get(key, "(default)")
        rows.append((key, str(value), _SETTING_LABELS[key]))
    console.table(rows, headers=("setting", "value", "what it controls"))


def _settings_interactive(cfg: user_config.UserConfig) -> int:
    if not _interactive():
        _print_settings(cfg)
        console.line("\n(non-interactive: use `self-harness settings set <key> <value>`)", "system")
        return 0
    while True:
        console.blank()
        _print_settings(cfg)
        console.line("\nEnter a key to change (e.g. api_key), or 'q' to go back.", "system")
        try:
            choice = console.prompt("settings › ", "user").strip()
        except (EOFError, KeyboardInterrupt):
            console.blank()
            return 0
        if choice in {"q", "quit", "exit", ""}:
            return 0
        if choice not in _SETTING_LABELS:
            console.status(f"unknown key: {choice}", "warn")
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
            console.blank()
            continue
        if not value:
            console.line("(unchanged)", "system")
            continue
        try:
            cfg.set(choice, value)
        except (KeyError, ValueError) as exc:
            console.error(str(exc))
            continue
        cfg.save()
        console.status(f"saved {choice}.", "success")


# ---------------------------------------------------------------------------------------------------
# Home menu.
# ---------------------------------------------------------------------------------------------------

_MENU_ITEMS = (
    ("1", "Code", "interactive coding agent (current folder)"),
    ("2", "Loop", "start/stop continuous self-improvement"),
    ("3", "Console", "open the web dashboard"),
    ("4", "Settings", "API key, model, defaults"),
    ("5", "Help", "what everything does"),
    ("q", "Quit", ""),
)


def _show_menu() -> None:
    console.blank()
    console.heading("SelfHarness ▸ GLM 5.2")
    rows = [(f"[{key}]", name, desc) for key, name, desc in _MENU_ITEMS]
    console.table(rows)


def run_home() -> int:
    """The interactive home menu shown for bare ``self-harness``."""

    if not _interactive():
        # Piped / non-tty: don't hang on input() — show help so the invocation is still useful.
        console.heading(_TAGLINE)
        console.blank()
        print_help("overview")
        return 0

    cfg = user_config.load_config()
    if not user_config.resolve_api_key(config=cfg):
        console.blank()
        console.status(
            "No GLM 5.2 API key set yet — choose [4] Settings to add it, or [5] Help → key.", "warn"
        )

    while True:
        _show_menu()
        try:
            choice = console.prompt("choose › ", "user").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.blank()
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
            console.status(f"'{choice}' is not an option. Pick 1-5 or q.", "warn")


def _menu_code() -> None:
    from self_harness import cli

    console.blank()
    console.line(f"Launching the coding agent in: {Path.cwd()}", "system")
    console.line("(type /exit inside to return to this menu)", "system")
    console.blank()
    try:
        cli.run_code_default()
    except Exception as exc:  # noqa: BLE001 - never let a sub-action crash the whole menu.
        console.error(f"error launching coding agent: {exc}")


def _menu_console() -> None:
    from self_harness import cli

    console.blank()
    console.line("Starting the web console. Press Ctrl-C to stop it and return here.", "system")
    console.blank()
    try:
        cli.run_console_default()
    except KeyboardInterrupt:
        console.status("console stopped", "system")
    except Exception as exc:  # noqa: BLE001
        console.error(f"error starting console: {exc}")


def _menu_loop() -> None:
    from self_harness import cli, loop_daemon

    running = loop_daemon.is_running()
    console.blank()
    if running is not None:
        console.status(f"The continuous loop is running in the background (pid {running}).", "success")
        console.line("  [s] show status + recent activity")
        console.line("  [x] stop it")
        console.line("  [enter] back")
        try:
            choice = console.prompt("loop › ", "user").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.blank()
            return
        if choice in {"s", "status"}:
            loop_daemon.status()
        elif choice in {"x", "stop"}:
            loop_daemon.stop_background()
        return

    console.line("Start the continuous self-improvement loop:", "heading")
    console.line("  [f] foreground — watch it live (Ctrl-C to stop)")
    console.line("  [b] background — keep running after you close the terminal")
    console.line("  [enter] back")
    try:
        choice = console.prompt("loop › ", "user").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.blank()
        return
    if choice in {"b", "background", "bg"}:
        loop_daemon.start_background()
    elif choice in {"f", "foreground", "fg"}:
        try:
            cli.run_loop_default()
        except KeyboardInterrupt:
            console.status("loop stopped", "system")
        except Exception as exc:  # noqa: BLE001
            console.error(f"error running loop: {exc}")


def _menu_help() -> None:
    while True:
        console.blank()
        console.line("Help topics: " + ", ".join(HELP_TOPICS), "accent")
        try:
            topic = console.prompt("help topic (enter=overview, q=back) › ", "user").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.blank()
            return
        if topic in {"q", "quit", "exit"}:
            return
        console.blank()
        print_help(topic or "overview")


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
