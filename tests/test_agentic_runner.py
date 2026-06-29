from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from self_harness.adapters.agentic.agent_loop import run_agent_loop
from self_harness.adapters.agentic.codex_verifier import parse_codex_verdict
from self_harness.adapters.agentic.runner import (
    AGENTIC_MODEL_ID,
    GLMAgenticRunner,
    GLMAgenticTaskAdapter,
)
from self_harness.adapters.agentic.tools import execute_tool, tool_schemas
from self_harness.adapters.llm.messages import AnthropicAgentTransport, MessagesTurn, RateLimitPacer, RateLimitPolicy
from self_harness.adapters.terminal_bench.agent_render import render_system_prompt
from self_harness.exceptions import CodexVerifierError, TaskLoadError
from self_harness.harness import apply_patch, initial_harness
from self_harness.types import (
    FailureCategory,
    HarnessOp,
    HarnessPatch,
    Split,
    Task,
    VerifierOutcome,
)

# --- tools -------------------------------------------------------------------


def test_tools_execute_in_workdir_and_block_traversal(tmp_path: Path) -> None:
    env = dict(os.environ)
    assert {schema["name"] for schema in tool_schemas()} == {"bash", "read_file", "write_file"}

    write = execute_tool("write_file", {"path": "a.txt", "content": "hi"}, workdir=tmp_path, env=env)
    assert not write.is_error
    assert (tmp_path / "a.txt").read_text() == "hi"

    read = execute_tool("read_file", {"path": "a.txt"}, workdir=tmp_path, env=env)
    assert read.output == "hi"

    failed = execute_tool("bash", {"command": "exit 7"}, workdir=tmp_path, env=env)
    assert failed.is_error
    assert "exit_code=7" in failed.output

    traversal = execute_tool("read_file", {"path": "../../etc/passwd"}, workdir=tmp_path, env=env)
    assert traversal.is_error

    unknown = execute_tool("nope", {}, workdir=tmp_path, env=env)
    assert unknown.is_error


def test_bash_tool_times_out(tmp_path: Path) -> None:
    result = execute_tool("bash", {"command": "sleep 5"}, workdir=tmp_path, env=dict(os.environ), timeout_seconds=1)
    assert result.is_error
    assert "timed out" in result.output


# --- agent loop --------------------------------------------------------------


class ScriptedTransport:
    """Replays a fixed list of MessagesTurns; records the messages it was given."""

    def __init__(self, turns: list[MessagesTurn]) -> None:
        self._turns = list(turns)
        self.seen_messages: list[Sequence[Mapping[str, Any]]] = []

    def create_message(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
    ) -> MessagesTurn:
        self.seen_messages.append([dict(m) for m in messages])
        return self._turns.pop(0)


def test_agent_loop_runs_tools_until_end_turn(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            MessagesTurn(
                "tool_use",
                [{"type": "tool_use", "id": "t1", "name": "write_file", "input": {"path": "x.txt", "content": "ok"}}],
                {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            ),
            MessagesTurn(
                "end_turn",
                [{"type": "text", "text": "done"}],
                {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8},
            ),
        ]
    )
    result = run_agent_loop(
        transport=transport,
        system_prompt="be an agent",
        task_prompt="write ok to x.txt",
        workdir=tmp_path,
        env=dict(os.environ),
    )
    assert result.stop_reason == "end_turn"
    assert result.steps == 2
    assert result.tool_calls == 1
    assert result.final_text == "done"
    assert result.usage == {"input_tokens": 16, "output_tokens": 6, "total_tokens": 22}
    assert (tmp_path / "x.txt").read_text() == "ok"
    # The second model call must include a tool_result for the tool_use id.
    second_call = transport.seen_messages[1]
    tool_result_msg = second_call[-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["tool_use_id"] == "t1"


def test_agent_loop_stops_at_max_steps(tmp_path: Path) -> None:
    looping = MessagesTurn(
        "tool_use",
        [{"type": "tool_use", "id": "t", "name": "bash", "input": {"command": "true"}}],
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )
    transport = ScriptedTransport([looping for _ in range(10)])
    result = run_agent_loop(
        transport=transport,
        system_prompt="s",
        task_prompt="p",
        workdir=tmp_path,
        env=dict(os.environ),
        max_steps=3,
    )
    assert result.stop_reason == "max_steps"
    assert result.steps == 3


# --- codex verdict parsing ---------------------------------------------------


def _jsonl(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def test_parse_codex_verdict_takes_last_agent_message() -> None:
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "x"},
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0}},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps({"passed": True, "reason": "ok"})},
        },
        {"type": "turn.completed"},
    )
    verdict = parse_codex_verdict(stdout)
    assert verdict == {"passed": True, "reason": "ok"}


def test_parse_codex_verdict_rejects_missing_or_malformed() -> None:
    with pytest.raises(CodexVerifierError):
        parse_codex_verdict(_jsonl({"type": "turn.completed"}))
    with pytest.raises(CodexVerifierError):
        parse_codex_verdict(_jsonl({"type": "item.completed", "item": {"type": "agent_message", "text": "not json"}}))


