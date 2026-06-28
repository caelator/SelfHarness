"""The interactive read-eval-print loop for `self-harness code`.

Drives a :class:`ConsoleRenderer` (rich TUI, or a plain fallback) over an :class:`InteractiveSession`:
streams GLM's reply token-by-token, renders a line per tool call, expands ``@file`` mentions into the
turn, and persists the session after every turn so it can be resumed later. Auto-run and failure
harvesting (the self-improvement flywheel) are unchanged from Phase 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from self_harness.cli_agent.context import expand_mentions
from self_harness.cli_agent.session import InteractiveSession
from self_harness.cli_agent.sessions import SessionRecord, list_sessions, save_session
from self_harness.cli_agent.ui import ConsoleRenderer

_HELP = """\
Commands:
  /help        show this help
  /harness     show the active harness (hash + whether it is the evolving lineage)
  /harvested   list failure bundles harvested this session (fed to the improvement loop)
  /sessions    list saved sessions you can resume (self-harness code --resume <id>)
  /cwd         show the working directory
  /reset       clear the conversation history
  /exit /quit  leave (or Ctrl-D)
Mention @path/to/file to inline that file's contents into your message.
Anything else is sent to GLM 5.2, which acts in the working directory with bash/read/write tools."""


def run_repl(
    session: InteractiveSession,
    *,
    banner: bool = True,
    root: Path | None = None,
    session_id: str = "code-adhoc",
    timestamp: str = "",
    plain: bool = False,
) -> int:
    """Drive the interactive session until the user exits. Returns a process exit code.

    ``root`` is the project root under which sessions are persisted (``<root>/runs/sessions``); when
    omitted, sessions are not saved (ad-hoc / test use). ``session_id``/``timestamp`` identify the saved
    record. ``plain`` forces the plain-text renderer.
    """

    renderer = ConsoleRenderer(plain=plain)
    record = SessionRecord(
        id=session_id,
        workdir=str(session.workdir),
        harness_hash=session.harness_hash,
        created_at=timestamp,
        updated_at=timestamp,
    )

    if banner:
        lineage = "evolving lineage" if session.evolving else "initial_harness() (Figure 3)"
        harvest = f"{'on' if session.harvester.enabled else 'off'} -> {session.harvester.inbox_dir}"
        renderer.banner(
            workdir=str(session.workdir),
            harness_hash=session.harness_hash,
            lineage=lineage,
            harvest=harvest,
        )

    while True:
        try:
            line = renderer.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break
        if line == "/help":
            renderer.info(_HELP)
            continue
        if line == "/cwd":
            renderer.info(str(session.workdir))
            continue
        if line == "/reset":
            session.reset()
            renderer.info("(history cleared)")
            continue
        if line == "/harness":
            lineage = "evolving lineage" if session.evolving else "initial_harness() (Figure 3)"
            renderer.info(f"harness {session.harness_hash} ({lineage})")
            continue
        if line == "/harvested":
            ids = session.harvester.written_ids
            renderer.info(
                f"harvested {len(ids)} failure bundle(s) this session"
                + (": " + ", ".join(ids) if ids else "")
            )
            continue
        if line == "/sessions":
            _list_sessions(renderer, root)
            continue
        if line.startswith("/"):
            renderer.info(f"unknown command: {line} (try /help)")
            continue

        _run_turn(session, line, renderer, record, root)

    _farewell(session, renderer, record, root)
    return 0


def _run_turn(
    session: InteractiveSession,
    line: str,
    renderer: ConsoleRenderer,
    record: SessionRecord,
    root: Path | None,
) -> None:
    augmented, inlined = expand_mentions(line, session.workdir)
    if inlined:
        renderer.info("  @ inlined: " + ", ".join(inlined))

    renderer.start_stream()
    streamed = False

    def _on_delta(delta: str) -> None:
        nonlocal streamed
        streamed = True
        renderer.push_delta(delta)

    def _on_tool_event(name: str, summary: str, ok: bool) -> None:
        # Each tool event commits the preceding text step, so streamed text and tool lines stay separated.
        renderer.tool_event(name, summary, ok)

    try:
        result = session.send(
            augmented,
            on_text_delta=_on_delta,
            on_tool_event=_on_tool_event,
        )
    except Exception as exc:  # noqa: BLE001 - surface any transport/runtime error without crashing the REPL.
        renderer.end_stream(stop_reason="error")
        renderer.error(f"  ! error: {exc}")
        return

    renderer.end_stream(fallback_text=result.final_text.strip(), stop_reason=result.stop_reason)
    if result.error:
        renderer.error(f"  ! {result.error}")
    renderer.harvest_note(len(result.harvested))

    _record_turn(record, line, result)
    record.history[:] = list(session.history)  # snapshot conversation so a resume continues exactly here.
    _save(record, root)


def _record_turn(record: SessionRecord, line: str, result: object) -> None:
    res = result  # TurnResult; kept loosely typed to avoid an import cycle in the type checker's eyes.
    record.turns.append(
        {
            "user": line,
            "final_text": getattr(res, "final_text", ""),
            "steps": getattr(res, "steps", 0),
            "tool_calls": getattr(res, "tool_calls", 0),
            "stop_reason": getattr(res, "stop_reason", ""),
            "harvested": list(getattr(res, "harvested", [])),
        }
    )


def _save(record: SessionRecord, root: Path | None) -> None:
    if root is None:
        return
    record.updated_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    save_session(root, record)


def _list_sessions(renderer: ConsoleRenderer, root: Path | None) -> None:
    if root is None:
        renderer.info("(sessions are not persisted in this mode)")
        return
    records = list_sessions(root)
    if not records:
        renderer.info("no saved sessions yet")
        return
    renderer.info(f"{len(records)} saved session(s) (newest first):")
    for r in records[:20]:
        renderer.info(f"  {r.id}  ·  {len(r.turns)} turn(s)  ·  {r.updated_at or '?'}  ·  {r.workdir}")


def _farewell(
    session: InteractiveSession,
    renderer: ConsoleRenderer,
    record: SessionRecord,
    root: Path | None,
) -> None:
    # Persist the final history snapshot before quitting (history accumulates on the session in place).
    if root is not None:
        record.history[:] = list(session.history)
        _save(record, root)
    ids = session.harvester.written_ids
    if ids:
        renderer.info(
            f"Harvested {len(ids)} failure bundle(s) this session -> {session.harvester.inbox_dir}"
        )
        renderer.info("Run the continuous loop (self-harness ui, Start continuous loop) to learn from them.")
    if root is not None:
        renderer.info(f"Session saved as {record.id} (resume with: self-harness code --resume {record.id})")
