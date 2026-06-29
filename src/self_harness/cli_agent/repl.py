"""The interactive read-eval-print loop for `self-harness code`.

Drives a :class:`ConsoleRenderer` (rich TUI, or a plain fallback) over an :class:`InteractiveSession`:
streams GLM's reply token-by-token, renders a line per tool call, expands ``@file`` mentions into the
turn, and persists the session after every turn so it can be resumed later. Auto-run and failure
harvesting (the self-improvement flywheel) are unchanged from Phase 1.
"""

from __future__ import annotations

import json
import shlex
import uuid
from datetime import UTC, datetime
from pathlib import Path

from self_harness import user_config
from self_harness.adapters.agentic.runner import DEFAULT_GLM_MODEL
from self_harness.agentic_session import resolve_zai_api_key, resolve_zai_base_url
from self_harness.cli_agent.context import expand_mentions
from self_harness.cli_agent.effort import (
    EFFORT_ALIASES,
    effort_help,
    normalize_effort,
    supported_efforts,
    valid_effort_or_none,
    validate_effort_for_provider,
)
from self_harness.cli_agent.model_discovery import ModelCatalog, discover_provider_models
from self_harness.cli_agent.session import HeadlessCliSession, InteractiveSession, headless_binary_for_backend
from self_harness.cli_agent.sessions import SessionRecord, list_sessions, load_session, save_session
from self_harness.cli_agent.ui import BackRequested, ConsoleRenderer
from self_harness.cli_agent.ux_harvest import UxFailureHarvester
from self_harness.exceptions import AgenticRunnerError

CodeSession = InteractiveSession | HeadlessCliSession
_KNOWN_EFFORTS_TEXT = "none, minimal, low, medium, high, xhigh, max"

_PROVIDER_ALIASES = {
    "codex": "codex",
    "codex-cli": "codex",
    "agy": "agy",
    "agy-cli": "agy",
    "claude": "claude",
    "claude-cli": "claude",
    "claude-code": "claude",
    "glm": "glm",
    "glm-5": "glm",
    "glm-5.2": "glm",
    "zai": "glm",
    "z.ai": "glm",
}
_SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/menu", "open the command palette"),
    ("/commands", "open the command palette"),
    ("/palette", "open the command palette"),
    ("/help", "show command help"),
    ("/model", "switch provider, model, and effort"),
    ("/provider", "switch provider"),
    ("/backend", "switch provider"),
    ("/effort", "switch reasoning effort"),
    ("/threads", "open the thread picker"),
    ("/thread", "thread actions: list, new, switch"),
    ("/sessions", "list saved sessions"),
    ("/whoami", "show active provider and configured model"),
    ("/identity", "show active provider and configured model"),
    ("/status", "show runtime status"),
    ("/config", "edit runtime settings"),
    ("/history", "show recent turns"),
    ("/harness", "show active harness hash"),
    ("/harvested", "list harvested failure bundles"),
    ("/report", "report a semantic/control-plane UX issue"),
    ("/feedback", "alias for /report"),
    ("/rejected", "list rejected semantic UX captures"),
    ("/cwd", "show working directory"),
    ("/clear", "clear the terminal"),
    ("/reset", "clear current thread history"),
    ("/save", "save the current thread"),
    ("/stop", "explain turn interruption"),
    ("/interrupt", "explain turn interruption"),
    ("/exit", "save and exit"),
    ("/quit", "save and exit"),
    ("/q", "save and exit"),
)

_HELP = """\
Commands:
  /menu        open the TUI command palette
  /model       open model/provider picker, or set directly: /model codex gpt-5.6 xhigh
  /provider    switch provider: /provider codex|agy|claude|glm
  /effort      open/set effort for Codex or Claude
  /threads     open thread picker; /thread new; /thread switch <id-or-number>
  /whoami      show active provider, model, effort, and transport
  /status      show cwd, thread, harness, model, and runtime controls
  /config      open runtime settings picker
  /history     show recent turns
  /harness     show the active harness
  /harvested   list harvested failure bundles and admitted UX reports
  /report      report a semantic/control-plane UX issue for secondary judging
  /feedback    alias for /report
  /rejected    list rejected semantic UX captures
  /sessions    list saved sessions you can resume (alias for /threads list)
  /clear       clear the terminal
  /reset       clear the current thread history
  /save        save the current thread now
  /stop        during a running turn, press Ctrl-C; at the prompt this is a no-op
  /exit /quit  leave (or Ctrl-D, Ctrl-C at the prompt)
Mention @path/to/file to inline that file's contents into your message.
Anything else is sent to the configured coding backend, which acts in the working directory."""


