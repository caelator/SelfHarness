import json
import ssl
import threading
import time
import urllib.error
from collections.abc import Callable
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from self_harness.adapters.http_verifier import HttpVerifierRunner, HttpVerifierTaskAdapter, _url_error_is_tls
from self_harness.cli import main
from self_harness.config import EngineConfig
from self_harness.corpus import TaskCorpus
from self_harness.engine import SelfHarnessEngine
from self_harness.evaluation import evaluate
from self_harness.exceptions import HttpVerifierError, TaskLoadError
from self_harness.harness import initial_harness
from self_harness.proposer import HeuristicProposer
from self_harness.types import FailureCategory, Split, Task


def test_http_verifier_maps_structured_pass_and_failure() -> None:
    with _verifier_server(_selector_response) as server:
        runner = HttpVerifierRunner(server.url)
        passed = runner.run(_task("pass", "pass"), initial_harness())
        failed = runner.run(_task("fail", "fail"), initial_harness())

    assert passed.passed
    assert passed.outcome.terminal_cause == "verifier-pass"
    assert passed.outcome.mechanism == "http-verifier"
    assert not failed.passed
    assert failed.outcome.terminal_cause == "assertion-fail"
    assert failed.outcome.mechanism == "fixture-http-assertion"
    assert server.requests[0]["verifier_selector"] == "pass"


def test_http_verifier_uses_fresh_workdir_per_attempt() -> None:
    with _verifier_server(_selector_response) as server:
        result = evaluate(HttpVerifierRunner(server.url), initial_harness(), [_task("fresh", "pass")], repeats=2)

    workdirs = {
        event.metadata["workdir"]
        for record in result.records
        for event in record.trace
        if event.metadata and "workdir" in event.metadata
    }

    assert len(workdirs) == 2
    assert {request["attempt_index"] for request in server.requests} == {0, 1}


def test_http_verifier_timeout_maps_to_timeout_outcome() -> None:
    def slow_response(_request: dict[str, object]) -> dict[str, object]:
        time.sleep(0.3)
        return _passed()

    with _verifier_server(slow_response) as server:
        record = HttpVerifierRunner(server.url, timeout_seconds=0.05).run(_task("slow", "pass"), initial_harness())

    assert not record.passed
    assert record.outcome.terminal_cause == "timeout"
    assert record.outcome.mechanism == "http-timeout"


def test_http_verifier_tls_classifier_inspects_wrapped_url_error_reasons() -> None:
    assert _url_error_is_tls(
        urllib.error.URLError(OSError("wrapped handshake failure", ssl.SSLError("certificate required")))
    )
    assert _url_error_is_tls(urllib.error.URLError(OSError("EOF occurred in violation of protocol")))
    assert _url_error_is_tls(
        urllib.error.URLError(BrokenPipeError("client certificate required")),
        "https://127.0.0.1/verify",
    )
    assert not _url_error_is_tls(
        urllib.error.URLError(BrokenPipeError("server closed request body")),
        "http://127.0.0.1/verify",
    )


def test_http_verifier_non_2xx_maps_to_environment_error() -> None:
    with _verifier_server(lambda _request: (503, {"error": "unavailable"})) as server:
        record = HttpVerifierRunner(server.url).run(_task("status", "pass"), initial_harness())

    assert not record.passed
    assert record.outcome.terminal_cause == "environment-error"
    assert record.outcome.mechanism == "http-status-error"


def test_http_verifier_fails_closed_on_unknown_category_and_malformed_json() -> None:
    with _verifier_server(
        lambda _request: {
            "passed": False,
            "failure_category": "partial-pass",
            "mechanism": "bad",
            "message": "bad",
        }
    ) as server:
        with pytest.raises(HttpVerifierError, match="invalid-failure-category"):
            HttpVerifierRunner(server.url).run(_task("unknown", "pass"), initial_harness())

    with _verifier_server(lambda _request: _RawResponse(200, b"not-json")) as server:
        with pytest.raises(HttpVerifierError, match="response must be JSON"):
            HttpVerifierRunner(server.url).run(_task("malformed", "pass"), initial_harness())


