from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from self_harness.adapters.agentic.agent_loop import run_agent_loop
from self_harness.adapters.llm.messages import MessagesTurn
from self_harness.cli_agent.harvest import FailureHarvester
from self_harness.cli_agent.session import InteractiveSession, load_session_harness
from self_harness.harness import harness_hash, initial_harness
from self_harness.task_sources import ingest_failing_bundle


class ScriptedTransport:
    """Replays a fixed list of MessagesTurns; records the messages it was given (incl. across calls)."""

    def __init__(self, turns: list[MessagesTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
    ) -> MessagesTurn:
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)


def _end(text: str) -> MessagesTurn:
    return MessagesTurn("end_turn", [{"type": "text", "text": text}], {"total_tokens": 1})


def _bash(cmd: str, tid: str = "t1") -> MessagesTurn:
    return MessagesTurn(
        "tool_use",
        [{"type": "tool_use", "id": tid, "name": "bash", "input": {"command": cmd}}],
        {"total_tokens": 1},
    )


# ---- agent loop: history reuse + observer -----------------------------------------------------------


def test_history_persists_across_calls(tmp_path: Path) -> None:
    transport = ScriptedTransport([_end("hi there"), _end("still here")])
    history: list[dict[str, Any]] = []
    run_agent_loop(
        transport=transport, system_prompt="s", task_prompt="first",
        workdir=tmp_path, env={}, history=history,
    )
    # After turn 1: user + assistant in history.
    assert [m["role"] for m in history] == ["user", "assistant"]
    run_agent_loop(
        transport=transport, system_prompt="s", task_prompt="second",
        workdir=tmp_path, env={}, history=history,
    )
    # Turn 2 appends another user+assistant; the second model call saw the full prior history.
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert transport.calls[1][0]["content"] == "first"  # prior turn carried into the second call


def test_on_tool_result_fires_per_tool(tmp_path: Path) -> None:
    transport = ScriptedTransport([_bash("true"), _end("done")])
    seen: list[tuple[str, bool]] = []
    run_agent_loop(
        transport=transport, system_prompt="s", task_prompt="go",
        workdir=tmp_path, env={},
        on_tool_result=lambda name, _inp, result: seen.append((name, result.is_error)),
    )
    assert seen == [("bash", False)]


# ---- harvester --------------------------------------------------------------------------------------


def _result(output: str, is_error: bool):
    from self_harness.adapters.agentic.tools import ToolResult

    return ToolResult(output=output, is_error=is_error)


def test_harvester_writes_bundle_for_failing_check_and_dedupes(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    h = FailureHarvester(inbox_dir=inbox, workdir=tmp_path)
    # A failing pytest is a check command -> harvested.
    h.observe("bash", {"command": "pytest -q"}, _result("exit_code=1", True))
    # The same command again is deduped.
    h.observe("bash", {"command": "pytest -q"}, _result("exit_code=1", True))
    written = h.flush(id_prefix="cli-001")
    assert len(written) == 1
    files = list(inbox.glob("*.json"))
    assert len(files) == 1
    bundle = json.loads(files[0].read_text())
    assert bundle["command"] == "pytest -q"
    # The bundle round-trips through the real ingestion path.
    task = ingest_failing_bundle(bundle)
    assert task["split"] == "held_in"


def test_harvester_ignores_non_check_and_successful_commands(tmp_path: Path) -> None:
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path)
    h.observe("bash", {"command": "grep foo bar"}, _result("", True))   # not a check command
    h.observe("bash", {"command": "pytest"}, _result("", False))        # passed, nothing to learn
    assert h.flush(id_prefix="x") == []


def test_harvester_snapshots_touched_files(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path)
    h.observe("write_file", {"path": "calc.py"}, _result("ok", False))
    h.observe("bash", {"command": "python3 -m pytest"}, _result("exit_code=1", True))
    h.flush(id_prefix="cli-001")
    bundle = json.loads(next((tmp_path / "inbox").glob("*.json")).read_text())
    assert bundle["files"]["calc.py"].startswith("def add")


def test_harvester_disabled_writes_nothing(tmp_path: Path) -> None:
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path, enabled=False)
    h.observe("bash", {"command": "pytest"}, _result("exit_code=1", True))
    assert h.flush(id_prefix="x") == []


def test_harvester_snapshots_command_named_file(tmp_path: Path) -> None:
    # Even if the agent only ran `python3 test_calc.py` (no read/write), the named file is snapshotted
    # so the harvested bundle is reproducible by the loop.
    (tmp_path / "test_calc.py").write_text("assert False\n", encoding="utf-8")
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path)
    h.observe("bash", {"command": "python3 test_calc.py"}, _result("exit_code=1", True))
    h.flush(id_prefix="cli-001")
    bundle = json.loads(next((tmp_path / "inbox").glob("*.json")).read_text())
    assert bundle["files"]["test_calc.py"] == "assert False\n"


# ---- session ----------------------------------------------------------------------------------------


def test_load_session_harness_falls_back_to_initial(tmp_path: Path) -> None:
    spec, evolving = load_session_harness(tmp_path / "missing.json")
    assert evolving is False
    assert harness_hash(spec) == harness_hash(initial_harness())


def test_session_multi_turn_and_reset(tmp_path: Path) -> None:
    transport = ScriptedTransport([_end("one"), _end("two")])
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path)
    session = InteractiveSession(
        api_key="k", base_url="https://example/api/anthropic", workdir=tmp_path,
        harness=initial_harness(), harvester=h,
    )
    session._transport = transport  # inject stub
    r1 = session.send("first")
    assert r1.final_text == "one"
    assert len(session.history) == 2
    r2 = session.send("second")
    assert r2.final_text == "two"
    assert len(session.history) == 4
    session.reset()
    assert session.history == []


def test_session_harvests_failing_command(tmp_path: Path) -> None:
    # Use a command that is in the check allowlist AND deterministically fails (exit 1) in any environment.
    transport = ScriptedTransport([_bash("python3 -c 'import sys; sys.exit(1)'"), _end("fixed it")])
    inbox = tmp_path / "inbox"
    h = FailureHarvester(inbox_dir=inbox, workdir=tmp_path)
    session = InteractiveSession(
        api_key="k", base_url="https://example/api/anthropic", workdir=tmp_path,
        harness=initial_harness(), harvester=h,
    )
    session._transport = transport
    result = session.send("run the tests")
    assert result.harvested  # a failing-check bundle was produced
    assert list(inbox.glob("*.json"))