def run_repl(
    session: CodeSession,
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

    renderer = ConsoleRenderer(plain=plain, assistant_label=_assistant_label(session), slash_commands=_SLASH_COMMANDS)
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
            agent_label=_agent_label(session),
        )

    while True:
        try:
            line = renderer.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if _is_exit_command(line):
            break
        if line == "/help":
            renderer.info(_HELP)
            continue
        if line in {"/menu", "/commands", "/palette", "/"}:
            session, record, exit_requested = _command_palette(session, record, renderer, root)
            renderer.assistant_label = _assistant_label(session)
            if exit_requested:
                break
            continue
        if line == "/model" or line.startswith("/model "):
            session = _handle_model_command(session, line.removeprefix("/model").strip(), renderer)
            renderer.assistant_label = _assistant_label(session)
            record.history[:] = list(session.history)
            continue
        if line == "/provider" or line.startswith("/provider ") or line == "/backend" or line.startswith("/backend "):
            command = line.split(maxsplit=1)[1] if " " in line else ""
            session = _handle_provider_command(session, command, renderer)
            renderer.assistant_label = _assistant_label(session)
            record.history[:] = list(session.history)
            continue
        if line == "/effort" or line.startswith("/effort "):
            session = _handle_effort_command(session, line.removeprefix("/effort").strip(), renderer)
            renderer.assistant_label = _assistant_label(session)
            record.history[:] = list(session.history)
            continue
        if _is_threads_command(line):
            session, record = _handle_thread_command(session, record, line, renderer, root)
            renderer.assistant_label = _assistant_label(session)
            continue
        if line in {"/whoami", "/identity"} or _is_identity_query(line):
            renderer.info(_identity_text(session))
            continue
        if line == "/status":
            renderer.info(_status_text(session, record, root))
            continue
        if line == "/config":
            session = _config_palette(session, renderer)
            renderer.assistant_label = _assistant_label(session)
            record.history[:] = list(session.history)
            continue
        if line == "/history" or line.startswith("/history "):
            _show_history(record, renderer, line)
            continue
        if line == "/clear":
            renderer.clear()
            continue
        if line in {"/stop", "/interrupt"}:
            renderer.info("No turn is running. During a running turn, press Ctrl-C to interrupt it.")
            continue
        if line == "/save":
            record.history[:] = list(session.history)
            _save(record, root)
            renderer.info(f"saved thread {record.id}")
            continue
        if line == "/cwd":
            renderer.info(str(session.workdir))
            continue
        if line == "/reset":
            session.reset()
            record.turns.clear()
            record.history.clear()
            _save(record, root)
            renderer.info("(history cleared)")
            continue
        if line == "/harness":
            lineage = "evolving lineage" if session.evolving else "initial_harness() (Figure 3)"
            renderer.info(f"harness {session.harness_hash} ({lineage})")
            continue
        if line == "/harvested" or line == "/harvested --all":
            _show_harvested(session, renderer)
            continue
        if line == "/harvested --rejected" or line == "/rejected":
            _show_rejected(session, renderer)
            continue
        if line == "/report" or line.startswith("/report ") or line == "/feedback" or line.startswith("/feedback "):
            command = line.split(maxsplit=1)[1] if " " in line else ""
            _handle_report_command(session, record, command, renderer, root)
            continue
        if line == "/sessions":
            _list_sessions(renderer, root)
            continue
        if line.startswith("/"):
            renderer.info(f"unknown command: {line} (try /help)")
            continue
        natural = _natural_model_command(line)
        if natural is not None:
            session = _handle_model_command(session, natural, renderer)
            renderer.assistant_label = _assistant_label(session)
            record.history[:] = list(session.history)
            continue

        _run_turn(session, line, renderer, record, root)

    _farewell(session, renderer, record, root)
    return 0


_MAX_AUTO_CONTINUE = 4  # how many times a turn may auto-resume after hitting the step budget


