"""The interactive read-eval-print loop for `self-harness code`."""

from __future__ import annotations

import sys

from self_harness.cli_agent.session import InteractiveSession

_HELP = """\
Commands:
  /help        show this help
  /harness     show the active harness (hash + whether it is the evolving lineage)
  /harvested   list failure bundles harvested this session (fed to the improvement loop)
  /cwd         show the working directory
  /reset       clear the conversation history
  /exit /quit  leave (or Ctrl-D)
Anything else is sent to GLM 5.2, which acts in the working directory with bash/read/write tools."""


def run_repl(session: InteractiveSession, *, banner: bool = True) -> int:
    """Drive the interactive session until the user exits. Returns a process exit code."""

    if banner:
        lineage = "evolving lineage" if session.evolving else "initial_harness() (Figure 3)"
        print("SelfHarness Code — GLM 5.2 dev agent")
        print(f"  cwd: {session.workdir}")
        print(f"  harness: {session.harness_hash[:16]} ({lineage})")
        print(f"  harvest: {'on' if session.harvester.enabled else 'off'} -> {session.harvester.inbox_dir}")
        print("Type /help for commands, /exit to quit.\n")

    while True:
        try:
            line = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break
        if line == "/help":
            print(_HELP)
            continue
        if line == "/cwd":
            print(session.workdir)
            continue
        if line == "/reset":
            session.reset()
            print("(history cleared)")
            continue
        if line == "/harness":
            lineage = "evolving lineage" if session.evolving else "initial_harness() (Figure 3)"
            print(f"harness {session.harness_hash} ({lineage})")
            continue
        if line == "/harvested":
            ids = session.harvester.written_ids
            print(f"harvested {len(ids)} failure bundle(s) this session" + (": " + ", ".join(ids) if ids else ""))
            continue
        if line.startswith("/"):
            print(f"unknown command: {line} (try /help)")
            continue

        _run_turn(session, line)

    _farewell(session)
    return 0


def _run_turn(session: InteractiveSession, line: str) -> None:
    try:
        result = session.send(line)
    except Exception as exc:  # noqa: BLE001 - surface any transport/runtime error without crashing the REPL.
        print(f"  ! error: {exc}\n", file=sys.stderr)
        return

    for activity in result.tool_activity:
        print(f"  · {activity}")
    if result.error:
        print(f"  ! {result.error}")
    text = result.final_text.strip()
    print(f"\nglm › {text}\n" if text else "\nglm › (no text; stopped: " + result.stop_reason + ")\n")
    if result.harvested:
        print(f"  ⤷ harvested {len(result.harvested)} failing command(s) into the improvement inbox\n")


def _farewell(session: InteractiveSession) -> None:
    ids = session.harvester.written_ids
    if ids:
        print(f"Harvested {len(ids)} failure bundle(s) this session -> {session.harvester.inbox_dir}")
        print("Run the continuous loop (self-harness ui, Start continuous loop) to learn from them.")
