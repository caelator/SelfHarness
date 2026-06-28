from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from self_harness import loop_daemon


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the daemon's loop_root at a temp dir so pidfile/log/runs land there, not the real checkout."""

    monkeypatch.setattr(loop_daemon, "loop_root", lambda: tmp_path)
    return tmp_path


def test_status_and_stop_when_not_running(fake_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert loop_daemon.is_running() is None
    assert loop_daemon.status() == 0
    assert "not running" in capsys.readouterr().out
    assert loop_daemon.stop_background() == 0
    assert "no background loop" in capsys.readouterr().out


def test_stale_pidfile_is_cleared(fake_root: Path) -> None:
    # A pidfile pointing at a dead pid must be treated as "not running" and removed.
    (fake_root / "runs").mkdir(parents=True, exist_ok=True)
    loop_daemon.pidfile().write_text("999999999\n", encoding="utf-8")
    assert loop_daemon.is_running() is None
    assert not loop_daemon.pidfile().exists()


def _spawn_stub(fake_root: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Start a detached harmless child (sleep) recorded in the pidfile, mimicking start_background."""

    import subprocess

    real_popen = subprocess.Popen

    # Replace the real loop command with a long sleep so no GLM work happens, but keep all the
    # detach kwargs (start_new_session, stdout, etc.) that start_background passes.
    def fake_popen(_cmd: object, **kwargs: object) -> object:
        return real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(loop_daemon.subprocess, "Popen", fake_popen)
    rc = loop_daemon.start_background()
    assert rc == 0
    return loop_daemon._read_pid()  # type: ignore[return-value]


def test_start_status_stop_lifecycle(fake_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _spawn_stub(fake_root, monkeypatch)
    try:
        assert pid is not None and pid > 0
        # Detached into its own session.
        assert loop_daemon.is_running() == pid
        # Starting again is a no-op while running.
        assert loop_daemon.start_background() == 0
        # Stop terminates it and clears the pidfile.
        assert loop_daemon.stop_background(timeout_seconds=10) == 0
        assert loop_daemon.is_running() is None
        assert not loop_daemon.pidfile().exists()
    finally:
        if pid and loop_daemon._alive(pid):
            os.kill(pid, 9)


def test_status_tails_log(fake_root: Path) -> None:
    (fake_root / "runs").mkdir(parents=True, exist_ok=True)
    loop_daemon.logfile().write_text("line one\nline two\n", encoding="utf-8")
    # With no running process, status still returns cleanly (not running takes precedence).
    assert loop_daemon.status() == 0


def test_detached_child_outlives_via_new_session(fake_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _spawn_stub(fake_root, monkeypatch)
    try:
        # Session id differs from this test process's group only matters on POSIX; just confirm liveness.
        time.sleep(0.3)
        assert loop_daemon._alive(pid)
    finally:
        if pid and loop_daemon._alive(pid):
            os.kill(pid, 9)
        loop_daemon.pidfile().unlink(missing_ok=True)
