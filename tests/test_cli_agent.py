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
        self.systems: list[str] = []

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
    ) -> MessagesTurn:
        self.systems.append(system)
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


def test_interactive_session_system_prompt_includes_glm_identity(tmp_path: Path) -> None:
    transport = ScriptedTransport([_end("identity noted")])
    session = InteractiveSession(
        api_key="k",
        base_url="https://api.z.ai/api/anthropic",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        model="glm-5.2",
        effort="high",
    )
    session._transport = transport

    session.send("what model are you")

    assert "SelfHarness Code provider: GLM via Z.ai." in transport.systems[-1]
    assert "Configured model id: glm-5.2." in transport.systems[-1]
    assert "Configured reasoning effort: high." in transport.systems[-1]
    assert "Do not infer identity from the API protocol or compatibility layer." in transport.systems[-1]


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
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert "Current user request:\n\nfix it" in prompt
    assert "SelfHarness Code provider: Codex headless CLI." in prompt
    assert "Configured model id: gpt-5.6." in prompt
    assert "Headless binary:" in prompt
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


def test_headless_command_filters_provider_invalid_effort(tmp_path: Path) -> None:
    from self_harness.cli_agent.session import _headless_command

    codex_max = _headless_command(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        last_message_path=tmp_path / "last.txt",
        timeout_seconds=30,
        effort="max",
    )
    codex_xhigh = _headless_command(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        last_message_path=tmp_path / "last.txt",
        timeout_seconds=30,
        effort="xhigh",
    )
    claude_max = _headless_command(
        backend="claude",
        binary="claude",
        workdir=tmp_path,
        last_message_path=tmp_path / "last.txt",
        timeout_seconds=30,
        effort="max",
    )

    assert not any("model_reasoning_effort" in part for part in codex_max)
    assert 'model_reasoning_effort="xhigh"' in codex_xhigh
    assert "--effort" in claude_max
    assert "max" in claude_max


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


