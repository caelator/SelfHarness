"""Rich terminal UI for the interactive coding agent: streamed markdown, tool lines, spinner.

This is the only module in ``cli_agent`` that imports ``rich``. It shares its color palette with
``console_style`` so the coding chat and the rest of the CLI agree on who-said-what (you = green,
assistant = magenta, tools = yellow, dim scaffolding, green/red for ok/error).

Streaming design (correctness over flashiness): rendering a growing Markdown buffer in a live region is
fundamentally unsafe — once the reply is taller than the terminal, the lines that scroll off cannot be
erased, so a ``transient`` region re-emits them and the text duplicates (the bug this fixes). Instead the
only live element is a **fixed one-line** "receiving…" indicator (always safely erasable), and when a step
ends — a tool call begins or the turn finishes — the accumulated text is committed **once** as permanent,
syntax-highlighted Markdown and the buffer resets. This fixes the duplicate-output bug, gives full
Markdown (tables, fenced code) on the final text, and separates each step cleanly. The plain-text fallback
(``--plain`` / non-tty) streams tokens directly, which is append-only and so also cannot duplicate.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from typing import Any

from self_harness.console_style import STYLES


def _now() -> float:
    return time.monotonic()


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"

# Pull the shared palette so the chat matches the menu/settings/help/loop coloring exactly.
_ASSISTANT = STYLES["glm"]
_USER = STYLES["user"]
_TOOL = STYLES["tool"]
_DIM = STYLES["system"]
_OK = STYLES["success"]
_ERR = STYLES["error"]
_HEAD = STYLES["heading"]

SlashCommand = tuple[str, str]


class BackRequested(Exception):
    """Raised when the operator presses Esc in a nested CLI control prompt."""


def slash_command_matches(text_before_cursor: str, commands: Sequence[SlashCommand]) -> list[SlashCommand]:
    """Return slash commands matching the current prompt prefix.

    ``/`` returns every command, ``/m`` filters by prefix, and text containing a space is treated as an
    in-progress command argument rather than another command lookup.
    """

    prefix = _slash_command_prefix(text_before_cursor)
    if prefix is None:
        return []
    return [(command, description) for command, description in commands if command.startswith(prefix)]


def _slash_command_prefix(text_before_cursor: str) -> str | None:
    if not text_before_cursor.startswith("/"):
        return None
    if any(char.isspace() for char in text_before_cursor):
        return None
    return text_before_cursor


def _build_prompt_session(commands: Sequence[SlashCommand]) -> Any | None:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.document import Document
        from prompt_toolkit.filters import has_completions
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.shortcuts import CompleteStyle
    except Exception:  # noqa: BLE001 - optional terminal enhancement; stdlib input remains valid.
        return None

    class SlashCommandCompleter(Completer):
        def get_completions(self, document: Document, complete_event: Any) -> Any:
            del complete_event
            prefix = _slash_command_prefix(document.text_before_cursor)
            if prefix is None:
                return
            for command, description in slash_command_matches(document.text_before_cursor, commands):
                yield Completion(command, start_position=-len(prefix), display=command, display_meta=description)

    bindings = KeyBindings()

    @bindings.add("/")
    def _slash(event: Any) -> None:
        buffer = event.current_buffer
        buffer.insert_text("/")
        if buffer.document.text_before_cursor == "/":
            buffer.start_completion(select_first=True)

    @bindings.add("down", filter=has_completions)
    def _down(event: Any) -> None:
        event.current_buffer.complete_next()

    @bindings.add("up", filter=has_completions)
    def _up(event: Any) -> None:
        event.current_buffer.complete_previous()

    @bindings.add("enter", filter=has_completions)
    def _enter(event: Any) -> None:
        buffer = event.current_buffer
        complete_state = buffer.complete_state
        if complete_state is not None and complete_state.current_completion is not None:
            buffer.apply_completion(complete_state.current_completion)
        buffer.validate_and_handle()

    return PromptSession(
        completer=SlashCommandCompleter(),
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=True,
        key_bindings=bindings,
        reserve_space_for_menu=8,
    )


def _build_control_prompt_session() -> Any | None:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
    except Exception:  # noqa: BLE001 - optional terminal enhancement; stdlib input remains valid.
        return None

    bindings = KeyBindings()

    @bindings.add("escape")
    def _escape(event: Any) -> None:
        event.app.exit(exception=BackRequested())

    return PromptSession(key_bindings=bindings)


class ConsoleRenderer:
    """Streamed rich UI with a plain fallback. One renderer per session."""

    def __init__(
        self,
        *,
        plain: bool = False,
        assistant_label: str = "agent",
        slash_commands: Sequence[SlashCommand] = (),
    ) -> None:
        # Fall back to plain output when asked, or when stdout is not a TTY (pipes, tests, CI).
        self.plain = plain or not sys.stdout.isatty()
        self.assistant_label = assistant_label
        self._slash_commands = tuple(slash_commands)
        self._console: Any = None
        self._prompt_session: Any = None
        self._control_prompt_session: Any = None
        self._live: Any = None
        self._heartbeat: Any = None  # _Heartbeat renderable driving the "Working…" line
        self._buffer = ""
        self._plain_emitted = 0  # chars of the current buffer already streamed to screen (plain mode)
        self._label_shown = False
        self._plain_last_tick = 0.0  # throttle for plain-mode periodic activity lines
        if not self.plain:
            try:
                from rich.console import Console

                self._console = Console()
            except Exception:  # noqa: BLE001 - any rich import/init issue degrades to plain, never crashes.
                self.plain = True
        if not self.plain and self._slash_commands:
            self._prompt_session = _build_prompt_session(self._slash_commands)
        if not self.plain:
            self._control_prompt_session = _build_control_prompt_session()

    # -- session chrome ---------------------------------------------------------------------------

    def banner(
        self, *, workdir: str, harness_hash: str, lineage: str, harvest: str, agent_label: str
    ) -> None:
        if self.plain or self._console is None:
            print(f"SelfHarness Code — {agent_label}")
            print(f"  cwd: {workdir}")
            print(f"  harness: {harness_hash[:16]} ({lineage})")
            print(f"  harvest: {harvest}")
            print("Type /help for commands, /model to switch backend/model, /exit to quit.\n")
            return
        from rich.panel import Panel
        from rich.text import Text

        body = Text()
        body.append(f"{agent_label} — self-improving harness\n", style=_HEAD)
        body.append(f"cwd      {workdir}\n", style=_DIM)
        body.append(f"harness  {harness_hash[:16]} ({lineage})\n", style=_DIM)
        body.append(f"harvest  {harvest}\n", style=_DIM)
        body.append("/help commands · /model switch · /exit quit", style="dim italic")
        self._console.print(Panel(body, title="SelfHarness Code", border_style=_HEAD))

    def info(self, text: str) -> None:
        if self.plain or self._console is None:
            print(text)
        else:
            self._console.print(text, style=_DIM, highlight=False)

    def error(self, text: str) -> None:
        if self.plain or self._console is None:
            print(text, file=sys.stderr)
        else:
            self._console.print(text, style=_ERR, highlight=False)

    def clear(self) -> None:
        if self.plain or self._console is None:
            print("\033c", end="")
        else:
            self._console.clear()

    def prompt(self) -> str:
        # Plain mode keeps stdlib input for piped-stdin tests. Interactive TTY mode uses prompt_toolkit
        # so typing "/" opens a command menu that Up/Down can navigate.
        if self.plain or self._console is None:
            return input("you › ")
        if self._prompt_session is not None:
            try:
                return str(self._prompt_session.prompt("you › "))
            except (EOFError, KeyboardInterrupt):
                raise
            except Exception:  # noqa: BLE001 - if prompt_toolkit has a terminal issue, fall back to input.
                self._prompt_session = None
        self._console.print("you › ", style=_USER, end="")
        return input()

    def ask(self, label: str, *, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        prompt = f"{label}{suffix} › "
        if self.plain or self._console is None:
            value = input(prompt)
            if value == "\x1b":
                raise BackRequested
        else:
            if self._control_prompt_session is not None:
                try:
                    value = str(self._control_prompt_session.prompt(prompt))
                except BackRequested:
                    raise
                except (EOFError, KeyboardInterrupt):
                    raise
                except Exception:  # noqa: BLE001 - if prompt_toolkit has a terminal issue, fall back to input.
                    self._control_prompt_session = None
                else:
                    return default if value == "" and default is not None else value
            self._console.print(prompt, style=_USER, end="")
            value = input()
        return default if value == "" and default is not None else value

    def menu(self, title: str, options: list[tuple[str, str]], *, footer: str = "") -> None:
        if self.plain or self._console is None:
            print(title)
            for key, label in options:
                print(f"  {key}. {label}")
            if footer:
                print(footer)
            return
        from rich.panel import Panel
        from rich.table import Table

        table = Table.grid(padding=(0, 2))
        table.add_column(style=_USER, justify="right")
        table.add_column(style=_DIM)
        for key, label in options:
            table.add_row(key, label)
        renderable: Any = table
        if footer:
            from rich.console import Group
            from rich.text import Text

            renderable = Group(table, Text(footer, style="dim italic"))
        self._console.print(Panel(renderable, title=title, border_style=_HEAD))

    # -- per-turn activity heartbeat --------------------------------------------------------------

    def begin_turn(self) -> None:
        """Start a turn: show a persistent 'Working…' heartbeat that animates on its own (rich) timer.

        The heartbeat keeps ticking elapsed time / tokens / phase even while the main thread is blocked
        inside a model request or a long tool subprocess, because rich's Live runs a background refresh
        thread and the renderable recomputes elapsed time at render time.
        """

        self._buffer = ""
        self._plain_emitted = 0
        self._label_shown = False
        self._plain_last_tick = _now()
        if self.plain or self._console is None:
            self._heartbeat = _PlainHeartbeat()
            return
        from rich.live import Live

        self._heartbeat = _Heartbeat()
        self._live = Live(
            self._heartbeat,
            console=self._console,
            refresh_per_second=4,
            transient=True,  # the one-line heartbeat is erased when we commit text or finish
        )
        self._live.start()

    # Back-compat alias: callers that predate begin_turn() still work.
    def start_stream(self) -> None:
        self.begin_turn()

    def set_phase(self, phase: str) -> None:
        """Update what the heartbeat says we're doing (e.g. 'thinking', 'running cargo test')."""

        if self._heartbeat is not None:
            self._heartbeat.phase = phase
        if self.plain or self._console is None:
            self._maybe_plain_tick(force=True)

    def add_tokens(self, count: int) -> None:
        if count and self._heartbeat is not None:
            self._heartbeat.tokens += count

    def _maybe_plain_tick(self, *, force: bool = False) -> None:
        # Plain/non-tty mode can't animate; emit a throttled one-line activity update instead.
        if not (self.plain or self._console is None) or self._heartbeat is None:
            return
        now = _now()
        if not force and (now - self._plain_last_tick) < 10.0:
            return
        self._plain_last_tick = now
        print(f"  · {self._heartbeat.render_plain()}", flush=True)

    # -- streamed assistant reply -----------------------------------------------------------------

    def _ensure_label(self) -> None:
        if self._label_shown:
            return
        self._label_shown = True
        if self.plain or self._console is None:
            print(f"\n{self.assistant_label} › ", end="", flush=True)
        else:
            self._console.print(f"{self.assistant_label} ›", style=_ASSISTANT)

    def push_delta(self, delta: str) -> None:
        if not delta:
            return
        self._buffer += delta
        if self._heartbeat is not None:
            self._heartbeat.phase = "responding"
        if self.plain or self._console is None:
            self._ensure_label()
            print(delta, end="", flush=True)
            self._plain_emitted = len(self._buffer)
            return
        # Rich: while text streams, swap the heartbeat to show a running char count. The actual text is
        # rendered once, as Markdown, by _commit_step() — never live (a growing multi-line buffer taller
        # than the viewport can't be safely erased and would duplicate).
        if self._heartbeat is not None:
            self._heartbeat.chars = len(self._buffer)

    def _commit_step(self) -> None:
        """Finalize the current text step: erase the live preview and print it once as permanent Markdown."""

        if self.plain or self._console is None:
            remainder = self._buffer[self._plain_emitted :]
            if remainder:
                self._ensure_label()
                print(remainder, end="")
            if self._buffer:
                print()
            self._buffer = ""
            self._plain_emitted = 0
            return
        if self._buffer.strip():
            # Pause the live heartbeat, print permanent Markdown above it, then the heartbeat continues.
            from rich.markdown import Markdown

            if self._live is not None:
                self._live.stop()
            self._ensure_label()
            self._console.print(Markdown(self._buffer))
            if self._live is not None and self._heartbeat is not None:
                self._heartbeat.chars = 0
                self._live.start()
        self._buffer = ""

    def end_stream(self, *, fallback_text: str = "", stop_reason: str = "") -> None:
        # If a non-streaming transport produced text without deltas, render it now.
        if not self._buffer and fallback_text:
            self._buffer = fallback_text
        produced = bool(self._buffer.strip())
        # Tear the heartbeat down first so it never lingers under the final text.
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._heartbeat = None
        if produced:
            self._commit_step_final()
        elif not self._label_shown:
            note = f"(no text; stopped: {stop_reason})" if stop_reason else "(no response)"
            self.info(note)
        if not (self.plain or self._console is None):
            self._console.print()

    def _commit_step_final(self) -> None:
        # Like _commit_step but the heartbeat is already gone (end of turn): just print the text once.
        if self.plain or self._console is None:
            remainder = self._buffer[self._plain_emitted :]
            if remainder:
                self._ensure_label()
                print(remainder, end="")
            if self._buffer:
                print()
        else:
            from rich.markdown import Markdown

            self._ensure_label()
            self._console.print(Markdown(self._buffer))
        self._buffer = ""
        self._plain_emitted = 0

    # -- tool activity ----------------------------------------------------------------------------

    def tool_starting(self, name: str, summary: str) -> None:
        """Called just before a tool runs — the longest silent stretch of a turn."""

        self.set_phase(f"running {summary}" if summary else f"running {name}")

    def tool_event(self, name: str, summary: str, ok: bool) -> None:
        # Committing here separates the text that preceded the tool from the tool line, and finalizes that
        # text step so the buffer resets before the next step streams.
        self._commit_step()
        if self.plain or self._console is None:
            mark = "ok" if ok else "error"
            print(f"  · {name}: {summary} ({mark})")
            self._label_shown = False
            return
        from rich.text import Text

        line = Text()
        line.append("  ")
        line.append("✓ " if ok else "✗ ", style=_OK if ok else _ERR)
        line.append(f"{name} ", style="bold")
        line.append(summary, style=_DIM)
        if self._live is not None:
            self._live.stop()
        self._console.print(line)
        if self._live is not None and self._heartbeat is not None:
            self._heartbeat.phase = "thinking"
            self._live.start()
        # The next step's text should re-announce the speaker.
        self._label_shown = False

    def harvest_note(self, count: int) -> None:
        if count <= 0:
            return
        msg = f"  ⤷ harvested {count} bundle(s) into the improvement inbox"
        if self.plain or self._console is None:
            print(msg + "\n")
        else:
            self._console.print(msg, style=_TOOL)


class _NullContext:
    def __enter__(self) -> _NullContext:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Heartbeat:
    """A one-line 'Working…' renderable that recomputes elapsed time at render time.

    Because rich's Live refreshes this on its own background thread, the elapsed clock keeps ticking even
    while the main thread is blocked inside a model request or a long tool subprocess — which is exactly
    when the user otherwise sees nothing and wonders if it hung.
    """

    def __init__(self) -> None:
        self.start = _now()
        self.tokens = 0
        self.chars = 0
        self.phase = "thinking"

    def _text(self) -> str:
        elapsed = _fmt_elapsed(_now() - self.start)
        parts = [elapsed]
        if self.tokens:
            parts.append(f"↓ {_fmt_tokens(self.tokens)} tokens")
        if self.phase == "responding" and self.chars:
            parts.append(f"{self.chars} chars")
        elif self.phase:
            parts.append(self.phase)
        return f"Working… ({' · '.join(parts)})"

    def __rich__(self) -> Any:
        from rich.spinner import Spinner

        return Spinner("dots", text=self._text(), style=_DIM)

    def render_plain(self) -> str:
        return self._text()


class _PlainHeartbeat(_Heartbeat):
    """Same state tracking as :class:`_Heartbeat`, used in plain mode (no rich render)."""
