"""Background process management for the continuous self-improvement loop.

Lets ``self-harness loop --background`` start the loop as a detached process that survives closing the
terminal, with ``self-harness loop status`` / ``loop stop`` to inspect and end it. Dependency-free and
portable across macOS/Linux: the child is spawned in its own session (``start_new_session=True``) with
stdout/stderr redirected to a log file, and its PID recorded in a pidfile next to it. Stop sends SIGTERM,
which the foreground loop turns into the same graceful "finish current run, then halt" path as Ctrl-C.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from self_harness.loop_paths import loop_root


def _state_dir() -> Path:
    d = loop_root() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pidfile() -> Path:
    return _state_dir() / "loop.pid"


def logfile() -> Path:
    return _state_dir() / "loop.log"


def _read_pid() -> int | None:
    pf = pidfile()
    if not pf.is_file():
        return None
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid


def _alive(pid: int) -> bool:
    """True if a process with ``pid`` exists (signal 0 probes without sending anything)."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else
    return True


def _reap_if_child(pid: int) -> None:
    """If ``pid`` is a child of this process and has exited, reap it so it stops showing as a zombie.

    In normal CLI use the background loop is reparented to init (its launcher exits immediately), so this
    is a no-op. It only matters when start/stop happen within one long-lived process (e.g. tests), where
    an unreaped child would otherwise report as 'alive' forever via os.kill(pid, 0).
    """

    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        return
    if reaped == pid:
        # Mark our local view: the next os.kill(pid, 0) will raise ProcessLookupError.
        return


def is_running() -> int | None:
    """Return the PID of the running background loop, or None (clearing a stale pidfile)."""

    pid = _read_pid()
    if pid is None:
        return None
    if _alive(pid):
        return pid
    pidfile().unlink(missing_ok=True)  # stale
    return None


def start_background(*, rounds: int = 1, seed: int = 0) -> int:
    """Spawn the loop as a detached process. Returns a process exit code (0 on success)."""

    existing = is_running()
    if existing is not None:
        print(f"loop already running in the background (pid {existing}).")
        print("  status: self-harness loop status\n  stop:   self-harness loop stop")
        return 0

    log = logfile()
    # Append so successive sessions keep history; the child prints a banner each start.
    log_handle = open(log, "a", encoding="utf-8")  # noqa: SIM115 - handed to the child; closed in parent below.
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "self_harness.cli", "loop", "--rounds", str(rounds), "--seed", str(seed)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from the controlling terminal so it survives terminal close
            cwd=str(loop_root()),
        )
    finally:
        log_handle.close()
    pidfile().write_text(str(proc.pid) + "\n", encoding="utf-8")
    print(f"continuous self-improvement loop started in the background (pid {proc.pid}).")
    print(f"  log:    {log}")
    print("  status: self-harness loop status")
    print("  stop:   self-harness loop stop")
    return 0


def stop_background(*, timeout_seconds: float = 60.0) -> int:
    """Signal the background loop to stop gracefully (SIGTERM) and wait for it to exit."""

    import time

    pid = is_running()
    if pid is None:
        print("no background loop is running.")
        return 0
    print(f"stopping background loop (pid {pid}); it will finish the current run first…")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pidfile().unlink(missing_ok=True)
        print("loop already exited.")
        return 0
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _reap_if_child(pid)
        if not _alive(pid):
            pidfile().unlink(missing_ok=True)
            print("loop stopped.")
            return 0
        time.sleep(0.5)
    print(f"loop did not stop within {timeout_seconds:.0f}s; it may be mid-run. Re-run `loop stop` to retry.")
    return 1


def status(*, tail_lines: int = 12) -> int:
    """Print whether the background loop is running, plus the tail of its log."""

    pid = is_running()
    if pid is None:
        print("background loop: not running")
        print("  start: self-harness loop --background")
        return 0
    print(f"background loop: RUNNING (pid {pid})")
    print(f"  log: {logfile()}")
    lines = _tail(logfile(), tail_lines)
    if lines:
        print("  recent activity:")
        for line in lines:
            print(f"    {line}")
    print("  stop: self-harness loop stop")
    return 0


def _tail(path: Path, n: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in content[-n:] if ln.strip()]