def test_http_verifier_rejects_selector_and_url_metadata_shapes() -> None:
    with _verifier_server(_selector_response) as server:
        runner = HttpVerifierRunner(server.url)
        with pytest.raises(TaskLoadError):
            runner.run(_task("bad-selector", "x" * 257), initial_harness())
        with pytest.raises(TaskLoadError):
            runner.run(
                Task(
                    id="url",
                    split=Split.HELD_IN,
                    failure_mode="http_verifier",
                    description="url",
                    metadata={"verifier_selector": "pass", "verifier_url": server.url},
                ),
                initial_harness(),
            )


def test_http_verifier_engine_artifacts_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    with _verifier_server(_selector_response) as server:
        _run_engine(first, server.url)
        _run_engine(second, server.url)

    assert _tree_bytes(first) == _tree_bytes(second)


def test_http_demo_cli_requires_trusted_url_and_runs(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    out_dir = tmp_path / "run"

    with pytest.raises(SystemExit) as exc:
        main(["http-demo", str(corpus), "--out", str(out_dir)])
    assert exc.value.code == 2

    with _verifier_server(_selector_response) as server:
        code = main(
            [
                "http-demo",
                str(corpus),
                "--trust-verifier-url",
                server.url,
                "--header",
                "X-Test: yes",
                "--rounds",
                "1",
                "--evaluation-repeats",
                "2",
                "--out",
                str(out_dir),
            ]
        )
        output = capsys.readouterr().out
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert code == 0
    assert "not a benchmark reproduction" in output
    assert manifest["model_id"] == "http-verifier"


def _selector_response(request: dict[str, object]) -> dict[str, object]:
    selector = request.get("verifier_selector")
    if selector == "pass":
        return _passed()
    return {
        "passed": False,
        "failure_category": FailureCategory.ASSERTION_FAIL.value,
        "mechanism": "fixture-http-assertion",
        "message": "fixture HTTP verifier failed",
    }


def _passed() -> dict[str, object]:
    return {
        "passed": True,
        "failure_category": None,
        "mechanism": "fixture-http-pass",
        "message": "fixture HTTP verifier passed",
    }


def _run_engine(out_dir: Path, url: str) -> None:
    adapter = HttpVerifierTaskAdapter(verifier_url=url)
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="http-fixture",
        tasks=[
            _task("held-in-pass", "pass", split=Split.HELD_IN),
            _task("held-out-pass", "pass", split=Split.HELD_OUT),
        ],
    )
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2, model_id="http-verifier"),
    )
    engine.run()


def _write_corpus(path: Path) -> None:
    payload = {
        "corpus_version": "1",
        "corpus_id": "http-cli-fixture",
        "tasks": [
            _task_row("held-in-pass", "held_in", "pass"),
            _task_row("held-out-pass", "held_out", "pass"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _task(id_: str, selector: str, *, split: Split = Split.HELD_IN) -> Task:
    return Task(
        id=id_,
        split=split,
        failure_mode="http_verifier",
        description=id_,
        metadata={"verifier_selector": selector},
    )


def _task_row(id_: str, split: str, selector: str) -> dict[str, object]:
    return {
        "id": id_,
        "split": split,
        "failure_mode": "http_verifier",
        "description": id_,
        "metadata": {"verifier_selector": selector},
    }


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


class _RawResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


class _Server:
    def __init__(self, httpd: ThreadingHTTPServer, thread: threading.Thread, requests: list[dict[str, object]]) -> None:
        self._httpd = httpd
        self._thread = thread
        self.requests = requests

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}/verify"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


@contextmanager
def _verifier_server(
    responder: Callable[[dict[str, object]], dict[str, object] | tuple[int, dict[str, object]] | _RawResponse],
):
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            requests.append(body)
            response = responder(body)
            if isinstance(response, _RawResponse):
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response.body)
                return
            if isinstance(response, tuple):
                status, payload = response
            else:
                status, payload = 200, response
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    server = _Server(httpd, thread, requests)
    try:
        yield server
    finally:
        server.close()
