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
from typing import Any

from self_harness import user_config
from self_harness.console_style import console

# ---------------------------------------------------------------------------------------------------
# Help content — plain-language, grouped, not the raw argparse dump.
# ---------------------------------------------------------------------------------------------------

_TAGLINE = (
    "SelfHarness — a coding agent that improves its own harness as you use it "
    "(GLM 5.2 or headless codex/agy/claude)."
)

HELP_TOPICS: dict[str, str] = {
    "overview": f"""\
{_TAGLINE}

What it is
  A terminal coding assistant powered by GLM 5.2 or a headless local CLI provider (codex, agy,
  claude), wrapped in a "self-harness" loop: the instructions that steer the model (its harness)
  are not fixed — the system mines the agent's own failures and rewrites the harness to do better,
  keeping only changes that measurably help.

The two things you'll use most
  • Code   — an interactive coding agent that works in your current folder (writes files, runs
             commands, fixes tests) with an in-CLI command palette and thread picker.
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
  It opens a chat in your CURRENT directory. The configured provider (GLM, codex, agy, or claude)
  can read files, write files, and run shell commands to accomplish what you ask.

  In the chat:
    • Type plainly: "add a --json flag and run the tests".
    • @path/to/file        inline a file's contents into your message.
    • /menu                open the TUI command palette.
    • /model               pick provider/model/effort from inside the CLI.
    • /threads             list, create, and switch conversation threads.
    • /config              edit runtime options such as max steps, timeout, and harvesting.
    • /status, /history    inspect the active thread and recent turns.
    • /exit, /quit, /q     leave. Ctrl-C exits at the prompt; during a turn it interrupts.

  Auto-run: commands execute directly on your machine (no sandbox). Use it on code you trust.
  Flywheel: failing tests/builds it hits are harvested so the Loop can learn from them. By default
  `code` shares ONE central harness + failure inbox (under ~/Documents/SelfHarness/runs) across every
  project, so the loop learns from your real sessions; pass --local-harness for a per-project one.
  Long tasks: if the agent reaches its per-turn step budget it auto-continues a few times instead of
  stopping; type a new instruction any time to redirect.
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
  Keys: api_key, base_url, model, code_provider, code_model, code_effort, max_steps,
        tool_timeout_seconds, auto_promote, harvest, share_central_harness, loop_eval_repeats.
  `self-harness code` also exposes these through /menu, /model, and /config without leaving the TUI.
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
    self-harness save            save current workspace as a project
    self-harness resume <#>      resume a saved project
    self-harness projects        list saved projects
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
    "model": "Legacy model/provider id",
    "code_provider": "Code: active provider (glm, codex, agy, claude)",
    "code_model": "Code: provider model override",
    "code_effort": "Code: reasoning effort override",
    "max_steps": "Coding agent: max steps per turn",
    "tool_timeout_seconds": "Coding agent: per-command timeout (s)",
    "auto_promote": "Loop: auto-integrate accepted edits into source",
    "harvest": "Coding agent: harvest failures for the loop",
    "share_central_harness": "Code: share one central harness across all projects",
    "loop_eval_repeats": "Loop: times each task is attempted per evaluation (higher = less noise)",
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
    ("3", "Projects", "save / resume work snapshots"),
    ("4", "Console", "open the web dashboard"),
    ("5", "Settings", "API key, model, defaults"),
    ("6", "Help", "what everything does"),
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
        elif choice in {"3", "projects", "p"}:
            _menu_projects()
        elif choice in {"4", "console", "ui"}:
            _menu_console()
        elif choice in {"5", "settings", "s"}:
            run_settings([])
        elif choice in {"6", "help", "h", "?"}:
            _menu_help()
        else:
            console.status(f"'{choice}' is not an option. Pick 1-6 or q.", "warn")


def _menu_projects() -> None:
    """Save/resume project snapshots with a polished interactive UI."""
    from self_harness import project_manager

    while True:
        console.blank()
        console.rule("Projects")
        console.blank()

        projects = project_manager.list_projects()

        # ── Save current ──
        console.line("[s] Save current workspace", "accent")

        if projects:
            console.blank()
            console.line("Saved projects:", "heading")
            console.blank()
            rows: list[tuple[str, str, str, str, str]] = []
            for i, p in enumerate(projects, 1):
                # Status indicator
                if p.held_in_score is not None:
                    score_str = f"{p.held_in_score:.0%}"
                else:
                    score_str = "—"
                # Truncate name for display
                name_display = p.name[:30] if len(p.name) > 30 else p.name
                # Working dir basename
                dir_display = Path(p.working_dir).name if p.working_dir else "?"
                rows.append((
                    f"  [{i}]",
                    name_display,
                    dir_display,
                    score_str,
                    p.saved_at,
                ))
            console.table(rows, headers=["", "Name", "Dir", "Score", "Saved"])
            console.blank()
            console.line("  Enter a number to resume, or 'd <#>' to delete", "system")
        else:
            console.blank()
            console.line("  No saved projects yet.", "system")

        console.blank()
        console.line("  [enter] back to menu", "system")

        try:
            choice = console.prompt("projects › ", "user").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.blank()
            return

        if choice == "":
            return

        if choice in {"s", "save"}:
            _menu_save_project()
            continue

        # Delete: "d 3" or "delete 3"
        if choice.startswith("d ") or choice.startswith("delete "):
            num_str = choice.split()[-1]
            project = project_manager.load_project(num_str)
            if project:
                project_manager.delete_project(project.id)
                console.status(f"Deleted '{project.name}'", "success")
            else:
                console.status(f"No project at #{num_str}", "warn")
            continue

        # Resume by number or name
        project = project_manager.load_project(choice)
        if project is None:
            console.status(f"No project matching '{choice}'", "warn")
            continue

        _menu_resume_project(project)


def _menu_save_project() -> None:
    """Save the current workspace as a project snapshot."""
    from self_harness import project_manager
    from self_harness.loop_paths import central_runs_dir

    console.blank()
    console.line("Save current workspace", "heading")
    console.blank()

    # Name
    default_name = Path.cwd().name
    try:
        name = console.prompt(f"project name ({default_name}) › ", "user").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not name:
        name = default_name

    # Notes
    try:
        notes = console.prompt("notes (what were you working on?) › ", "user").strip()
    except (EOFError, KeyboardInterrupt):
        notes = ""

    # Load harness state if available
    harness_state: dict | None = None
    runs_dir = central_runs_dir()
    if runs_dir and runs_dir is not None:
        state_file = runs_dir / "harness_state.json"
        if state_file.is_file():
            try:
                import json
                harness_state = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    # Find corpus
    corpus_path: str | None = None
    for candidate in [Path.cwd() / "examples" / "agentic_corpus.json",
                      Path.cwd() / "examples" / "local_corpus.json"]:
        if candidate.is_file():
            corpus_path = str(candidate)
            break

    # Count rounds from runs dir
    rounds = 0
    if runs_dir:
        rounds_dir = runs_dir / "rounds"
        if rounds_dir.is_dir():
            rounds = len(list(rounds_dir.iterdir()))

    # Extract scores from harness state if available
    held_in: float | None = None
    held_out: float | None = None

    project = project_manager.save_project(
        name=name,
        working_dir=str(Path.cwd()),
        corpus_path=corpus_path,
        harness_state=harness_state,
        rounds_completed=rounds,
        notes=notes,
        held_in_score=held_in,
        held_out_score=held_out,
    )

    # Commit, merge, and push to GitHub
    console.blank()
    console.line("Syncing to GitHub...", "system")
    sync = project_manager.git_sync(
        str(Path.cwd()),
        f"save project: {name}",
    )

    console.status(f"Saved '{name}'", "success")
    console.line(f"  directory: {Path.cwd()}", "system")
    if harness_state:
        console.line(f"  harness:  captured ({rounds} rounds)", "system")

    # Git status
    if sync.committed:
        sha = sync.commit_sha or "?"
        console.status(f"committed {sha}", "success")
    if sync.merged:
        console.status(f"merged {', '.join(sync.remote_ahead)} from remote", "success")
    if sync.pushed:
        console.status("pushed to origin", "success")
    if sync.errors:
        for err in sync.errors:
            console.status(err, "warn")

    console.line(f"  resume:   self-harness resume {project.id.split('-')[-1]}", "accent")
    console.blank()


def _menu_resume_project(project: Any) -> None:
    """Show project details and offer to resume."""
    console.blank()
    console.rule(f"Resume: {project.name}")

    console.blank()
    console.line(f"  directory:   {project.working_dir}", "system")
    console.line(f"  saved:       {project.saved_at}", "system")
    if project.corpus_path:
        console.line(f"  corpus:      {project.corpus_path}", "system")
    console.line(f"  rounds done: {project.rounds_completed}", "system")
    if project.notes:
        console.blank()
        console.panel(project.notes, title="Notes", role="accent")
    console.blank()

    # Check if the directory exists
    target_dir = Path(project.working_dir)
    if not target_dir.is_dir():
        console.error(f"Directory does not exist: {target_dir}")
        console.line("  The project was saved from a location that is no longer available.", "warn")
        console.blank()
        console.line("  [enter] back", "system")
        try:
            console.prompt("› ", "user")
        except (EOFError, KeyboardInterrupt):
            pass
        return

    console.line(f"  [r] resume — cd to {target_dir.name} and continue", "accent")
    console.line("  [c] code  — open the coding agent there", "accent")
    console.line("  [l] loop  — start the self-improvement loop there", "accent")
    console.line("  [enter] back", "system")

    try:
        choice = console.prompt("resume › ", "user").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return

    if choice in {"r", "resume", "c", "code", "l", "loop"}:
        # Change to the project directory
        import os
        os.chdir(target_dir)
        console.status(f"Switched to: {target_dir}", "success")

        if choice in {"c", "code"}:
            _menu_code()
        elif choice in {"l", "loop"}:
            _menu_loop()
        else:
            console.blank()
            console.line(f"You're now in: {target_dir}", "heading")
            console.line("  The coding agent, loop, and console will use this directory.", "system")
            console.line("  Type 'self-harness code' to start coding, or pick from the menu.", "system")
            console.blank()


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