def _run_turn(
    session: CodeSession,
    line: str,
    renderer: ConsoleRenderer,
    record: SessionRecord,
    root: Path | None,
) -> None:
    augmented, inlined = expand_mentions(line, session.workdir)
    previous_assistant_text = _last_assistant_text(record)
    if inlined:
        renderer.info("  @ inlined: " + ", ".join(inlined))

    def _on_delta(delta: str) -> None:
        # Approximate token count from streamed characters (~4 chars/token) for the live heartbeat.
        renderer.add_tokens(max(1, len(delta) // 4))
        renderer.push_delta(delta)

    def _on_tool_event(name: str, summary: str, ok: bool) -> None:
        # Each tool event commits the preceding text step, so streamed text and tool lines stay separated.
        renderer.tool_event(name, summary, ok)

    def _on_tool_starting(name: str, summary: str) -> None:
        # The longest silent stretch of a turn — show "running <cmd>…" so it never looks frozen.
        renderer.tool_starting(name, summary)

    def _on_model_request(step: int) -> None:
        renderer.set_phase("thinking")

    # Send the user's message; if the agent runs out of step budget mid-task, auto-continue a bounded
    # number of times instead of stopping silently (the old behavior that left big tasks half-done).
    message = augmented
    result = None
    for attempt in range(_MAX_AUTO_CONTINUE + 1):
        renderer.begin_turn()
        try:
            result = session.send(
                message,
                on_text_delta=_on_delta,
                on_tool_event=_on_tool_event,
                on_tool_starting=_on_tool_starting,
                on_model_request=_on_model_request,
            )
        except KeyboardInterrupt:
            renderer.end_stream(stop_reason="interrupted")
            renderer.info("Interrupted. The turn was not saved; prompt control is back.")
            return
        except Exception as exc:  # noqa: BLE001 - surface any transport/runtime error without crashing.
            renderer.end_stream(stop_reason="error")
            renderer.error(f"  ! error: {exc}")
            return

        renderer.end_stream(fallback_text=result.final_text.strip(), stop_reason=result.stop_reason)
        if result.error:
            renderer.error(f"  ! {result.error}")
            if _is_rate_limit_error(result.error):
                renderer.info(
                    "  Z.ai rate limit detected. Use `self-harness settings set model codex`, "
                    "`self-harness settings set model agy`, or `self-harness settings set model claude` "
                    "to run the main coding agent through a local headless CLI."
                )
        ux_written, ux_rejected = _observe_ux_turn(
            session,
            user_text=line if attempt == 0 else message,
            result=result,
            previous_assistant_text=previous_assistant_text,
        )
        if ux_written:
            result.harvested.extend(ux_written)
            renderer.info(f"  semantic issue candidate admitted: {', '.join(ux_written)}")
        if ux_rejected:
            renderer.info(f"  semantic issue candidate rejected: {len(ux_rejected)}")
        renderer.harvest_note(len(result.harvested))
        _record_turn(record, message if attempt == 0 else "(auto-continue)", result)
        _remember_harvested(record, result.harvested)
        record.history[:] = list(session.history)
        _save(record, root)

        if result.stop_reason != "max_steps":
            break
        if attempt < _MAX_AUTO_CONTINUE:
            renderer.info(
                f"  … hit the {session.max_steps}-step budget; auto-continuing "
                f"({attempt + 1}/{_MAX_AUTO_CONTINUE}). Type a new instruction to redirect."
            )
            message = "Continue where you left off until the task is complete."
        else:
            renderer.info(
                f"  … still working after {_MAX_AUTO_CONTINUE} auto-continues. "
                "Say 'continue' to keep going, or give a narrower next step."
            )


def _handle_report_command(
    session: CodeSession,
    record: SessionRecord,
    raw: str,
    renderer: ConsoleRenderer,
    root: Path | None,
) -> None:
    harvester = _ux_harvester(session)
    if harvester is None or not harvester.enabled:
        renderer.info("semantic UX harvesting is off for this session")
        return
    last_assistant = _last_assistant_text(record)
    if raw.strip():
        trigger = "manual-report"
        observation = raw.strip()
        expected = ""
        observed = last_assistant[:1000]
        criterion = ""
    else:
        try:
            observation = renderer.ask("problem").strip()
            expected = renderer.ask("expected behavior").strip()
            observed = renderer.ask("observed behavior", default=last_assistant[:1000]).strip()
            criterion = renderer.ask("checkable criterion").strip()
        except BackRequested:
            renderer.info("report cancelled")
            return
        trigger = observation[:80] or "manual-report"
    if not observation:
        renderer.error("  ! report needs a user-visible problem")
        return
    harvester.report(
        trigger=trigger,
        observation=observation,
        expected_behavior=expected,
        observed=observed,
        checkable_criterion=criterion,
        operating_provider=_provider(session),
        metadata={"trigger_kind": "manual-report"},
    )
    written, rejected = harvester.flush(id_prefix=f"report-{_now_stamp()}")
    if written:
        _remember_harvested(record, written)
        _save(record, root)
        renderer.info(f"semantic issue candidate admitted: {', '.join(written)}")
    if rejected:
        renderer.info(f"semantic issue candidate rejected: {', '.join(rejected)}")
    if not written and not rejected:
        renderer.info("no semantic issue candidate was written")


def _observe_ux_turn(
    session: CodeSession,
    *,
    user_text: str,
    result: object,
    previous_assistant_text: str,
) -> tuple[list[str], list[str]]:
    harvester = _ux_harvester(session)
    if harvester is None:
        return [], []
    harvester.observe_turn(
        user_text=user_text,
        final_text=str(getattr(result, "final_text", "")),
        stop_reason=str(getattr(result, "stop_reason", "")),
        error=getattr(result, "error", None),
        tool_activity=list(getattr(result, "tool_activity", [])),
        operating_provider=_provider(session),
        model_status=_model_status(session),
        previous_assistant_text=previous_assistant_text,
    )
    return harvester.flush(id_prefix=f"cli-{getattr(session, 'turn_index', 0):03d}")


def _ux_harvester(session: CodeSession) -> UxFailureHarvester | None:
    value = getattr(session, "ux_harvester", None)
    return value if isinstance(value, UxFailureHarvester) else None


def _last_assistant_text(record: SessionRecord) -> str:
    for turn in reversed(record.turns):
        text = turn.get("final_text")
        if isinstance(text, str) and text.strip():
            return text
    return ""


def _remember_harvested(record: SessionRecord, ids: list[str]) -> None:
    for bundle_id in ids:
        if bundle_id not in record.harvested:
            record.harvested.append(bundle_id)


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


def _is_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return "rate limit" in lowered or "[1302]" in lowered


def _agent_label(session: CodeSession) -> str:
    backend = getattr(session, "backend", "")
    if isinstance(backend, str) and backend:
        return f"{backend} headless agent"
    model = getattr(session, "model", "GLM 5.2")
    return f"{model} dev agent"


def _assistant_label(session: CodeSession) -> str:
    backend = getattr(session, "backend", "")
    if isinstance(backend, str) and backend:
        return backend
    model = str(getattr(session, "model", "glm"))
    return "glm" if model.startswith("glm") else model


def _is_exit_command(line: str) -> bool:
    return line.strip().lower() in {"/exit", "/quit", "/q", ":q", "exit", "quit"}


def _is_threads_command(line: str) -> bool:
    lowered = line.strip().lower()
    return (
        lowered == "/threads"
        or lowered.startswith("/threads ")
        or lowered == "/thread"
        or lowered.startswith("/thread ")
    )


def _is_identity_query(line: str) -> bool:
    normalized = line.strip().lower().rstrip(" ?!.")
    return normalized in {
        "whoami",
        "who are you",
        "what model are you",
        "what model are you using",
        "which model are you",
        "which model are you using",
        "what model am i using",
        "which model am i using",
        "what model are we using",
        "which model are we using",
        "what provider are you using",
        "which provider are you using",
        "what backend are you using",
        "which backend are you using",
    }


def _command_palette(
    session: CodeSession,
    record: SessionRecord,
    renderer: ConsoleRenderer,
    root: Path | None,
) -> tuple[CodeSession, SessionRecord, bool]:
    renderer.menu(
        "SelfHarness Command Palette",
        [
            ("1", "Model/provider/effort"),
            ("2", "Threads"),
            ("3", "Identity"),
            ("4", "Status"),
            ("5", "Runtime config"),
            ("6", "History"),
            ("7", "Harness"),
            ("8", "Harvested failures"),
            ("9", "Report UX issue"),
            ("10", "Rejected UX captures"),
            ("11", "Save current thread"),
            ("12", "Clear screen"),
            ("13", "Reset current thread"),
            ("14", "Help"),
            ("0", "Exit SelfHarness Code"),
        ],
        footer="Type a number, or press Enter to cancel.",
    )
    try:
        choice = renderer.ask("command").strip().lower()
    except BackRequested:
        return session, record, False
    if not choice:
        return session, record, False
    if choice in {"1", "model", "m"}:
        session = _model_palette(session, renderer)
    elif choice in {"2", "threads", "thread", "t"}:
        session, record = _thread_palette(session, record, renderer, root)
    elif choice in {"3", "identity", "whoami", "i"}:
        renderer.info(_identity_text(session))
    elif choice in {"4", "status", "s"}:
        renderer.info(_status_text(session, record, root))
    elif choice in {"5", "config", "settings", "c"}:
        session = _config_palette(session, renderer)
    elif choice in {"6", "history"}:
        _show_history(record, renderer, "/history")
    elif choice in {"7", "harness"}:
        lineage = "evolving lineage" if session.evolving else "initial_harness() (Figure 3)"
        renderer.info(f"harness {session.harness_hash} ({lineage})")
    elif choice in {"8", "harvested"}:
        _show_harvested(session, renderer)
    elif choice in {"9", "report", "feedback"}:
        _handle_report_command(session, record, "", renderer, root)
    elif choice in {"10", "rejected"}:
        _show_rejected(session, renderer)
    elif choice in {"11", "save"}:
        record.history[:] = list(session.history)
        _save(record, root)
        renderer.info(f"saved thread {record.id}")
    elif choice in {"12", "clear"}:
        renderer.clear()
    elif choice in {"13", "reset"}:
        if _confirm(renderer, "Clear current thread history?"):
            session.reset()
            record.history.clear()
            record.turns.clear()
            _save(record, root)
            renderer.info("(history cleared)")
    elif choice in {"14", "help", "?"}:
        renderer.info(_HELP)
    elif choice in {"0", "exit", "quit", "q"}:
        return session, record, True
    else:
        renderer.info(f"unknown palette choice: {choice}")
    return session, record, False


def _handle_model_command(session: CodeSession, raw: str, renderer: ConsoleRenderer) -> CodeSession:
    if not raw:
        return _model_palette(session, renderer)
    try:
        provider, model, effort = _parse_model_selection(raw, default_provider=_provider(session))
    except ValueError as exc:
        renderer.error(f"  ! {exc}")
        return session
    selected = _switch_code_backend(session, provider=provider, model=model, effort=effort, renderer=renderer)
    _persist_code_selection(selected)
    renderer.info(_model_status(selected))
    return selected


def _handle_provider_command(session: CodeSession, raw: str, renderer: ConsoleRenderer) -> CodeSession:
    if not raw:
        return _model_palette(session, renderer)
    try:
        provider, model, effort = _parse_model_selection(raw, default_provider=_provider(session))
    except ValueError as exc:
        renderer.error(f"  ! {exc}")
        return session
    selected = _switch_code_backend(session, provider=provider, model=model, effort=effort, renderer=renderer)
    _persist_code_selection(selected)
    renderer.info(_model_status(selected))
    return selected


def _handle_effort_command(session: CodeSession, raw: str, renderer: ConsoleRenderer) -> CodeSession:
    provider = _provider(session)
    if not raw:
        try:
            picked = _effort_picker(session, renderer, provider=provider)
        except BackRequested:
            return session
        if picked is None:
            renderer.info(_model_status(session))
            return session
        raw = picked
    effort = _normalize_effort(raw)
    if effort is None:
        if supported_efforts(provider):
            renderer.error(f"  ! effort must be one of: {effort_help(provider)}")
        else:
            renderer.error(f"  ! {provider} does not support reasoning effort")
        return session
    try:
        effort = validate_effort_for_provider(provider, effort)
    except ValueError as exc:
        renderer.error(f"  ! {exc}")
        return session
    selected: CodeSession
    if isinstance(session, HeadlessCliSession):
        session.effort = effort
        selected = session
    else:
        renderer.error(f"  ! {provider} does not support reasoning effort; switch to Codex or Claude first.")
        selected = session
        renderer.info(_model_status(selected))
        return selected
    _persist_code_selection(selected, effort=effort)
    renderer.info(_model_status(selected))
    return selected


def _model_palette(session: CodeSession, renderer: ConsoleRenderer) -> CodeSession:
    while True:
        current_provider = _provider(session)
        renderer.menu(
            "Model / Provider",
            [
                ("1", f"Codex headless CLI{' (current)' if current_provider == 'codex' else ''}"),
                ("2", f"Agy headless CLI{' (current)' if current_provider == 'agy' else ''}"),
                ("3", f"Claude headless CLI{' (current)' if current_provider == 'claude' else ''}"),
                ("4", f"GLM via Z.ai{' (current)' if current_provider == 'glm' else ''}"),
                ("0", "Cancel"),
            ],
            footer=_model_status(session),
        )
        try:
            choice = renderer.ask("provider").strip().lower()
        except BackRequested:
            return session
        if not choice or choice == "0":
            return session
        mapping = {"1": "codex", "2": "agy", "3": "claude", "4": "glm"}
        if choice in mapping:
            provider = mapping[choice]
        else:
            try:
                provider = _normalize_provider(choice)
            except ValueError as exc:
                renderer.error(f"  ! {exc}")
                continue
        selected = _model_palette_for_provider(session, provider, renderer)
        if selected is not None:
            return selected


def _model_palette_for_provider(
    session: CodeSession,
    provider: str,
    renderer: ConsoleRenderer,
) -> CodeSession | None:
    while True:
        try:
            selected_model, model_cancelled = _model_picker(provider, session, renderer)
        except BackRequested:
            return None
        if model_cancelled:
            return session
        model = selected_model
        current_effort = valid_effort_or_none(provider, getattr(session, "effort", None))
        effort = current_effort
        if provider in {"codex", "claude"}:
            try:
                selected_effort = _effort_picker(session, renderer, current=current_effort, provider=provider)
            except BackRequested:
                continue
            effort = selected_effort if selected_effort is not None else current_effort
        elif provider == "agy":
            renderer.info("Agy exposes model selection; no effort flag is advertised by this install.")
            effort = None
        selected = _switch_code_backend(session, provider=provider, model=model, effort=effort, renderer=renderer)
        _persist_code_selection(selected)
        renderer.info(_model_status(selected))
        return selected


def _model_picker(provider: str, session: CodeSession, renderer: ConsoleRenderer) -> tuple[str | None, bool]:
    current_model = getattr(session, "model", None) if _provider(session) == provider else None
    binary = headless_binary_for_backend(provider) if provider in {"codex", "agy", "claude"} else None
    catalog = discover_provider_models(provider, binary=binary)
    while True:
        options, model_by_choice = _model_options(catalog, current_model=current_model)
        renderer.menu(
            f"{provider.upper()} Models",
            options,
            footer=_model_picker_footer(catalog, current_model=current_model),
        )
        choice = renderer.ask("model").strip()
        if not choice:
            return None, False
        lowered = choice.lower()
        if lowered == "0":
            return None, True
        if lowered in {"d", "default"}:
            return None, False
        if lowered in {"c", "custom"}:
            try:
                value = renderer.ask("custom model id").strip()
            except BackRequested:
                continue
            return (value or None), False
        if choice in model_by_choice:
            return model_by_choice[choice], False
        renderer.error(f"  ! unknown model choice: {choice}")


def _model_options(
    catalog: ModelCatalog,
    *,
    current_model: object,
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    model_by_choice: dict[str, str] = {}
    options: list[tuple[str, str]] = []
    current = current_model if isinstance(current_model, str) and current_model else ""
    models = list(catalog.models)
    if current and current not in models and not catalog.models:
        models.insert(0, current)
    for index, model in enumerate(models, start=1):
        model_by_choice[str(index)] = model
        options.append((str(index), f"{model}{' (current)' if model == current else ''}"))
    options.extend(
        [
            ("d", "Provider default / clear model override"),
            ("c", "Custom model id"),
            ("0", "Cancel"),
        ]
    )
    return options, model_by_choice


def _model_picker_footer(catalog: ModelCatalog, *, current_model: object) -> str:
    current = current_model if isinstance(current_model, str) and current_model else "provider default"
    if catalog.models:
        if current != "provider default" and current not in catalog.models:
            return f"source: {catalog.source}; ignored incompatible current override: {current}"
        return f"source: {catalog.source}; current: {current}"
    return f"could not query live model catalog ({catalog.error or 'unknown error'}); current: {current}"


def _effort_picker(
    session: CodeSession,
    renderer: ConsoleRenderer,
    *,
    current: str | None = None,
    provider: str | None = None,
) -> str | None:
    selected_provider = provider or _provider(session)
    supported = supported_efforts(selected_provider)
    if not supported:
        renderer.error(f"  ! {selected_provider} does not support reasoning effort")
        return None
    current_effort = valid_effort_or_none(selected_provider, current or getattr(session, "effort", None))
    options = [(str(index), _effort_label(value)) for index, value in enumerate(supported, start=1)]
    options.append(("0", "provider default / unchanged"))
    mapping: dict[str, str | None] = {str(index): value for index, value in enumerate(supported, start=1)}
    mapping.update({"0": None, "": None})
    renderer.menu(
        f"{selected_provider.upper()} Reasoning Effort",
        options,
        footer=f"current: {current_effort or 'provider default'}",
    )
    choice = renderer.ask("effort").strip().lower()
    if choice in mapping:
        return mapping[choice]
    effort = _normalize_effort(choice)
    if effort is None:
        renderer.error(f"  ! effort must be one of: {effort_help(selected_provider)}")
        return None
    try:
        return validate_effort_for_provider(selected_provider, effort)
    except ValueError as exc:
        renderer.error(f"  ! {exc}")
        return None


def _parse_model_selection(raw: str, *, default_provider: str) -> tuple[str, str | None, str | None]:
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"could not parse model command: {exc}") from exc
    if not tokens:
        return default_provider, None, None

    provider = default_provider
    effort: str | None = None
    rest: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        lowered = token.lower()
        if lowered in {"--provider", "--backend"}:
            idx += 1
            if idx >= len(tokens):
                raise ValueError(f"{token} needs a provider")
            provider = _normalize_provider(tokens[idx])
        elif lowered == "--effort":
            idx += 1
            if idx >= len(tokens):
                raise ValueError("--effort needs a level")
            effort = _normalize_effort(tokens[idx])
            if effort is None:
                raise ValueError(f"effort must be one of: {_KNOWN_EFFORTS_TEXT}")
        else:
            rest.append(token)
        idx += 1

    if rest and _is_provider(rest[0]):
        provider = _normalize_provider(rest.pop(0))

    effort_from_tail = _pop_effort(rest)
    if effort_from_tail is not None:
        effort = effort_from_tail

    model = _normalize_model_name(rest)
    effort = validate_effort_for_provider(provider, effort)
    return provider, model, effort


def _switch_code_backend(
    session: CodeSession,
    *,
    provider: str,
    model: str | None,
    effort: str | None,
    renderer: ConsoleRenderer,
) -> CodeSession:
    try:
        effort = validate_effort_for_provider(provider, effort)
    except ValueError as exc:
        renderer.error(f"  ! {exc}")
        return session
    if provider == "glm":
        try:
            api_key = resolve_zai_api_key()
        except AgenticRunnerError as exc:
            renderer.error(f"  ! {exc}")
            return session
        return InteractiveSession(
            api_key=api_key,
            base_url=resolve_zai_base_url(),
            workdir=session.workdir,
            harness=session.harness,
            harvester=session.harvester,
            ux_harvester=_ux_harvester(session),
            model=model or DEFAULT_GLM_MODEL,
            max_steps=session.max_steps,
            tool_timeout_seconds=session.tool_timeout_seconds,
            evolving=session.evolving,
            history=list(session.history),
            turn_index=session.turn_index,
        )

    if isinstance(session, HeadlessCliSession) and session.backend == provider:
        if model is not None:
            session.model = model
        if effort is not None:
            session.effort = effort
        return session

    return HeadlessCliSession(
        backend=provider,
        binary=headless_binary_for_backend(provider),
        workdir=session.workdir,
        harness=session.harness,
        harvester=session.harvester,
        ux_harvester=_ux_harvester(session),
        model=model,
        effort=effort,
        max_steps=session.max_steps,
        tool_timeout_seconds=session.tool_timeout_seconds,
        evolving=session.evolving,
        history=list(session.history),
        turn_index=session.turn_index,
    )


def _persist_code_selection(
    session: CodeSession,
    *,
    effort: str | None = None,
) -> None:
    cfg = user_config.load_config()
    provider = _provider(session)
    cfg.set("code_provider", provider)
    model = getattr(session, "model", None)
    if isinstance(model, str) and model:
        cfg.set("code_model", model)
    else:
        cfg.unset("code_model")
    selected_effort = effort or getattr(session, "effort", None)
    if isinstance(selected_effort, str) and selected_effort:
        cfg.set("code_effort", selected_effort)
    elif provider in {"codex", "claude"}:
        cfg.unset("code_effort")
    # Keep the legacy startup path readable for older installs and `settings get model`.
    cfg.set("model", provider if provider != "glm" else (model or DEFAULT_GLM_MODEL))
    cfg.save()


def _model_status(session: CodeSession) -> str:
    provider = _provider(session)
    model = getattr(session, "model", None) or ("provider default" if provider != "glm" else DEFAULT_GLM_MODEL)
    raw_effort = getattr(session, "effort", None)
    valid_effort = valid_effort_or_none(provider, raw_effort)
    if raw_effort and valid_effort is None:
        effort = f"provider default (ignored invalid: {raw_effort})"
    else:
        effort = valid_effort or "provider default"
    binary = getattr(session, "binary", None)
    binary_text = f", binary: {binary}" if isinstance(binary, str) and binary else ""
    effort_note = " (not used by agy)" if provider == "agy" and effort != "provider default" else ""
    return f"provider: {provider}, model: {model}, effort: {effort}{effort_note}{binary_text}"


def _identity_text(session: CodeSession) -> str:
    provider = _provider(session)
    if provider == "glm":
        transport = "Z.ai Anthropic-compatible Messages API"
    else:
        binary = getattr(session, "binary", None) or provider
        transport = f"{provider} headless CLI ({binary})"
    return f"SelfHarness Code is using {_model_status(session)}\ntransport: {transport}"


def _provider(session: CodeSession) -> str:
    backend = getattr(session, "backend", "")
    if isinstance(backend, str) and backend:
        return _normalize_provider(backend)
    return "glm"


def _normalize_provider(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in _PROVIDER_ALIASES:
        raise ValueError(f"provider must be one of: glm, codex, agy, claude (got {value!r})")
    return _PROVIDER_ALIASES[normalized]


def _is_provider(value: str) -> bool:
    return value.strip().lower().replace("_", "-") in _PROVIDER_ALIASES


def _normalize_effort(value: str) -> str | None:
    return normalize_effort(value)


def _effort_label(value: str) -> str:
    if value == "xhigh":
        return "xhigh / extra high"
    return value


def _pop_effort(tokens: list[str]) -> str | None:
    if len(tokens) >= 2:
        pair = f"{tokens[-2]} {tokens[-1]}".lower().replace("_", "-")
        effort = EFFORT_ALIASES.get(pair)
        if effort is not None:
            del tokens[-2:]
            return effort
    if tokens:
        effort = _normalize_effort(tokens[-1])
        if effort is not None:
            tokens.pop()
            return effort
    return None


def _normalize_model_name(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    if len(tokens) == 1:
        return tokens[0]
    head = tokens[0].lower()
    joined = " ".join(tokens)
    if head in {"gpt", "glm", "claude", "o", "o3", "o4"}:
        return joined.replace(" ", "-")
    return joined


def _natural_model_command(line: str) -> str | None:
    lowered = line.strip().lower()
    prefixes = ("change to ", "switch to ", "use model ", "use provider ")
    if not lowered.startswith(prefixes):
        return None
    remainder = line.strip().split(" ", 2)[2] if lowered.startswith("use ") else line.strip().split(" ", 2)[2]
    clue_text = remainder.lower()
    clues = ("gpt", "claude", "sonnet", "opus", "codex", "agy", "glm", "o3", "o4", "xhigh", "extra high")
    return remainder if any(clue in clue_text for clue in clues) else None


def _handle_thread_command(
    session: CodeSession,
    record: SessionRecord,
    line: str,
    renderer: ConsoleRenderer,
    root: Path | None,
) -> tuple[CodeSession, SessionRecord]:
    raw = line.split(maxsplit=1)[1] if " " in line else ""
    if root is None:
        renderer.info("(threads are not persisted in this mode)")
        return session, record
    if not raw:
        return _thread_palette(session, record, renderer, root)
    parts = raw.split()
    action = parts[0].lower()
    arg = " ".join(parts[1:]).strip()
    if action in {"list", "ls"}:
        _list_sessions(renderer, root)
        return session, record
    if action in {"new", "create"}:
        _save_current_thread(session, record, root)
        new_record = _new_thread_record(session)
        session.reset()
        _save(new_record, root)
        renderer.info(f"new thread {new_record.id}")
        return session, new_record
    if action in {"switch", "open", "resume", "use"}:
        if not arg:
            renderer.error("  ! usage: /thread switch <id-or-number>")
            return session, record
        return _switch_thread(session, record, renderer, root, arg)
    renderer.info("usage: /threads, /thread new, /thread switch <id-or-number>, /thread list")
    return session, record


def _thread_palette(
    session: CodeSession,
    record: SessionRecord,
    renderer: ConsoleRenderer,
    root: Path | None,
) -> tuple[CodeSession, SessionRecord]:
    if root is None:
        renderer.info("(threads are not persisted in this mode)")
        return session, record
    records = list_sessions(root)
    options = [("n", "New thread")]
    for idx, item in enumerate(records[:20], start=1):
        marker = "current, " if item.id == record.id else ""
        options.append((str(idx), f"{item.id}  ·  {marker}{len(item.turns)} turn(s)  ·  {item.updated_at or '?'}"))
    options.append(("0", "Cancel"))
    renderer.menu("Threads", options, footer="Pick a thread number, n for new, or Enter to cancel.")
    try:
        choice = renderer.ask("thread").strip()
    except BackRequested:
        return session, record
    if not choice or choice == "0":
        return session, record
    if choice.lower() in {"n", "new"}:
        _save_current_thread(session, record, root)
        new_record = _new_thread_record(session)
        session.reset()
        _save(new_record, root)
        renderer.info(f"new thread {new_record.id}")
        return session, new_record
    return _switch_thread(session, record, renderer, root, choice)


def _switch_thread(
    session: CodeSession,
    record: SessionRecord,
    renderer: ConsoleRenderer,
    root: Path,
    selector: str,
) -> tuple[CodeSession, SessionRecord]:
    _save_current_thread(session, record, root)
    target = _resolve_thread(root, selector)
    if target is None:
        renderer.error(f"  ! no thread matches {selector!r}")
        return session, record
    session.history[:] = list(target.history)
    session.turn_index = len(target.turns)
    session.harvester.seed_written(_command_bundle_ids(target.harvested))
    ux = _ux_harvester(session)
    if ux is not None:
        ux.seed_written(_ux_bundle_ids(target.harvested))
    renderer.info(f"switched to thread {target.id} ({len(target.turns)} turn(s))")
    return session, target


def _resolve_thread(root: Path, selector: str) -> SessionRecord | None:
    if selector.isdigit():
        index = int(selector) - 1
        records = list_sessions(root)
        return records[index] if 0 <= index < len(records) else None
    return load_session(root, selector)


def _new_thread_record(session: CodeSession) -> SessionRecord:
    now = _now_stamp()
    return SessionRecord(
        id=f"code-{now}-{uuid.uuid4().hex[:8]}",
        workdir=str(session.workdir),
        harness_hash=session.harness_hash,
        created_at=now,
        updated_at=now,
    )


def _save_current_thread(session: CodeSession, record: SessionRecord, root: Path | None) -> None:
    record.history[:] = list(session.history)
    _save(record, root)


def _config_palette(session: CodeSession, renderer: ConsoleRenderer) -> CodeSession:
    while True:
        cfg = user_config.load_config()
        renderer.menu(
            "Runtime Config",
            [
                ("1", f"Max steps per turn: {session.max_steps}"),
                ("2", f"Tool timeout seconds: {session.tool_timeout_seconds}"),
                ("3", f"Harvest failures: {'on' if session.harvester.enabled else 'off'}"),
                ("4", "Model/provider/effort"),
                ("5", f"Config path: {cfg.path}"),
                ("0", "Cancel"),
            ],
            footer="Changes apply immediately to this session and are saved as defaults.",
        )
        try:
            choice = renderer.ask("config").strip().lower()
        except BackRequested:
            return session
        if choice in {"", "0"}:
            return session
        if choice == "1":
            try:
                value = renderer.ask("max steps", default=str(session.max_steps)).strip()
            except BackRequested:
                continue
            try:
                session.max_steps = max(1, int(value))
            except ValueError:
                renderer.error("  ! max steps must be an integer")
                continue
            cfg.set("max_steps", session.max_steps)
        elif choice == "2":
            try:
                value = renderer.ask("tool timeout seconds", default=str(session.tool_timeout_seconds)).strip()
            except BackRequested:
                continue
            try:
                session.tool_timeout_seconds = max(1, int(value))
            except ValueError:
                renderer.error("  ! tool timeout must be an integer")
                continue
            cfg.set("tool_timeout_seconds", session.tool_timeout_seconds)
        elif choice == "3":
            session.harvester.enabled = not session.harvester.enabled
            ux = _ux_harvester(session)
            if ux is not None:
                ux.enabled = session.harvester.enabled
            cfg.set("harvest", session.harvester.enabled)
            renderer.info(f"harvest {'on' if session.harvester.enabled else 'off'}")
        elif choice == "4":
            before_model_status = _model_status(session)
            session = _model_palette(session, renderer)
            cfg = user_config.load_config()
            if _model_status(session) == before_model_status:
                continue
        elif choice == "5":
            renderer.info(str(cfg.path))
            continue
        else:
            renderer.info(f"unknown config choice: {choice}")
            continue
        cfg.save()
        renderer.info(_status_text(session, None, None))
        return session


def _status_text(session: CodeSession, record: SessionRecord | None, root: Path | None) -> str:
    thread = record.id if record is not None else "(unsaved)"
    root_text = str(root) if root is not None else "(not persisting)"
    lineage = "evolving lineage" if session.evolving else "initial_harness()"
    ux = _ux_harvester(session)
    ux_text = "off"
    if ux is not None:
        ux_text = (
            f"{'on' if ux.enabled else 'off'} -> {ux.inbox_dir} "
            f"(admitted {len(ux.written_ids)}, rejected {len(ux.rejected_ids)})"
        )
    return (
        f"{_model_status(session)}\n"
        f"thread: {thread}\n"
        f"cwd: {session.workdir}\n"
        f"session store: {root_text}\n"
        f"harness: {session.harness_hash[:16]} ({lineage})\n"
        f"harvest: {'on' if session.harvester.enabled else 'off'} -> {session.harvester.inbox_dir}\n"
        f"semantic harvest: {ux_text}\n"
        f"max steps: {session.max_steps}, tool timeout: {session.tool_timeout_seconds}s"
    )


def _show_history(record: SessionRecord, renderer: ConsoleRenderer, line: str) -> None:
    parts = line.split()
    limit = 10
    if len(parts) > 1:
        try:
            limit = max(1, int(parts[1]))
        except ValueError:
            renderer.error("  ! usage: /history [count]")
            return
    if not record.turns:
        renderer.info("no turns in this thread yet")
        return
    rows = record.turns[-limit:]
    for idx, turn in enumerate(rows, start=max(1, len(record.turns) - len(rows) + 1)):
        user = str(turn.get("user", "")).replace("\n", " ")[:100]
        stop = str(turn.get("stop_reason", ""))
        renderer.info(f"{idx}. {user}  [{stop}]")


def _show_harvested(session: CodeSession, renderer: ConsoleRenderer) -> None:
    command_ids = session.harvester.written_ids
    ux = _ux_harvester(session)
    ux_ids = ux.written_ids if ux is not None else []
    ids = _dedupe_ids([*command_ids, *ux_ids])
    renderer.info(
        f"harvested {len(ids)} bundle(s) this session"
        + (": " + ", ".join(ids) if ids else "")
        + f"\ncommand: {len(command_ids)}, ux: {len(ux_ids)}"
    )


def _show_rejected(session: CodeSession, renderer: ConsoleRenderer) -> None:
    harvester = _ux_harvester(session)
    if harvester is None:
        renderer.info("no semantic UX harvester is attached to this session")
        return
    rejected = harvester.rejected_ids
    if not rejected:
        renderer.info("no rejected semantic UX captures this session")
        return
    entries = _rejected_entries(harvester)
    if entries:
        lines = [f"rejected {len(rejected)} semantic UX capture(s):"]
        lines.extend(f"  {bundle_id}: {reason}" for bundle_id, reason in entries)
        renderer.info("\n".join(lines))
    else:
        renderer.info(f"rejected {len(rejected)} semantic UX capture(s): " + ", ".join(rejected))


def _rejected_entries(harvester: UxFailureHarvester) -> list[tuple[str, str]]:
    processed = harvester.inbox_dir / "processed"
    entries: list[tuple[str, str]] = []
    for bundle_id in harvester.rejected_ids:
        path = processed / f"{bundle_id}.json.rejected"
        reason = "rejected"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            entries.append((bundle_id, reason))
            continue
        if isinstance(payload, dict):
            candidate = payload.get("admission_reason")
            if isinstance(candidate, str) and candidate.strip():
                reason = candidate.strip()
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                judge = metadata.get("admitting_judge") or payload.get("admitting_judge")
                if isinstance(judge, str) and judge.strip():
                    reason = f"{reason} (judge: {judge.strip()})"
        entries.append((bundle_id, reason))
    return entries


def _dedupe_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    for bundle_id in ids:
        if bundle_id not in out:
            out.append(bundle_id)
    return out


def _ux_bundle_ids(ids: list[str]) -> list[str]:
    return [bundle_id for bundle_id in ids if "-ux-" in bundle_id]


def _command_bundle_ids(ids: list[str]) -> list[str]:
    return [bundle_id for bundle_id in ids if "-ux-" not in bundle_id]


def _confirm(renderer: ConsoleRenderer, question: str) -> bool:
    try:
        return renderer.ask(f"{question} Type yes to confirm").strip().lower() in {"y", "yes"}
    except BackRequested:
        return False


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


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
    session: CodeSession,
    renderer: ConsoleRenderer,
    record: SessionRecord,
    root: Path | None,
) -> None:
    # Persist the final history snapshot before quitting (history accumulates on the session in place).
    if root is not None:
        record.history[:] = list(session.history)
        _save(record, root)
    ux = _ux_harvester(session)
    ids = _dedupe_ids([*session.harvester.written_ids, *(ux.written_ids if ux is not None else [])])
    if ids:
        renderer.info(
            f"Harvested {len(ids)} bundle(s) this session -> {session.harvester.inbox_dir}"
        )
        renderer.info("Run the continuous loop (self-harness ui, Start continuous loop) to learn from them.")
    if ux is not None and ux.rejected_ids:
        renderer.info(f"Rejected {len(ux.rejected_ids)} semantic UX capture(s); inspect with /rejected next session.")
    if root is not None:
        renderer.info(f"Session saved as {record.id} (resume with: self-harness code --resume {record.id})")
