from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from self_harness.adapters.agentic.agent_loop import run_agent_loop
from self_harness.adapters.llm.messages import MessagesTurn
from self_harness.cli_agent.harvest import FailureHarvester
from self_harness.cli_agent.repl import run_repl
from self_harness.cli_agent.session import HeadlessCliSession, InteractiveSession, load_session_harness
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


def test_session_streaming_callbacks_and_tool_events(tmp_path: Path) -> None:
    # When on_text_delta is supplied the blocking stub is bypassed; but here we inject the stub directly
    # and just confirm the tool-event callback fires with the right (name, summary, ok) per tool.
    transport = ScriptedTransport([_bash("true"), _end("done")])
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path)
    session = InteractiveSession(
        api_key="k", base_url="https://example/api/anthropic", workdir=tmp_path,
        harness=initial_harness(), harvester=h,
    )
    session._transport = transport
    events: list[tuple[str, str, bool]] = []
    # on_text_delta=None keeps the injected blocking transport, so we exercise on_tool_event only.
    session.send("go", on_tool_event=lambda n, s, ok: events.append((n, s, ok)))
    assert events == [("bash", "$ true", True)]


def test_headless_codex_session_delegates_to_codex_exec(tmp_path: Path) -> None:
    fake = tmp_path / "codex"
    fake.write_text(
        """#!/bin/sh
out=""
printf '%s\\n' "$@" > args.txt
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    out="$1"
  fi
  shift || true
done
cat > prompt.txt
printf '%s' "codex fixed it" > "$out"
printf '%s\\n' '{"type":"turn.completed","usage":{"total_tokens":7}}'
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    h = FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path)
    session = HeadlessCliSession(
        backend="codex",
        binary=str(fake),
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=h,
        model="gpt-5.6",
        effort="xhigh",
    )

    result = session.send("fix it")

    assert result.stop_reason == "end_turn"
    assert result.final_text == "codex fixed it"
    assert result.usage == {"total_tokens": 7}
    assert [m["role"] for m in session.history] == ["user", "assistant"]
    assert "Current user request:\n\nfix it" in (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    args = (tmp_path / "args.txt").read_text(encoding="utf-8")
    assert "--model" in args
    assert "gpt-5.6" in args
    assert 'model_reasoning_effort="xhigh"' in args


def test_headless_print_session_supports_agy_and_claude(tmp_path: Path) -> None:
    fake = tmp_path / "headless"
    fake.write_text(
        """#!/bin/sh
printf '%s\\n' "$@" > args.txt
cat > prompt.txt
printf '%s\\n' "headless fixed it"
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    for backend in ("agy", "claude"):
        h = FailureHarvester(inbox_dir=tmp_path / f"inbox-{backend}", workdir=tmp_path)
        session = HeadlessCliSession(
            backend=backend,
            binary=str(fake),
            workdir=tmp_path,
            harness=initial_harness(),
            harvester=h,
            model=f"{backend}-model",
            effort="high",
        )
        result = session.send(f"fix with {backend}")
        assert result.stop_reason == "end_turn"
        assert result.final_text == "headless fixed it"
        assert session.history[-2]["content"] == f"fix with {backend}"
        args = (tmp_path / "args.txt").read_text(encoding="utf-8")
        assert f"{backend}-model" in args
        if backend == "claude":
            assert "--effort" in args
            assert "high" in args


def test_repl_model_command_changes_headless_model_and_effort(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fake = tmp_path / "codex"
    fake.write_text(
        """#!/bin/sh
out=""
printf '%s\\n' "$@" > args.txt
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    out="$1"
  fi
  shift || true
done
cat > prompt.txt
printf '%s' "model command ok" > "$out"
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("SELF_HARNESS_CODEX_BINARY", str(fake))
    lines = iter(["/model codex gpt 5.6 extra high", "hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = HeadlessCliSession(
        backend="codex",
        binary=str(fake),
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    assert run_repl(session, banner=False, root=None, plain=True) == 0

    out = capsys.readouterr().out
    assert "provider: codex, model: gpt-5.6, effort: xhigh" in out
    assert "codex › model command ok" in out
    args = (tmp_path / "args.txt").read_text(encoding="utf-8")
    assert "gpt-5.6" in args
    assert 'model_reasoning_effort="xhigh"' in args
    from self_harness import user_config

    cfg = user_config.load_config()
    assert cfg.get("code_provider") == "codex"
    assert cfg.get("code_model") == "gpt-5.6"
    assert cfg.get("code_effort") == "xhigh"


class ScriptedRenderer:
    def __init__(self, answers: Sequence[str]) -> None:
        self.answers = iter(answers)
        self.menus: list[tuple[str, list[tuple[str, str]], str]] = []
        self.prompts: list[str] = []
        self.infos: list[str] = []
        self.errors: list[str] = []

    def menu(self, title: str, options: list[tuple[str, str]], *, footer: str = "") -> None:
        self.menus.append((title, options, footer))

    def ask(self, label: str, *, default: str | None = None) -> str:
        del default
        self.prompts.append(label)
        return next(self.answers)

    def info(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)


def test_model_palette_queries_provider_models(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness import user_config
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import ModelCatalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls: list[tuple[str, str | None]] = []

    def fake_discover(provider: str, *, binary: str | None = None) -> ModelCatalog:
        calls.append((provider, binary))
        return ModelCatalog(("gpt-live-a", "gpt-live-b"), "fake live catalog")

    monkeypatch.setattr(repl, "discover_provider_models", fake_discover)
    renderer = ScriptedRenderer(["1", "2", "0"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._model_palette(session, renderer)

    assert isinstance(selected, HeadlessCliSession)
    assert selected.model == "gpt-live-b"
    assert calls == [("codex", "codex")]
    assert renderer.prompts == ["provider", "model", "effort"]
    assert renderer.menus[1][0] == "CODEX Models"
    assert ("2", "gpt-live-b") in renderer.menus[1][1]
    assert "source: fake live catalog" in renderer.menus[1][2]
    assert user_config.load_config().get("code_model") == "gpt-live-b"


def test_model_palette_allows_custom_model_when_discovery_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import ModelCatalog

    monkeypatch.setattr(
        repl,
        "discover_provider_models",
        lambda provider, *, binary=None: ModelCatalog((), "fake live catalog", "provider unavailable"),
    )
    renderer = ScriptedRenderer(["1", "c", "gpt-from-future", "0"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._model_palette(session, renderer)

    assert isinstance(selected, HeadlessCliSession)
    assert selected.model == "gpt-from-future"
    assert "could not query live model catalog (provider unavailable)" in renderer.menus[1][2]


def test_provider_model_discovery_reads_native_cli_catalog(tmp_path: Path) -> None:
    from self_harness.cli_agent.model_discovery import discover_provider_models

    fake = tmp_path / "agy"
    fake.write_text(
        """#!/bin/sh
if [ "$1" = "models" ]; then
  printf '%s\\n' "Gemini 3.5 Flash (Medium)" "Claude Sonnet 4.6 (Thinking)"
  exit 0
fi
exit 2
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    catalog = discover_provider_models("agy", binary=str(fake))

    assert catalog.models == ("Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)")
    assert catalog.error is None


def test_provider_model_discovery_does_not_call_unsupported_cli_catalogs(tmp_path: Path) -> None:
    from self_harness.cli_agent.model_discovery import discover_provider_models

    marker = tmp_path / "called"
    fake = tmp_path / "codex"
    fake.write_text(
        f"""#!/bin/sh
touch {marker}
exit 0
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    catalog = discover_provider_models("codex", binary=str(fake), env={})

    assert catalog.models == ()
    assert "OPENAI_API_KEY is not set" == catalog.error
    assert not marker.exists()


def test_repl_command_palette_can_exit(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    lines = iter(["/menu", "0"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    assert run_repl(session, banner=False, root=tmp_path, plain=True) == 0

    out = capsys.readouterr().out
    assert "SelfHarness Command Palette" in out
    assert "Exit SelfHarness Code" in out


def test_repl_config_palette_updates_runtime_defaults(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    lines = iter(["/config", "1", "7", "/status", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    assert run_repl(session, banner=False, root=tmp_path, plain=True) == 0

    out = capsys.readouterr().out
    assert "Runtime Config" in out
    assert "max steps: 7" in out
    from self_harness import user_config

    assert user_config.load_config().get("max_steps") == 7


def test_repl_thread_switch_uses_saved_session(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from self_harness.cli_agent.sessions import SessionRecord, save_session

    save_session(
        tmp_path,
        SessionRecord(
            id="code-old",
            workdir=str(tmp_path),
            harness_hash="abc",
            updated_at="t9",
            history=[{"role": "user", "content": "old"}],
            turns=[{"user": "old", "stop_reason": "end_turn"}],
        ),
    )
    lines = iter(["/thread switch code-old", "/status", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    assert run_repl(session, banner=False, root=tmp_path, plain=True) == 0

    out = capsys.readouterr().out
    assert "switched to thread code-old" in out
    assert "thread: code-old" in out
    assert session.history == [{"role": "user", "content": "old"}]


# ---- @file context expansion ------------------------------------------------------------------------


def test_expand_mentions_inlines_workdir_file(tmp_path: Path) -> None:
    from self_harness.cli_agent.context import expand_mentions

    (tmp_path / "notes.txt").write_text("hello from notes", encoding="utf-8")
    augmented, inlined = expand_mentions("explain @notes.txt please", tmp_path)
    assert inlined == ["notes.txt"]
    assert "Contents of notes.txt:" in augmented
    assert "hello from notes" in augmented


def test_expand_mentions_rejects_escape_and_missing(tmp_path: Path) -> None:
    from self_harness.cli_agent.context import expand_mentions

    # A traversal escape and a missing file both resolve to nothing; the line is returned unchanged.
    line, inlined = expand_mentions("see @../secret.txt and @nope.txt", tmp_path)
    assert inlined == []
    assert line == "see @../secret.txt and @nope.txt"


def test_expand_mentions_trims_trailing_punctuation(tmp_path: Path) -> None:
    from self_harness.cli_agent.context import expand_mentions

    (tmp_path / "README.md").write_text("# Title", encoding="utf-8")
    _, inlined = expand_mentions("look at @README.md.", tmp_path)
    assert inlined == ["README.md"]


# ---- session persistence + resume -------------------------------------------------------------------


def test_session_record_round_trips(tmp_path: Path) -> None:
    from self_harness.cli_agent.sessions import (
        SessionRecord,
        latest_session,
        list_sessions,
        load_session,
        save_session,
    )

    rec = SessionRecord(
        id="code-A", workdir=str(tmp_path), harness_hash="abc", created_at="t0", updated_at="t1",
        history=[{"role": "user", "content": "hi"}], turns=[{"user": "hi"}], harvested=["b1"],
    )
    save_session(tmp_path, rec)
    loaded = load_session(tmp_path, "code-A")
    assert loaded is not None
    assert loaded.history == [{"role": "user", "content": "hi"}]
    assert loaded.harvested == ["b1"]
    assert [r.id for r in list_sessions(tmp_path)] == ["code-A"]
    assert latest_session(tmp_path) is not None
    assert load_session(tmp_path, "missing") is None


def test_list_sessions_orders_by_updated_at(tmp_path: Path) -> None:
    from self_harness.cli_agent.sessions import SessionRecord, list_sessions, save_session

    save_session(tmp_path, SessionRecord(id="old", workdir=".", harness_hash="h", updated_at="t1"))
    save_session(tmp_path, SessionRecord(id="new", workdir=".", harness_hash="h", updated_at="t9"))
    assert [r.id for r in list_sessions(tmp_path)] == ["new", "old"]


# ---- plain renderer (no ANSI under non-tty) ---------------------------------------------------------


def test_plain_renderer_streams_without_ansi(capsys) -> None:  # type: ignore[no-untyped-def]
    from self_harness.cli_agent.ui import ConsoleRenderer

    renderer = ConsoleRenderer(plain=True)
    assert renderer.plain is True
    renderer.start_stream()
    renderer.push_delta("hello ")
    renderer.push_delta("world")
    renderer.end_stream()
    renderer.tool_event("bash", "$ ls", True)
    out = capsys.readouterr().out
    assert "hello world" in out
    assert "\x1b[" not in out  # no ANSI escape codes in plain mode


def test_renderer_does_not_duplicate_multistep_text(capsys) -> None:  # type: ignore[no-untyped-def]
    # Regression: a multi-step turn (text, tool, more text) must render each chunk exactly once.
    # The old renderer accumulated the whole turn in one Live(Markdown) buffer and re-emitted it on
    # every refresh once it overflowed the viewport, producing repeated blocks.
    from self_harness.cli_agent.ui import ConsoleRenderer

    renderer = ConsoleRenderer(plain=True)
    renderer.start_stream()
    renderer.push_delta("FIRST STEP narration")
    renderer.tool_event("bash", "$ cargo test", True)  # commits step 1
    renderer.push_delta("SECOND STEP the final evaluation")
    renderer.end_stream()  # commits step 2
    out = capsys.readouterr().out
    assert out.count("FIRST STEP narration") == 1
    assert out.count("SECOND STEP the final evaluation") == 1


def test_renderer_uses_fallback_text_when_no_deltas(capsys) -> None:  # type: ignore[no-untyped-def]
    # A non-streaming transport yields final_text via end_stream(fallback_text=...) with no push_delta.
    from self_harness.cli_agent.ui import ConsoleRenderer

    renderer = ConsoleRenderer(plain=True)
    renderer.start_stream()
    renderer.end_stream(fallback_text="the whole answer", stop_reason="end_turn")
    out = capsys.readouterr().out
    assert out.count("the whole answer") == 1


def test_heartbeat_plain_emits_activity_lines(capsys) -> None:  # type: ignore[no-untyped-def]
    # In plain mode the heartbeat can't animate, so phase changes emit throttled "Working…" lines —
    # the user always has feedback, including during the otherwise-silent tool-running stretch.
    from self_harness.cli_agent.ui import ConsoleRenderer

    renderer = ConsoleRenderer(plain=True)
    renderer.begin_turn()
    renderer.set_phase("thinking")
    renderer.tool_starting("bash", "$ cargo test")  # phase change -> forced tick
    renderer.tool_event("bash", "$ cargo test", True)
    renderer.push_delta("done")
    renderer.end_stream(stop_reason="end_turn")
    out = capsys.readouterr().out
    assert "Working…" in out
    assert "running $ cargo test" in out  # the tool phase is surfaced
    assert "done" in out


def test_heartbeat_renderable_clock_advances() -> None:
    # The _Heartbeat text must reflect elapsed time computed at render time (so rich's refresh thread
    # makes it tick even while the main thread is blocked). Simulate by moving its start time back.
    from self_harness.cli_agent.ui import _Heartbeat

    hb = _Heartbeat()
    hb.start -= 75  # pretend 75s elapsed
    hb.tokens = 3500
    text = hb.render_plain()
    assert "1m 15s" in text
    assert "3.5k tokens" in text


def test_slash_command_matches_all_and_filters_prefix() -> None:
    from self_harness.cli_agent.ui import slash_command_matches

    commands = (("/menu", "palette"), ("/model", "model picker"), ("/threads", "thread picker"))

    assert [command for command, _ in slash_command_matches("/", commands)] == ["/menu", "/model", "/threads"]
    assert [command for command, _ in slash_command_matches("/mo", commands)] == ["/model"]
    assert slash_command_matches("hello /", commands) == []
    assert slash_command_matches("/model ", commands) == []


def test_slash_prompt_session_builds_when_toolkit_is_available() -> None:
    from self_harness.cli_agent.ui import _build_prompt_session

    session = _build_prompt_session((("/menu", "palette"),))

    assert session is not None
