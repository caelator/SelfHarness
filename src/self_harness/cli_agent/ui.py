"""Rich terminal UI for the interactive coding agent: streamed markdown, tool panels, spinner.

This is the only module that imports ``rich``. The renderer streams GLM's reply as live markdown (so
fenced code is syntax-highlighted as it arrives), shows a compact panel per tool call, and a spinner
while the model is thinking. A plain-text fallback (``--plain`` or a non-tty stdout) keeps piped and
scripted use working without ANSI escapes.
"""

from __future__ import annotations

import sys
from typing import Any


class ConsoleRenderer:
    """Streamed rich UI with a plain fallback. One renderer per session."""

    def __init__(self, *, plain: bool = False) -> None:
        # Fall back to plain output when asked, or when stdout is not a TTY (pipes, tests, CI).
        self.plain = plain or not sys.stdout.isatty()
        self._console: Any = None
        self._live: Any = None
        self._buffer = ""
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
        body.append("GLM 5.2 dev agent — self-improving harness\n", style="bold cyan")
        body.append(f"cwd      {workdir}\n", style="dim")
        body.append(f"harness  {harness_hash[:16]} ({lineage})\n", style="dim")
        body.append(f"harvest  {harvest}\n", style="dim")
        body.append("/help for commands · /exit to quit", style="dim italic")
        self._console.print(Panel(body, title="SelfHarness Code", border_style="cyan"))

    def info(self, text: str) -> None:
        if self.plain or self._console is None:
            print(text)
        else:
            self._console.print(text, style="dim")

    def error(self, text: str) -> None:
        if self.plain or self._console is None:
            print(text, file=sys.stderr)
        else:
            self._console.print(text, style="bold red")

    def prompt(self) -> str:
        # input() works in both modes; rich console.input would add styling but breaks piped stdin tests.
        return input("you › ")

    # -- thinking spinner -------------------------------------------------------------------------

    def thinking(self) -> Any:
        """Context manager: a spinner shown until the first token arrives (rich), or a no-op (plain)."""

        if self.plain or self._console is None:
            return _NullContext()
        from rich.status import Status

        return Status("thinking…", console=self._console, spinner="dots")

    # -- streamed assistant reply -----------------------------------------------------------------

    def start_stream(self) -> None:
        self._buffer = ""
        if self.plain or self._console is None:
            print("\nglm › ", end="", flush=True)
            return
        from rich.live import Live
        from rich.markdown import Markdown

        self._live = Live(Markdown(""), console=self._console, refresh_per_second=12, vertical_overflow="visible")
        self._live.start()

    def push_delta(self, delta: str) -> None:
        self._buffer += delta
        if self.plain or self._console is None:
            print(delta, end="", flush=True)
            return
        from rich.markdown import Markdown

        if self._live is not None:
            self._live.update(Markdown(self._buffer))

    def end_stream(self, *, fallback_text: str = "", stop_reason: str = "") -> None:
        if self.plain or self._console is None:
            if not self._buffer and fallback_text:
                print(fallback_text, end="")
            print("\n")
            return
        if self._live is not None:
            from rich.markdown import Markdown

            final = self._buffer or fallback_text or f"(no text; stopped: {stop_reason})"
            self._live.update(Markdown(final))
            self._live.stop()
            self._live = None
            self._console.print()

    # -- tool activity ----------------------------------------------------------------------------

    def tool_event(self, name: str, summary: str, ok: bool) -> None:
        if self.plain or self._console is None:
            mark = "ok" if ok else "error"
            print(f"  · {name}: {summary} ({mark})")
            return
        from rich.text import Text

        line = Text()
        line.append("  ", style="")
        line.append("✓ " if ok else "✗ ", style="green" if ok else "red")
        line.append(f"{name} ", style="bold")
        line.append(summary, style="dim")
        self._console.print(line)

    def harvest_note(self, count: int) -> None:
        if count <= 0:
            return
        msg = f"  ⤷ harvested {count} failing command(s) into the improvement inbox"
        if self.plain or self._console is None:
            print(msg + "\n")
        else:
            self._console.print(msg, style="yellow")


class _NullContext:
    def __enter__(self) -> _NullContext:
        return self

    def __exit__(self, *exc: object) -> None:
        return None
