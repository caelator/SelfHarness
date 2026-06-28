"""Rich terminal UI for the interactive coding agent: streamed markdown, tool lines, spinner.

This is the only module in ``cli_agent`` that imports ``rich``. It shares its color palette with
``console_style`` so the coding chat and the rest of the CLI agree on who-said-what (you = green,
GLM = magenta, tools = yellow, dim scaffolding, green/red for ok/error).

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
from typing import Any

from self_harness.console_style import STYLES

# Pull the shared palette so the chat matches the menu/settings/help/loop coloring exactly.
_GLM = STYLES["glm"]
_USER = STYLES["user"]
_TOOL = STYLES["tool"]
_DIM = STYLES["system"]
_OK = STYLES["success"]
_ERR = STYLES["error"]
_HEAD = STYLES["heading"]


class ConsoleRenderer:
    """Streamed rich UI with a plain fallback. One renderer per session."""

    def __init__(self, *, plain: bool = False) -> None:
        # Fall back to plain output when asked, or when stdout is not a TTY (pipes, tests, CI).
        self.plain = plain or not sys.stdout.isatty()
        self._console: Any = None
        self._live: Any = None
        self._spinner: Any = None
        self._buffer = ""
        self._plain_emitted = 0  # chars of the current buffer already streamed to screen (plain mode)
        self._label_shown = False
        if not self.plain:
            try:
                from rich.console import Console

                self._console = Console()
            except Exception:  # noqa: BLE001 - any rich import/init issue degrades to plain, never crashes.
                self.plain = True

    # -- session chrome ---------------------------------------------------------------------------

    def banner(self, *, workdir: str, harness_hash: str, lineage: str, harvest: str) -> None:
        if self.plain or self._console is None:
            print("SelfHarness Code — GLM 5.2 dev agent")
            print(f"  cwd: {workdir}")
            print(f"  harness: {harness_hash[:16]} ({lineage})")
            print(f"  harvest: {harvest}")
            print("Type /help for commands, /exit to quit.\n")
            return
        from rich.panel import Panel
        from rich.text import Text

        body = Text()
        body.append("GLM 5.2 dev agent — self-improving harness\n", style=_HEAD)
        body.append(f"cwd      {workdir}\n", style=_DIM)
        body.append(f"harness  {harness_hash[:16]} ({lineage})\n", style=_DIM)
        body.append(f"harvest  {harvest}\n", style=_DIM)
        body.append("/help for commands · /exit to quit", style="dim italic")
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

    def prompt(self) -> str:
        # input() works in both modes; rich's console.input would break piped-stdin tests, so style the
        # label ourselves and read with stdlib input.
        if self.plain or self._console is None:
            return input("you › ")
        self._console.print("you › ", style=_USER, end="")
        return input()

    # -- thinking spinner -------------------------------------------------------------------------

    def thinking(self) -> Any:
        """Context manager: a spinner shown until the first token arrives (rich), or a no-op (plain)."""

        if self.plain or self._console is None:
            return _NullContext()
        from rich.status import Status

        return Status("thinking…", console=self._console, spinner="dots")

    # -- streamed assistant reply -----------------------------------------------------------------

    def start_stream(self) -> None:
        """Begin a turn. No output yet — the 'glm ›' label and any region appear lazily on first token."""

        self._buffer = ""
        self._plain_emitted = 0
        self._label_shown = False
        self._live = None

    def _ensure_label(self) -> None:
        if self._label_shown:
            return
        self._label_shown = True
        if self.plain or self._console is None:
            print("\nglm › ", end="", flush=True)
        else:
            self._console.print("glm ›", style=_GLM)

    def push_delta(self, delta: str) -> None:
        if not delta:
            return
        self._buffer += delta
        if self.plain or self._console is None:
            self._ensure_label()
            print(delta, end="", flush=True)
            self._plain_emitted = len(self._buffer)
            return
        # Rich: the live element is a SINGLE fixed line (a spinner + running char count), never the growing
        # text itself. A one-line region is always safely erasable, so nothing can scroll off and survive.
        # The actual text is rendered once, as Markdown, by _commit_step().
        from rich.live import Live
        from rich.spinner import Spinner

        if self._live is None:
            self._ensure_label()
            self._spinner = Spinner("dots", text=self._progress_text(), style=_DIM)
            self._live = Live(
                self._spinner,
                console=self._console,
                refresh_per_second=8,
                transient=True,  # the one-line indicator is erased on stop; the committed Markdown remains
            )
            self._live.start()
        else:
            self._spinner.update(text=self._progress_text())

    def _progress_text(self) -> str:
        return f"receiving… ({len(self._buffer)} chars)"

    def _commit_step(self) -> None:
        """Finalize the current text step: erase the live preview and print it once as permanent Markdown."""

        if self.plain or self._console is None:
            # Print any buffered text not already streamed live (e.g. fallback_text set with no deltas),
            # then end the line. Streamed text is append-only, so we only emit the remainder.
            remainder = self._buffer[self._plain_emitted :]
            if remainder:
                self._ensure_label()
                print(remainder, end="")
            if self._buffer:
                print()
            self._buffer = ""
            self._plain_emitted = 0
            return
        if self._live is not None:
            self._live.stop()  # transient → preview erased
            self._live = None
        if self._buffer.strip():
            from rich.markdown import Markdown

            self._console.print(Markdown(self._buffer))
        self._buffer = ""

    def end_stream(self, *, fallback_text: str = "", stop_reason: str = "") -> None:
        # If a non-streaming transport produced text without deltas, render it now.
        if not self._buffer and fallback_text:
            self._buffer = fallback_text
            if not self._label_shown:
                self._ensure_label()
        if not self._buffer and not self._label_shown:
            # Nothing at all was produced this turn.
            note = f"(no text; stopped: {stop_reason})" if stop_reason else "(no response)"
            self.info(note)
            return
        self._commit_step()
        if not (self.plain or self._console is None):
            self._console.print()

    # -- tool activity ----------------------------------------------------------------------------

    def tool_event(self, name: str, summary: str, ok: bool) -> None:
        # Committing here separates the text that preceded the tool from the tool line, and finalizes that
        # text step so the buffer resets before the next step streams.
        self._commit_step()
        if self.plain or self._console is None:
            mark = "ok" if ok else "error"
            print(f"  · {name}: {summary} ({mark})")
            return
        from rich.text import Text

        line = Text()
        line.append("  ")
        line.append("✓ " if ok else "✗ ", style=_OK if ok else _ERR)
        line.append(f"{name} ", style="bold")
        line.append(summary, style=_DIM)
        self._console.print(line)
        # The next step's text should re-announce the speaker.
        self._label_shown = False

    def harvest_note(self, count: int) -> None:
        if count <= 0:
            return
        msg = f"  ⤷ harvested {count} failing command(s) into the improvement inbox"
        if self.plain or self._console is None:
            print(msg + "\n")
        else:
            self._console.print(msg, style=_TOOL)


class _NullContext:
    def __enter__(self) -> _NullContext:
        return self

    def __exit__(self, *exc: object) -> None:
        return None
