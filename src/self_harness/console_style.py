"""One shared, consistently-themed console for all CLI output.

Everything the everyday CLI prints — the home menu, settings, help, and the loop's status lines — goes
through this module so colors and layout are uniform and legible: who said or did what is distinguishable
at a glance (the user, GLM, a tool, the system). It wraps ``rich`` but degrades cleanly: when output is
not a TTY, ``NO_COLOR`` is set, or ``rich`` is unavailable, it falls back to plain ``print`` with no ANSI
so piped/scripted use and tests stay clean.

Palette (semantic, not literal — keep call sites meaning-oriented):
  heading  bold cyan      section titles, banners
  user     bold green     the human's turn / prompts
  glm      bold magenta   GLM 5.2's voice
  tool     yellow         tool activity (bash/read/write)
  system   dim            scaffolding, paths, hints
  success  green          things that worked
  warn     yellow         non-fatal problems
  error    bold red       failures
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from typing import Any

# Semantic role → rich style string. Call sites pass a role, never a raw color.
STYLES = {
    "heading": "bold cyan",
    "user": "bold green",
    "glm": "bold magenta",
    "tool": "yellow",
    "system": "dim",
    "success": "green",
    "warn": "yellow",
    "error": "bold red",
    "accent": "cyan",
}

_PREFIX = {
    "success": "✓ ",
    "warn": "! ",
    "error": "✗ ",
}


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("SELF_HARNESS_PLAIN"):
        return False
    return sys.stdout.isatty()


class Style:
    """A thin façade over a rich Console with a plain fallback. Use the module-level :data:`console`."""

    def __init__(self, *, force_plain: bool | None = None) -> None:
        self.plain = (not _color_enabled()) if force_plain is None else force_plain
        self._console: Any = None
        if not self.plain:
            try:
                from rich.console import Console
                from rich.theme import Theme

                self._console = Console(theme=Theme(STYLES))
            except Exception:  # noqa: BLE001 - any rich problem degrades to plain, never crashes the CLI.
                self.plain = True

    # -- primitives -------------------------------------------------------------------------------

    def line(self, text: str = "", role: str | None = None) -> None:
        """Print one line in the given semantic role (or unstyled)."""

        if self.plain or self._console is None:
            print(_plain_decorate(text, role))
            return
        if role and role in STYLES:
            self._console.print(text, style=role, highlight=False)
        else:
            self._console.print(text, highlight=False)

    def status(self, text: str, role: str = "system") -> None:
        """A short status line with a leading glyph for success/warn/error."""

        glyph = _PREFIX.get(role, "")
        if self.plain or self._console is None:
            print(_plain_decorate(glyph + text, role))
            return
        self._console.print(f"{glyph}{text}", style=role, highlight=False)

    def heading(self, text: str) -> None:
        self.line(text, "heading")

    def error(self, text: str) -> None:
        if self.plain or self._console is None:
            print(_plain_decorate("✗ " + text, "error"), file=sys.stderr)
        else:
            self._console.print(f"✗ {text}", style="error", highlight=False)

    def blank(self) -> None:
        print()

    # -- structured blocks ------------------------------------------------------------------------

    def rule(self, text: str = "") -> None:
        if self.plain or self._console is None:
            bar = "─" * 60
            print(f"{bar} {text}".rstrip() if text else bar)
            return
        from rich.rule import Rule

        self._console.print(Rule(text, style="accent"))

    def panel(self, body: str, *, title: str = "", role: str = "accent") -> None:
        if self.plain or self._console is None:
            if title:
                print(f"== {title} ==")
            print(body)
            return
        from rich.panel import Panel

        self._console.print(Panel(body, title=title, border_style=role, highlight=False))

    def table(self, rows: Sequence[Sequence[Any]], *, headers: Sequence[Any] | None = None) -> None:
        """Render aligned rows. Plain fallback pads with spaces."""

        if self.plain or self._console is None:
            widths: list[int] = []
            all_rows: list[Sequence[Any]] = []
            if headers:
                all_rows.append(headers)
            all_rows.extend(rows)
            for col in range(max((len(r) for r in all_rows), default=0)):
                widths.append(max((len(str(r[col])) for r in all_rows if col < len(r)), default=0))
            for r in all_rows:
                print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
            return
        from rich.table import Table

        table = Table(show_header=headers is not None, header_style="heading", box=None, pad_edge=False)
        ncols = max([len(r) for r in rows] + ([len(headers)] if headers else []), default=0)
        for i in range(ncols):
            table.add_column(str(headers[i]) if headers and i < len(headers) else "")
        for r in rows:
            table.add_row(*[str(c) for c in r])
        self._console.print(table)

    def prompt(self, label: str, role: str = "user") -> str:
        """Render a styled prompt label and read a line (always via stdlib input for testability)."""

        if self.plain or self._console is None:
            return input(label)
        # rich's input renders the markup then hands off to stdlib input().
        return str(self._console.input(f"[{role}]{label}[/{role}]"))


def _plain_decorate(text: str, role: str | None) -> str:
    # Plain mode has no color; glyphs already carry success/warn/error meaning, so just return text.
    return text


def styled_markup(text: str, role: str) -> str:
    """Return rich-markup-wrapped text for embedding inside a larger rich string (no-op in plain)."""

    if not console.plain and role in STYLES:
        return f"[{role}]{text}[/{role}]"
    return text


# The single shared instance the whole CLI uses.
console = Style()


def reset_for_test(*, force_plain: bool) -> Style:
    """Rebuild the shared console (tests toggle plain/colored deterministically)."""

    global console
    console = Style(force_plain=force_plain)
    return console
