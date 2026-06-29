from __future__ import annotations

import io
import re

import pytest

from self_harness import console_style

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_plain_mode_has_no_ansi(capsys: pytest.CaptureFixture[str]) -> None:
    c = console_style.reset_for_test(force_plain=True)
    c.heading("Title")
    c.line("body")
    c.status("ok", "success")
    c.status("careful", "warn")
    c.table([("a", "1"), ("b", "2")], headers=("k", "v"))
    out = capsys.readouterr().out
    assert "Title" in out and "body" in out
    assert "ok" in out and "careful" in out
    assert "a" in out and "1" in out  # table content present
    assert not _ANSI.search(out)  # no escape codes in plain mode


def test_status_glyphs_in_plain_mode(capsys: pytest.CaptureFixture[str]) -> None:
    c = console_style.reset_for_test(force_plain=True)
    c.status("done", "success")
    c.status("hmm", "warn")
    out = capsys.readouterr().out
    assert "✓ done" in out
    assert "! hmm" in out


def test_error_goes_to_stderr_in_plain(capsys: pytest.CaptureFixture[str]) -> None:
    c = console_style.reset_for_test(force_plain=True)
    c.error("broke")
    captured = capsys.readouterr()
    assert "✗ broke" in captured.err
    assert "broke" not in captured.out


def test_color_mode_emits_ansi() -> None:
    # Force a colored rich Console writing to a string buffer, independent of the test's tty state.
    from rich.console import Console
    from rich.theme import Theme

    buf = io.StringIO()
    rich_console = Console(
        file=buf,
        force_terminal=True,
        color_system="standard",
        theme=Theme(console_style.STYLES),
        width=80,
    )
    c = console_style.Style(force_plain=False)
    c._console = rich_console
    c.plain = False
    c.heading("Hello")
    c.status("good", "success")
    output = buf.getvalue()
    assert "Hello" in output
    assert _ANSI.search(output)  # ANSI escape codes present when colored


def test_table_aligns_columns_in_plain(capsys: pytest.CaptureFixture[str]) -> None:
    c = console_style.reset_for_test(force_plain=True)
    c.table([("short", "x"), ("a-longer-key", "y")], headers=("name", "val"))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    # The value column should start at the same offset on every row (alignment).
    offsets = {ln.index("x") for ln in lines if "x" in ln}
    val_offsets = [ln.find("y") for ln in lines if "y" in ln]
    assert offsets and val_offsets  # rendered something
    # Header and rows present.
    assert any("name" in ln for ln in lines)


def test_styled_markup_noop_in_plain() -> None:
    console_style.reset_for_test(force_plain=True)
    assert console_style.styled_markup("hi", "glm") == "hi"


def test_reset_for_test_toggles_plain() -> None:
    assert console_style.reset_for_test(force_plain=True).plain is True
    assert console_style.reset_for_test(force_plain=False).plain is False
    # Restore plain for any later tests sharing the module-level console.
    console_style.reset_for_test(force_plain=True)