# --- messages transport tool round-trip (stub HTTP server) -------------------


@contextmanager
def _messages_server(captured: dict[str, Any]):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            length = int(self.headers.get("Content-Length", "0"))
            captured["request"] = json.loads(self.rfile.read(length).decode("utf-8"))
            captured["x_api_key"] = self.headers.get("x-api-key")
            payload = json.dumps(
                {
                    "stop_reason": "tool_use",
                    "content": [
                        {"type": "text", "text": "calling tool"},
                        {"type": "tool_use", "id": "tool_1", "name": "bash", "input": {"command": "ls"}},
                    ],
                    "usage": {"input_tokens": 9, "output_tokens": 5},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}/api/anthropic"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_agent_transport_passes_tools_and_preserves_tool_use() -> None:
    captured: dict[str, Any] = {}
    with _messages_server(captured) as base_url:
        transport = AnthropicAgentTransport(base_url=base_url, api_key="secret", model="glm-5.2", effort="xhigh")
        tools = tool_schemas()
        turn = transport.create_message(
            system="be an agent",
            messages=[{"role": "user", "content": "list files"}],
            tools=tools,
            max_tokens=512,
        )

    # tools were forwarded; auth header present; endpoint hit /v1/messages
    assert [t["name"] for t in captured["request"]["tools"]] == ["bash", "read_file", "write_file"]
    assert captured["request"]["system"] == "be an agent"
    assert captured["request"]["reasoning_effort"] == "xhigh"
    assert captured["request"]["thinking"] == {"type": "enabled"}
    assert captured["request"]["output_config"] == {"effort": "xhigh"}
    assert captured["x_api_key"] == "secret"
    # response preserved the tool_use block and stop_reason
    assert turn.stop_reason == "tool_use"
    assert turn.tool_uses()[0]["name"] == "bash"
    assert turn.usage == {"input_tokens": 9, "output_tokens": 5, "total_tokens": 14}


def test_agent_transport_minimal_effort_uses_zai_reasoning_effort() -> None:
    captured: dict[str, Any] = {}
    with _messages_server(captured) as base_url:
        transport = AnthropicAgentTransport(base_url=base_url, api_key="secret", model="glm-5.2", effort="minimal")
        transport.create_message(
            system="be an agent",
            messages=[{"role": "user", "content": "list files"}],
            tools=[],
            max_tokens=512,
        )

    assert captured["request"]["reasoning_effort"] == "minimal"
    assert captured["request"]["thinking"] == {"type": "enabled"}
    assert "output_config" not in captured["request"]


@contextmanager
def _rate_limit_then_success_server(captured: dict[str, Any]):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            captured.setdefault("requests", []).append(request)
            request_count = len(captured["requests"])
            if request_count == 1:
                payload = b'{"error":"[1302][Rate limit reached for requests]"}'
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "0")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = json.dumps(
                {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "ok after retry"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}/api/anthropic"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_agent_transport_retries_rate_limit_without_changing_model() -> None:
    captured: dict[str, Any] = {}
    retries: list[tuple[int, float, str]] = []
    with _rate_limit_then_success_server(captured) as base_url:
        transport = AnthropicAgentTransport(
            base_url=base_url,
            api_key="secret",
            model="glm-5.2",
            rate_limit_policy=RateLimitPolicy(
                max_attempts=3,
                base_backoff_seconds=0,
                max_backoff_seconds=0,
                min_interval_seconds=0,
            ),
            on_rate_limit_retry=lambda attempt, delay, reason: retries.append((attempt, delay, reason)),
        )
        turn = transport.create_message(system="s", messages=[{"role": "user", "content": "go"}], tools=[])

    assert turn.text() == "ok after retry"
    assert [request["model"] for request in captured["requests"]] == ["glm-5.2", "glm-5.2"]
    assert retries == [(1, 0.0, "status 429")]


def test_rate_limit_pacer_preserves_min_interval_between_successful_requests() -> None:
    captured: dict[str, Any] = {}
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    def sleeper(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    with _messages_server(captured) as base_url:
        transport = AnthropicAgentTransport(
            base_url=base_url,
            api_key="secret",
            model="glm-5.2",
            rate_limit_policy=RateLimitPolicy(max_attempts=1, min_interval_seconds=2),
            rate_limit_pacer=RateLimitPacer(clock=clock, sleeper=sleeper),
        )
        transport.create_message(system="s", messages=[{"role": "user", "content": "one"}], tools=[])
        transport.create_message(system="s", messages=[{"role": "user", "content": "two"}], tools=[])

    assert sleeps == [2.0]


# --- render_system_prompt ----------------------------------------------------


def test_render_system_prompt_changes_with_harness_edit() -> None:
    base = initial_harness()
    base_prompt = render_system_prompt(base)
    assert "Terminal Bench 2 Harbor" in base_prompt  # Figure 3 system prompt
    assert "most targeted command" in base_prompt  # verification surface

    edited, _ = apply_patch(
        base,
        HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", "Create the required output file immediately.")]),
    )
    edited_prompt = render_system_prompt(edited)
    assert edited_prompt != base_prompt
    assert "Create the required output file immediately." in edited_prompt


# --- runner integration (stubbed transport + verifier, no network) -----------


class _PassIfFileVerifier:
    def __init__(self, filename: str) -> None:
        self.filename = filename

    def judge(self, *, success_criteria: str, task_description: str, workdir: Path) -> VerifierOutcome:
        ok = (workdir / self.filename).is_file()
        return VerifierOutcome(
            passed=ok,
            terminal_cause=FailureCategory.VERIFIER_PASS.value if ok else FailureCategory.VERIFIER_FAIL.value,
            causal_status="confirmed" if ok else "rejected",
            mechanism="stub-judge",
            message="ok" if ok else "missing file",
        )


def _solve_then_finish(path: str, content: str) -> list[MessagesTurn]:
    return [
        MessagesTurn(
            "tool_use",
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "write_file",
                    "input": {"path": path, "content": content},
                }
            ],
            {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        ),
        MessagesTurn(
            "end_turn",
            [{"type": "text", "text": "done"}],
            {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        ),
    ]


def _task() -> Task:
    return Task(
        "write-answer",
        Split.HELD_IN,
        "agentic_coding",
        "Write answer.txt",
        {"success_criteria": "answer.txt exists"},
    )


def test_agentic_runner_emits_pass_record_with_reward_metadata() -> None:
    runner = GLMAgenticRunner(
        transport_factory=lambda: ScriptedTransport(_solve_then_finish("answer.txt", "42")),
        verifier=_PassIfFileVerifier("answer.txt"),
    )
    record = runner.run(_task(), initial_harness())

    assert record.passed
    assert record.outcome.terminal_cause == FailureCategory.VERIFIER_PASS.value
    assert record.metadata["reward_value"] == 1.0
    assert record.metadata["agent_tool_calls"] == 1
    assert record.metadata["solver_token_usage"]["total_tokens"] == 11
    assert [event.kind for event in record.trace][0] == "workspace"
    assert record.trace[-1].kind == "verdict"


def test_agentic_runner_emits_fail_record_when_unsolved() -> None:
    runner = GLMAgenticRunner(
        transport_factory=lambda: ScriptedTransport(
            [MessagesTurn("end_turn", [{"type": "text", "text": "giving up"}], {})]
        ),
        verifier=_PassIfFileVerifier("answer.txt"),
    )
    record = runner.run(_task(), initial_harness())

    assert not record.passed
    assert record.outcome.terminal_cause == FailureCategory.VERIFIER_FAIL.value
    assert record.metadata["reward_value"] == 0.0


def test_agentic_runner_signature_is_deterministic_across_attempts() -> None:
    runner = GLMAgenticRunner(
        transport_factory=lambda: ScriptedTransport(
            [MessagesTurn("end_turn", [{"type": "text", "text": "x"}], {})]
        ),
        verifier=_PassIfFileVerifier("answer.txt"),
    )
    first = runner.run(_task(), initial_harness(), attempt_index=0)
    second = runner.run(_task(), initial_harness(), attempt_index=1)
    # Same outcome signature across attempts so cluster_failures produces high-support patterns.
    assert (first.outcome.terminal_cause, first.outcome.causal_status, first.outcome.mechanism) == (
        second.outcome.terminal_cause,
        second.outcome.causal_status,
        second.outcome.mechanism,
    )


def test_agentic_runner_rejects_disallowed_metadata() -> None:
    runner = GLMAgenticRunner(
        transport_factory=lambda: ScriptedTransport([]),
        verifier=_PassIfFileVerifier("answer.txt"),
    )
    task = Task("x", Split.HELD_IN, "agentic_coding", "d", {"success_criteria": "y", "model": "evil"})
    with pytest.raises(TaskLoadError):
        runner.run(task, initial_harness())


def test_agentic_adapter_requires_api_key() -> None:
    from self_harness.exceptions import AgenticRunnerError

    with pytest.raises(AgenticRunnerError):
        GLMAgenticTaskAdapter(api_key="").runner()
    assert AGENTIC_MODEL_ID == "glm-5.2-agentic-runner"


def test_agentic_runner_materializes_workspace_files_and_solves() -> None:
    runner = GLMAgenticRunner(
        transport_factory=lambda: ScriptedTransport(
            [MessagesTurn("end_turn", [{"type": "text", "text": "seeded already"}], {})]
        ),
        verifier=_PassIfFileVerifier("input.txt"),
    )
    task = Task(
        "seeded",
        Split.HELD_IN,
        "agentic_coding",
        "Use the seeded file",
        {"success_criteria": "input.txt exists", "workspace_files": {"input.txt": "hello world"}},
    )
    record = runner.run(task, initial_harness())
    assert record.passed