def test_repl_identity_question_is_answered_locally(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    lines = iter(["what model are you", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = InteractiveSession(
        api_key="k",
        base_url="https://api.z.ai/api/anthropic",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        model="glm-5.2",
        effort="xhigh",
    )

    assert run_repl(session, banner=False, root=None, plain=True) == 0

    out = capsys.readouterr().out
    assert "provider: glm, model: glm-5.2, effort: xhigh" in out
    assert "transport: Z.ai Anthropic-compatible Messages API" in out
    assert "glm ›" not in out


class StubJudgeProvider:
    def __init__(self, provider_id: str, admitted: bool, criterion: str | None, reason: str) -> None:
        self.provider_id = provider_id
        self.admitted = admitted
        self.criterion = criterion
        self.reason = reason

    def admit(self, candidate: object):
        from self_harness.cli_agent.ux_harvest import AdmissionResult

        del candidate
        return AdmissionResult(self.admitted, self.provider_id, self.criterion, self.reason)


def _ux_harvester_for_test(tmp_path: Path, provider: StubJudgeProvider):
    from self_harness.cli_agent.ux_harvest import JudgeProviderRegistry, SecondaryModelJudge, UxFailureHarvester

    return UxFailureHarvester(
        inbox_dir=tmp_path / "inbox",
        workdir=tmp_path,
        judge=SecondaryModelJudge(registry=JudgeProviderRegistry(providers=[provider])),
    )


def test_repl_report_command_admits_ux_bundle(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    lines = iter(["/report model identity contradicted runtime state", "/harvested", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = InteractiveSession(
        api_key="k",
        base_url="https://api.z.ai/api/anthropic",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        ux_harvester=_ux_harvester_for_test(
            tmp_path,
            StubJudgeProvider("codex", True, "The identity answer uses SelfHarness runtime state.", "valid"),
        ),
        model="glm-5.2",
    )

    assert run_repl(session, banner=False, root=None, plain=True) == 0

    out = capsys.readouterr().out
    assert "semantic issue candidate admitted" in out
    assert "ux: 1" in out
    bundle = json.loads(next((tmp_path / "inbox").glob("*ux*.json")).read_text(encoding="utf-8"))
    assert bundle["kind"] == "ux_complaint"
    assert bundle["admitting_judge"] == "codex"


def test_repl_report_command_shows_rejected_ux_bundle(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    lines = iter(["/report vague badness", "/rejected", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = InteractiveSession(
        api_key="k",
        base_url="https://api.z.ai/api/anthropic",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        ux_harvester=_ux_harvester_for_test(
            tmp_path,
            StubJudgeProvider("codex", False, None, "not checkable"),
        ),
        model="glm-5.2",
    )

    assert run_repl(session, banner=False, root=None, plain=True) == 0

    out = capsys.readouterr().out
    assert "semantic issue candidate rejected" in out
    assert "not checkable" in out
    assert list((tmp_path / "inbox" / "processed").glob("*.rejected"))


def test_repl_auto_harvests_identity_contradiction(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    fake = tmp_path / "codex"
    fake.write_text(
        """#!/bin/sh
out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    out="$1"
  fi
  shift || true
done
cat > prompt.txt
printf '%s' "I'm Claude, made by Anthropic." > "$out"
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    lines = iter(["please describe your runtime identity", "/harvested", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    session = HeadlessCliSession(
        backend="codex",
        binary=str(fake),
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        ux_harvester=_ux_harvester_for_test(
            tmp_path,
            StubJudgeProvider("glm", True, "Identity answers use runtime state.", "identity mismatch"),
        ),
    )

    assert run_repl(session, banner=False, root=None, plain=True) == 0

    out = capsys.readouterr().out
    assert "semantic issue candidate admitted" in out
    assert "ux: 1" in out
    bundle = json.loads(next((tmp_path / "inbox").glob("*ux*.json")).read_text(encoding="utf-8"))
    assert bundle["trigger"] == "provider-identity-contradiction"


def test_model_status_ignores_stale_invalid_effort(tmp_path: Path) -> None:
    from self_harness.cli_agent import repl

    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        effort="max",
    )

    assert (
        repl._model_status(session)
        == "provider: codex, model: provider default, effort: provider default (ignored invalid: max), binary: codex"
    )


class ScriptedRenderer:
    def __init__(self, answers: Sequence[str | BaseException]) -> None:
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
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

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
    from self_harness.cli_agent.model_discovery import EffortCatalog, ModelCatalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls: list[tuple[str, str | None]] = []

    def fake_discover(provider: str, *, binary: str | None = None) -> ModelCatalog:
        calls.append((provider, binary))
        return ModelCatalog(("gpt-live-a", "gpt-live-b"), "fake live catalog")

    monkeypatch.setattr(repl, "discover_provider_models", fake_discover)
    monkeypatch.setattr(
        repl,
        "discover_provider_efforts",
        lambda provider, *, model=None, binary=None: EffortCatalog(("low", "high"), "fake effort catalog"),
    )
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
    assert renderer.menus[2][0] == "CODEX Reasoning Effort"
    assert ("1", "low") in renderer.menus[2][1]
    assert ("2", "high") in renderer.menus[2][1]
    assert "source: fake effort catalog" in renderer.menus[2][2]
    assert user_config.load_config().get("code_model") == "gpt-live-b"


def test_model_palette_uses_discovered_codex_efforts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import EffortCatalog, ModelCatalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        repl,
        "discover_provider_models",
        lambda provider, *, binary=None: ModelCatalog(("gpt-high-only",), "fake live catalog"),
    )
    monkeypatch.setattr(
        repl,
        "discover_provider_efforts",
        lambda provider, *, model=None, binary=None: EffortCatalog(("high",), "Codex models cache"),
    )
    renderer = ScriptedRenderer(["1", "1", "1"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._model_palette(session, renderer)

    assert isinstance(selected, HeadlessCliSession)
    assert selected.model == "gpt-high-only"
    assert selected.effort == "high"
    assert renderer.prompts == ["provider", "model", "effort"]
    assert renderer.menus[2][1] == [("1", "high"), ("0", "provider default / unchanged")]


def test_model_palette_disables_glm_effort_for_models_without_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import EffortCatalog, ModelCatalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("ZAI_API_KEY", "secret")
    monkeypatch.setattr(
        repl,
        "discover_provider_models",
        lambda provider, *, binary=None: ModelCatalog(("glm-5.1",), "Z.ai /models"),
    )
    monkeypatch.setattr(
        repl,
        "discover_provider_efforts",
        lambda provider, *, model=None, binary=None: EffortCatalog(
            (),
            "Z.ai /models",
            "glm-5.1 does not advertise effort",
            fallback_allowed=False,
        ),
    )
    renderer = ScriptedRenderer(["4", "1"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._model_palette(session, renderer)

    assert isinstance(selected, InteractiveSession)
    assert selected.model == "glm-5.1"
    assert selected.effort is None
    assert renderer.errors == [
        "  ! glm does not support reasoning effort for glm-5.1: glm-5.1 does not advertise effort"
    ]


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


def test_model_palette_escape_from_model_returns_to_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import ModelCatalog
    from self_harness.cli_agent.ui import BackRequested

    monkeypatch.setattr(
        repl,
        "discover_provider_models",
        lambda provider, *, binary=None: ModelCatalog(("gpt-live-a", "gpt-live-b"), "fake live catalog"),
    )
    renderer = ScriptedRenderer(["1", BackRequested(), "0"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._model_palette(session, renderer)

    assert selected is session
    assert renderer.prompts == ["provider", "model", "provider"]
    assert [title for title, _options, _footer in renderer.menus] == [
        "Model / Provider",
        "CODEX Models",
        "Model / Provider",
    ]


def test_model_palette_escape_from_effort_returns_to_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import ModelCatalog
    from self_harness.cli_agent.ui import BackRequested

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        repl,
        "discover_provider_models",
        lambda provider, *, binary=None: ModelCatalog(("gpt-live-a", "gpt-live-b"), "fake live catalog"),
    )
    renderer = ScriptedRenderer(["1", "1", BackRequested(), "2", "0"])
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
    assert renderer.prompts == ["provider", "model", "effort", "model", "effort"]


def test_model_picker_escape_from_custom_returns_to_model_list(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import ModelCatalog
    from self_harness.cli_agent.ui import BackRequested

    monkeypatch.setattr(
        repl,
        "discover_provider_models",
        lambda provider, *, binary=None: ModelCatalog((), "fake live catalog", "provider unavailable"),
    )
    renderer = ScriptedRenderer(["c", BackRequested(), "d"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    model, cancelled = repl._model_picker("codex", session, renderer)

    assert model is None
    assert cancelled is False
    assert renderer.prompts == ["model", "custom model id", "model"]


def test_effort_picker_scopes_choices_to_codex(tmp_path: Path) -> None:
    from self_harness.cli_agent import repl

    renderer = ScriptedRenderer(["0"])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        effort="max",
    )

    picked = repl._effort_picker(session, renderer, provider="codex", current="max")

    assert picked is None
    title, options, footer = renderer.menus[-1]
    labels = [label for _key, label in options]
    assert title == "CODEX Reasoning Effort"
    assert "none" in labels
    assert "minimal" in labels
    assert "xhigh / extra high" in labels
    assert "max" not in labels
    assert "source: built-in defaults; Codex models cache:" in footer
    assert footer.endswith("current: provider default")


def test_effort_picker_allows_claude_max(tmp_path: Path) -> None:
    from self_harness.cli_agent import repl

    renderer = ScriptedRenderer(["5"])
    session = HeadlessCliSession(
        backend="claude",
        binary="claude",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    assert repl._effort_picker(session, renderer, provider="claude") == "max"
    assert ("5", "max") in renderer.menus[-1][1]


def test_model_command_rejects_codex_max_effort(tmp_path: Path, monkeypatch) -> None:
    from self_harness.cli_agent import repl

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    renderer = ScriptedRenderer([])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._handle_model_command(session, "codex gpt-5.6 max", renderer)

    assert selected is session
    assert session.model is None
    assert session.effort is None
    assert renderer.errors == ["  ! effort for codex must be one of: none, minimal, low, medium, high, xhigh"]


def test_model_command_allows_claude_max_effort(tmp_path: Path, monkeypatch) -> None:
    from self_harness.cli_agent import repl

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    renderer = ScriptedRenderer([])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._handle_model_command(session, "claude sonnet max", renderer)

    assert isinstance(selected, HeadlessCliSession)
    assert selected.backend == "claude"
    assert selected.model == "sonnet"
    assert selected.effort == "max"


def test_model_command_rejects_effort_not_supported_by_selected_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import EffortCatalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        repl,
        "discover_provider_efforts",
        lambda provider, *, model=None, binary=None: EffortCatalog(("high",), "Codex models cache"),
    )
    renderer = ScriptedRenderer([])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
    )

    selected = repl._handle_model_command(session, "codex gpt-high-only xhigh", renderer)

    assert selected is session
    assert renderer.errors == ["  ! effort for gpt-high-only must be one of: high"]


def test_effort_command_rejects_effort_not_supported_by_current_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import EffortCatalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        repl,
        "discover_provider_efforts",
        lambda provider, *, model=None, binary=None: EffortCatalog(("low",), "Codex models cache"),
    )
    renderer = ScriptedRenderer([])
    session = HeadlessCliSession(
        backend="codex",
        binary="codex",
        workdir=tmp_path,
        harness=initial_harness(),
        harvester=FailureHarvester(inbox_dir=tmp_path / "inbox", workdir=tmp_path),
        model="gpt-low-only",
    )

    selected = repl._handle_effort_command(session, "high", renderer)

    assert selected is session
    assert session.effort is None
    assert renderer.errors == ["  ! effort for gpt-low-only must be one of: low"]


def test_code_startup_ignores_stale_codex_max_effort(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    import self_harness.cli as cli
    import self_harness.cli_agent as cli_agent
    from self_harness import user_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = user_config.load_config()
    cfg.set("code_provider", "codex")
    cfg.set("code_effort", "max")
    cfg.save()
    captured: dict[str, HeadlessCliSession] = {}

    def fake_run_repl(session: HeadlessCliSession, **_kwargs: object) -> int:
        captured["session"] = session
        return 0

    monkeypatch.setattr(cli_agent, "run_repl", fake_run_repl)
    monkeypatch.setattr(cli, "_served_code_model_or_default", lambda provider, model, binary=None: (model, None))

    assert cli._run_code(
        root=tmp_path,
        harness_state=None,
        inbox_dir=None,
        max_steps=1,
        tool_timeout_seconds=1,
        harvest=False,
        resume=None,
        plain=True,
        local_harness=True,
    ) == 0

    assert captured["session"].effort is None
    assert "ignored code_effort='max': effort for codex must be one of:" in capsys.readouterr().out


def test_model_options_do_not_offer_incompatible_current_model() -> None:
    from self_harness.cli_agent import repl
    from self_harness.cli_agent.model_discovery import ModelCatalog

    options, model_by_choice = repl._model_options(
        ModelCatalog(("glm-5.1", "glm-5.2"), "Z.ai /models"),
        current_model="gpt-5.6",
    )

    labels = [label for _, label in options]
    assert "gpt-5.6 (current)" not in labels
    assert model_by_choice == {"1": "glm-5.1", "2": "glm-5.2"}
    assert ("1", "glm-5.1") in options
    assert ("2", "glm-5.2") in options


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
    assert "OPENAI_API_KEY is not set" in str(catalog.error)
    assert not marker.exists()


def test_provider_model_discovery_reads_codex_cache(tmp_path: Path) -> None:
    from self_harness.cli_agent.model_discovery import discover_provider_models

    (tmp_path / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-codex-live", "display_name": "GPT Codex Live"},
                    {"slug": "gpt-codex-mini", "display_name": "GPT Codex Mini"},
                ]
            }
        ),
        encoding="utf-8",
    )

    catalog = discover_provider_models("codex", env={"CODEX_HOME": str(tmp_path)})

    assert catalog.models == ("gpt-codex-live", "gpt-codex-mini")
    assert catalog.source == "Codex models cache"
    assert catalog.error is None


def test_provider_effort_discovery_reads_codex_cache(tmp_path: Path) -> None:
    from self_harness.cli_agent.model_discovery import discover_provider_efforts

    (tmp_path / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-high-only",
                        "supported_reasoning_levels": [
                            {"effort": "low"},
                            {"effort": "high"},
                        ],
                    },
                    {
                        "slug": "gpt-no-reasoning",
                        "supported_reasoning_levels": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    catalog = discover_provider_efforts("codex", model="gpt-high-only", env={"CODEX_HOME": str(tmp_path)})
    unsupported = discover_provider_efforts("codex", model="gpt-no-reasoning", env={"CODEX_HOME": str(tmp_path)})

    assert catalog.efforts == ("low", "high")
    assert catalog.source == "Codex models cache"
    assert catalog.error is None
    assert unsupported.efforts == ()
    assert unsupported.fallback_allowed is False


def test_provider_effort_discovery_reads_claude_help(tmp_path: Path) -> None:
    from self_harness.cli_agent.model_discovery import discover_provider_efforts

    fake = tmp_path / "claude"
    fake.write_text(
        """#!/bin/sh
cat <<'EOF'
Usage: claude [options]
  --effort <level>  Effort level for the current session (low, medium, high, xhigh, max)
EOF
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    catalog = discover_provider_efforts("claude", binary=str(fake), env={})

    assert catalog.efforts == ("low", "medium", "high", "xhigh", "max")
    assert catalog.source == f"{fake} --help"


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


def test_control_prompt_session_builds_when_toolkit_is_available() -> None:
    from self_harness.cli_agent.ui import _build_control_prompt_session

    session = _build_control_prompt_session()

    assert session is not None
