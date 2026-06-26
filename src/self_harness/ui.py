from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from self_harness.adapters.llm.paper_models import GLMClient
from self_harness.audit import (
    audit_trajectory_rows,
    inspect_harness_run,
    summarize_audit_run,
)
from self_harness.config import EngineConfig
from self_harness.demo import ToyRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.llm_proposer import LLMProposer
from self_harness.model_backend_preflight import (
    ModelBackendPreflightError,
    UrlLibChatCompletionTransport,
    evaluate_model_backend_preflight,
    model_backend_preflight_report_to_jsonable,
)
from self_harness.proposer import HeuristicProposer
from self_harness.types import ProposalBudget, stable_json_dumps, to_jsonable

UI_SCHEMA_VERSION = "1.0"


@dataclass
class UiJob:
    id: str
    run_id: str
    path: Path
    status: str
    config: dict[str, int]
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    summary: dict[str, Any] | None = None
    events: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    proposer_mode: str = "heuristic"


class HarnessUiApp:
    def __init__(self, *, root: Path, runs_dir: Path, proposer_mode: str = "heuristic") -> None:
        self.root = root.resolve()
        self.runs_dir = _resolve_child(self.root, runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.proposer_mode = _normalize_proposer_mode(proposer_mode)
        self._lock = threading.Lock()
        self._jobs: dict[str, UiJob] = {}

    def state(self) -> dict[str, Any]:
        with self._lock:
            jobs = [_job_to_jsonable(job) for job in sorted(self._jobs.values(), key=lambda item: item.created_at)]
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "root": str(self.root),
            "runs_dir": str(self.runs_dir),
            "running_jobs": sum(1 for job in jobs if job["status"] == "running"),
            "jobs": jobs,
            "runs": self.list_runs(),
            "proposer_mode": self.proposer_mode,
            "model": "glm-5.2" if self.proposer_mode == "glm" else None,
            "reproduction_claimed": False,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return runs
        for path in sorted(self.runs_dir.iterdir(), key=lambda item: _mtime(item), reverse=True):
            if not path.is_dir() or not (path / "manifest.json").is_file():
                continue
            runs.append(_run_listing(path))
        return runs

    def run_detail(self, run_id: str) -> dict[str, Any]:
        path = self._run_path(run_id)
        summary = to_jsonable(summarize_audit_run(path))
        trajectory = audit_trajectory_rows(path)
        inspection = to_jsonable(inspect_harness_run(path))
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "id": path.name,
            "path": str(path),
            "summary": summary,
            "trajectory": trajectory,
            "inspection": inspection,
            "token_usage": self._usage_for_run(run_id),
            "reproduction_claimed": False,
        }

    def round_detail(self, run_id: str, round_index: int) -> dict[str, Any]:
        path = self._run_path(run_id)
        round_dir = path / "rounds" / str(round_index)
        if not round_dir.is_dir():
            raise FileNotFoundError(f"round not found: {round_index}")
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "id": path.name,
            "round": round_index,
            "patterns": _read_json_array(round_dir / "patterns.json"),
            "proposals": _read_jsonl(round_dir / "proposals.jsonl"),
            "evaluations": _read_jsonl(round_dir / "evaluations.jsonl"),
            "reproduction_claimed": False,
        }

    def harness_detail(self, run_id: str) -> dict[str, Any]:
        path = self._run_path(run_id)
        inspection = to_jsonable(inspect_harness_run(path))
        initial = _read_json_object(path / "rounds" / "0" / "harness_before.json")
        final = inspection.get("final_harness_surfaces") if isinstance(inspection, dict) else None
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "id": path.name,
            "initial_harness": initial,
            "final_harness": final,
            "inspection": inspection,
            "reproduction_claimed": False,
        }

    def preflight(self) -> dict[str, Any]:
        """Report GLM backend reachability for the console status banner.

        Uses dry-run when no key is configured (no network), and a live check when ZAI_API_KEY is
        present so the console can distinguish 'operational' from 'reachable, needs funding'.
        """

        api_key = os.environ.get("ZAI_API_KEY")
        mode = "live" if api_key else "dry-run"
        try:
            report = evaluate_model_backend_preflight(
                mode=mode,
                backend_ids=["glm"],
                env=os.environ,
            )
            payload = model_backend_preflight_report_to_jsonable(report)
        except (OSError, ModelBackendPreflightError) as exc:
            payload = {"ok": False, "mode": mode, "error": str(exc)}
        checks = payload.get("checks")
        check = checks[0] if isinstance(checks, list) and checks else {}
        detail = check.get("detail", "") if isinstance(check, dict) else ""
        status = _glm_status(mode, bool(payload.get("ok")), str(detail))
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "proposer_mode": self.proposer_mode,
            "model": "glm-5.2",
            "key_present": api_key is not None,
            "mode": mode,
            "status": status,
            "detail": detail,
            "report": payload,
            "reproduction_claimed": False,
        }

    def _usage_for_run(self, run_id: str) -> dict[str, int]:
        with self._lock:
            for job in self._jobs.values():
                if job.run_id == run_id and job.token_usage:
                    return dict(job.token_usage)
        return {}


    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = {
            "rounds": _int_payload(payload, "rounds", default=3, minimum=1, maximum=20),
            "seed": _int_payload(payload, "seed", default=0, minimum=0, maximum=1_000_000),
            "evaluation_repeats": _int_payload(payload, "evaluation_repeats", default=2, minimum=1, maximum=10),
            "max_proposals": _int_payload(payload, "max_proposals", default=8, minimum=1, maximum=64),
            "max_payload_bytes": _int_payload(payload, "max_payload_bytes", default=600, minimum=32, maximum=10_000),
        }
        run_id = _run_id()
        job = UiJob(
            id=str(uuid.uuid4()),
            run_id=run_id,
            path=self.runs_dir / run_id,
            status="queued",
            config=config,
            created_at=_now(),
            events=["queued"],
            proposer_mode=self.proposer_mode,
        )
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(target=self._run_job, args=(job.id,), name=f"self-harness-ui-{run_id}", daemon=True)
        thread.start()
        return _job_to_jsonable(job)

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
            job.events.append("running")
            config = dict(job.config)
            path = job.path
        try:
            engine_config = EngineConfig(
                rounds=config["rounds"],
                seed=config["seed"],
                evaluation_repeats=config["evaluation_repeats"],
                proposal_budget=ProposalBudget(
                    max_proposals=config["max_proposals"],
                    max_payload_bytes=config["max_payload_bytes"],
                ),
            )
            usage_lock = self._lock

            def _accumulate_usage(counts: dict[str, int]) -> None:
                with usage_lock:
                    accumulated = self._jobs[job_id].token_usage
                    for key, value in counts.items():
                        accumulated[key] = accumulated.get(key, 0) + value

            engine = SelfHarnessEngine(
                tasks=demo_tasks(),
                runner=ToyRunner(seed=config["seed"]),
                proposer=_build_proposer(self.proposer_mode, on_usage=_accumulate_usage),
                out_dir=path,
                config=engine_config,
            )
            engine.run()
            summary = to_jsonable(summarize_audit_run(path))
            with self._lock:
                job = self._jobs[job_id]
                job.status = "completed"
                job.ended_at = _now()
                job.summary = summary
                job.events.append("completed")
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.ended_at = _now()
                job.error = str(exc)
                job.events.append("failed")

    def _run_path(self, run_id: str) -> Path:
        if Path(run_id).name != run_id or run_id in {"", ".", ".."}:
            raise ValueError("invalid run id")
        path = (self.runs_dir / run_id).resolve()
        if path.parent != self.runs_dir:
            raise ValueError("run id escapes runs directory")
        if not (path / "manifest.json").is_file():
            raise FileNotFoundError(f"run not found: {run_id}")
        return path


def serve_ui(*, host: str, port: int, root: Path, runs_dir: Path, proposer_mode: str = "heuristic") -> int:
    app = HarnessUiApp(root=root, runs_dir=runs_dir, proposer_mode=proposer_mode)
    server = ThreadingHTTPServer((host, port), _make_handler(app))
    print(f"SelfHarness UI listening on http://{host}:{server.server_port} proposer={app.proposer_mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("SelfHarness UI stopped")
    finally:
        server.server_close()
    return 0


def _make_handler(app: HarnessUiApp) -> type[BaseHTTPRequestHandler]:
    class HarnessUiHandler(BaseHTTPRequestHandler):
        server_version = "SelfHarnessUI/1.0"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self._send_html(_HTML)
                    return
                if parsed.path == "/api/state":
                    self._send_json(app.state())
                    return
                if parsed.path == "/api/preflight":
                    self._send_json(app.preflight())
                    return
                if parsed.path.startswith("/api/runs/"):
                    suffix = parsed.path.removeprefix("/api/runs/").strip("/")
                    segments = suffix.split("/")
                    run_id = segments[0]
                    if len(segments) == 1:
                        self._send_json(app.run_detail(run_id))
                        return
                    if len(segments) == 2 and segments[1] == "harness":
                        self._send_json(app.harness_detail(run_id))
                        return
                    if len(segments) == 3 and segments[1] == "rounds" and segments[2].isdigit():
                        self._send_json(app.round_detail(run_id, int(segments[2])))
                        return
                    self._send_error(HTTPStatus.NOT_FOUND, "unknown route")
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "unknown route")
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/runs":
                    payload = self._read_json()
                    self._send_json(app.start_run(payload), status=HTTPStatus.ACCEPTED)
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "unknown route")
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def log_message(self, format: str, *args: Any) -> None:
            timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            print(f"{timestamp} {self.address_string()} {format % args}")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = (stable_json_dumps(payload) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json(
                {
                    "schema_version": UI_SCHEMA_VERSION,
                    "ok": False,
                    "error": message,
                    "reproduction_claimed": False,
                },
                status=status,
            )

    return HarnessUiHandler


def _resolve_child(root: Path, child: Path) -> Path:
    path = child if child.is_absolute() else root / child
    return path.resolve()


def _normalize_proposer_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"heuristic", "toy"}:
        return "heuristic"
    if normalized in {"glm", "glm-5.2", "zai", "z.ai"}:
        return "glm"
    raise ValueError("proposer_mode must be heuristic or glm")


def _glm_status(mode: str, ok: bool, detail: str) -> str:
    """Classify GLM reachability for the console banner.

    A Z.ai balance/quota error (code 1113) means the endpoint, key, and model are all valid and the
    account merely needs funding — a distinct, actionable state from an unreachable backend.
    """

    if ok:
        return "operational"
    if "1113" in detail or "insufficient balance" in detail.lower() or "recharge" in detail.lower():
        return "needs_funding"
    if mode == "dry-run":
        return "not_checked"
    return "unreachable"


def _read_json_array(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _build_proposer(
    mode: str,
    on_usage: Callable[[dict[str, int]], None] | None = None,
) -> HeuristicProposer | LLMProposer:
    if mode == "heuristic":
        return HeuristicProposer()
    if mode == "glm":
        api_key = os.environ.get("ZAI_API_KEY")
        if not api_key:
            raise RuntimeError("missing ZAI_API_KEY for GLM proposer")
        base_url = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
        transport = UrlLibChatCompletionTransport(base_url=base_url, api_key=api_key)
        return LLMProposer(
            GLMClient(transport=transport, max_tokens=4096, temperature=0.0, on_usage=on_usage)
        )
    raise ValueError(f"unsupported proposer mode: {mode}")


def _run_listing(path: Path) -> dict[str, Any]:
    try:
        summary: dict[str, Any] | None = to_jsonable(summarize_audit_run(path))
        error = None
    except Exception as exc:
        summary = None
        error = str(exc)
    return {
        "id": path.name,
        "path": str(path),
        "updated_at": _timestamp(_mtime(path)),
        "summary": summary,
        "error": error,
        "reproduction_claimed": False,
    }


def _job_to_jsonable(job: UiJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "run_id": job.run_id,
        "path": str(job.path),
        "status": job.status,
        "config": dict(job.config),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
        "error": job.error,
        "summary": job.summary,
        "events": list(job.events),
        "token_usage": dict(job.token_usage),
        "proposer_mode": job.proposer_mode,
    }


def _int_payload(payload: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = payload.get(key, default)
    if isinstance(raw, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"ui-{timestamp}-{uuid.uuid4().hex[:8]}"


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def _now() -> str:
    return _timestamp(time.time())


_HTML = r"""<!doctype html>
<html lang="en" x-data="shConsole()" x-init="init()" :data-theme="theme">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SelfHarness Console</title>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"></script>
  <style>
    :root {
      --bg: #0d1117; --bg-2: #161b22; --panel: #11161d; --panel-2: #1b222c;
      --ink: #e6edf3; --muted: #8b97a4; --line: #232c37; --line-2: #2f3a47;
      --accent: #2dd4bf; --accent-ink: #04201c; --accent-soft: #103a35;
      --ok: #3fb950; --warn: #d29922; --danger: #f85149; --info: #58a6ff;
      --chip: #1f2730; --shadow: 0 10px 30px rgba(0,0,0,.35);
    }
    [data-theme="light"] {
      --bg: #f5f7f6; --bg-2: #ffffff; --panel: #ffffff; --panel-2: #f0f3f2;
      --ink: #16201d; --muted: #5b6670; --line: #dde3e0; --line-2: #cbd3cf;
      --accent: #0d9488; --accent-ink: #ffffff; --accent-soft: #d7efec;
      --ok: #1a7f37; --warn: #9a6700; --danger: #cf222e; --info: #0969da;
      --chip: #eef2f1; --shadow: 0 8px 24px rgba(20,40,35,.10);
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; }
    body {
      background: radial-gradient(1200px 600px at 80% -10%, var(--accent-soft), transparent), var(--bg);
      color: var(--ink); line-height: 1.5;
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    code, pre, .mono { font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace; }
    a { color: var(--info); }
    header {
      position: sticky; top: 0; z-index: 10; backdrop-filter: blur(8px);
      display: flex; align-items: center; gap: 16px; padding: 14px 22px;
      border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--bg) 86%, transparent);
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .logo {
      width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center;
      background: linear-gradient(140deg, var(--accent), #1aa89a); color: var(--accent-ink);
      font-weight: 800; box-shadow: var(--shadow);
    }
    h1 { margin: 0; font-size: 17px; letter-spacing: .2px; }
    .sub { color: var(--muted); font-size: 12px; }
    .spacer { flex: 1; }
    .pill {
      display: inline-flex; align-items: center; gap: 7px; padding: 5px 11px; border-radius: 999px;
      border: 1px solid var(--line-2); background: var(--chip); font-size: 12px; font-weight: 600;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
    .dot.operational { background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 25%, transparent); }
    .dot.needs_funding { background: var(--warn); box-shadow: 0 0 0 3px color-mix(in srgb, var(--warn) 25%, transparent); }
    .dot.unreachable { background: var(--danger); box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) 25%, transparent); }
    .dot.not_checked { background: var(--muted); }
    button, input, select {
      font: inherit; color: var(--ink); border: 1px solid var(--line-2); border-radius: 9px;
      background: var(--bg-2); padding: 8px 11px;
    }
    button { cursor: pointer; transition: transform .04s ease, border-color .15s ease, background .15s ease; }
    button:hover { border-color: var(--accent); }
    button:active { transform: translateY(1px); }
    button:disabled { opacity: .5; cursor: not-allowed; }
    button.primary { background: linear-gradient(140deg, var(--accent), #1aa89a); color: var(--accent-ink); border: none; font-weight: 700; }
    button.ghost { background: transparent; }
    button.tiny { padding: 4px 9px; font-size: 12px; border-radius: 7px; }
    main { display: grid; grid-template-columns: 380px minmax(0,1fr); gap: 18px; padding: 18px 22px; align-items: start; }
    @media (max-width: 1000px) { main { grid-template-columns: 1fr; } }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); }
    .card > .card-h { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 10px; }
    .card > .card-h h2 { margin: 0; font-size: 14px; letter-spacing: .3px; }
    .card > .card-b { padding: 16px; }
    .field { margin-bottom: 12px; }
    .field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; font-weight: 600; }
    .field input, .field select { width: 100%; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .seg { display: inline-flex; border: 1px solid var(--line-2); border-radius: 9px; overflow: hidden; }
    .seg button { border: none; border-radius: 0; background: transparent; padding: 8px 13px; }
    .seg button.on { background: var(--accent-soft); color: var(--ink); font-weight: 700; }
    .runs { display: flex; flex-direction: column; gap: 8px; max-height: 46vh; overflow: auto; }
    .run-row { display: flex; align-items: center; gap: 10px; padding: 10px 11px; border: 1px solid var(--line); border-radius: 10px; cursor: pointer; background: var(--bg-2); }
    .run-row:hover { border-color: var(--accent); }
    .run-row.sel { border-color: var(--accent); background: var(--accent-soft); }
    .run-row .id { font-weight: 700; font-size: 13px; }
    .muted { color: var(--muted); }
    .tabs { display: flex; gap: 6px; flex-wrap: wrap; }
    .tabs button { border-radius: 999px; padding: 7px 14px; font-size: 13px; font-weight: 600; background: var(--chip); border: 1px solid transparent; }
    .tabs button.on { background: var(--accent-soft); border-color: var(--accent); }
    .scores { display: flex; gap: 12px; flex-wrap: wrap; }
    .stat { flex: 1; min-width: 120px; background: var(--bg-2); border: 1px solid var(--line); border-radius: 11px; padding: 12px 14px; }
    .stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
    .stat .v { font-size: 22px; font-weight: 800; margin-top: 3px; }
    .delta-up { color: var(--ok); } .delta-flat { color: var(--muted); } .delta-down { color: var(--danger); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; font-size: 12px; }
    .badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; }
    .badge.accepted, .badge.merged { background: color-mix(in srgb, var(--ok) 22%, transparent); color: var(--ok); }
    .badge.rejected, .badge.invalid { background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }
    .badge.superseded { background: var(--chip); color: var(--muted); }
    .traj { display: flex; flex-direction: column; gap: 10px; }
    .traj .row { display: grid; grid-template-columns: 56px 1fr auto; gap: 12px; align-items: center; padding: 11px 12px; border: 1px solid var(--line); border-radius: 11px; background: var(--bg-2); cursor: pointer; }
    .traj .row:hover { border-color: var(--accent); }
    .traj .rnum { font-weight: 800; font-size: 15px; color: var(--accent); }
    .bar { height: 8px; border-radius: 6px; background: var(--line); overflow: hidden; }
    .bar > span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #1aa89a); }
    .surface { border: 1px solid var(--line); border-radius: 11px; margin-bottom: 12px; overflow: hidden; }
    .surface .sh { padding: 10px 13px; background: var(--panel-2); font-weight: 700; font-size: 13px; display: flex; justify-content: space-between; align-items: center; }
    .surface .sb { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line); }
    .surface .col { background: var(--panel); padding: 11px 13px; }
    .surface .col .lab { font-size: 11px; color: var(--muted); text-transform: uppercase; margin-bottom: 5px; }
    .surface.changed .sh { color: var(--accent); }
    pre.box { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 12.5px; }
    .pattern { border: 1px solid var(--line); border-radius: 11px; padding: 12px 13px; margin-bottom: 10px; background: var(--bg-2); }
    .pattern .sig { font-size: 12px; color: var(--muted); }
    .kchips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
    .kchip { font-size: 11px; background: var(--chip); border: 1px solid var(--line); border-radius: 6px; padding: 2px 7px; }
    .prop { border: 1px solid var(--line); border-radius: 11px; padding: 13px; margin-bottom: 11px; background: var(--bg-2); }
    .prop .top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
    .prop .surf { font-weight: 700; }
    .prop .meta { color: var(--muted); font-size: 12px; }
    .risks { margin: 8px 0 0; padding-left: 18px; }
    .empty { text-align: center; color: var(--muted); padding: 28px; }
    .banner { display: flex; gap: 12px; align-items: center; padding: 12px 14px; border-radius: 11px; border: 1px solid var(--line-2); margin-bottom: 14px; background: var(--bg-2); }
    .banner.needs_funding { border-color: var(--warn); background: color-mix(in srgb, var(--warn) 12%, var(--bg-2)); }
    .banner.operational { border-color: var(--ok); background: color-mix(in srgb, var(--ok) 12%, var(--bg-2)); }
    .footer-note { color: var(--muted); font-size: 12px; padding: 8px 22px 26px; }
    .toast { position: fixed; right: 18px; bottom: 18px; background: var(--panel); border: 1px solid var(--line-2); border-radius: 11px; padding: 12px 15px; box-shadow: var(--shadow); max-width: 360px; }
    .nojs { padding: 22px; }
    [x-cloak] { display: none !important; }
  </style>
</head>
<body>
  <noscript><div class="nojs">The SelfHarness console requires JavaScript. The JSON API is available at <code>/api/state</code>, <code>/api/preflight</code>, and <code>/api/runs/&lt;id&gt;</code>.</div></noscript>

  <header x-cloak>
    <div class="brand">
      <div class="logo">SH</div>
      <div>
        <h1>SelfHarness Console</h1>
        <div class="sub">propose · validate · promote — auditable harness improvement</div>
      </div>
    </div>
    <div class="spacer"></div>
    <span class="pill" :title="glm.detail || ''">
      <span class="dot" :class="glm.status"></span>
      <span x-text="glmLabel()"></span>
    </span>
    <button class="ghost tiny" @click="toggleTheme()" x-text="theme === 'dark' ? '☀ Light' : '☾ Dark'"></button>
    <button class="ghost tiny" @click="refresh()">↻ Refresh</button>
  </header>

  <main x-cloak>
    <!-- LEFT: launcher + runs -->
    <div style="display:flex; flex-direction:column; gap:18px;">
      <section class="card">
        <div class="card-h"><h2>New run</h2></div>
        <div class="card-b">
          <div class="field">
            <label>Proposer backend</label>
            <div class="seg">
              <button :class="{on: form.proposer==='heuristic'}" @click="form.proposer='heuristic'">Heuristic (toy)</button>
              <button :class="{on: form.proposer==='glm'}" @click="form.proposer='glm'" :disabled="!glm.key_present" :title="glm.key_present ? '' : 'Set ZAI_API_KEY and restart with --proposer glm'">GLM 5.2</button>
            </div>
            <div class="muted" style="font-size:12px; margin-top:6px;"
                 x-show="form.proposer==='glm'"
                 x-text="glm.status==='operational' ? 'GLM 5.2 live and funded.' : (glm.status==='needs_funding' ? 'GLM 5.2 reachable — Z.ai account needs funding (code 1113).' : 'GLM proposer selected.')"></div>
            <div class="muted" style="font-size:12px; margin-top:6px;" x-show="serverProposer !== form.proposer && form.proposer==='glm'">
              Note: this server was started with proposer=<b x-text="serverProposer"></b>. Runs use the server's backend; restart with <code>--proposer glm</code> to launch GLM runs.
            </div>
          </div>
          <div class="grid2">
            <div class="field"><label>Rounds</label><input type="number" min="1" max="20" x-model.number="form.rounds"></div>
            <div class="field"><label>Seed</label><input type="number" min="0" x-model.number="form.seed"></div>
            <div class="field"><label>Eval repeats</label><input type="number" min="1" max="10" x-model.number="form.evaluation_repeats"></div>
            <div class="field"><label>Max proposals</label><input type="number" min="1" max="64" x-model.number="form.max_proposals"></div>
          </div>
          <div class="field"><label>Max payload bytes</label><input type="number" min="32" max="10000" x-model.number="form.max_payload_bytes"></div>
          <button class="primary" style="width:100%" @click="startRun()" :disabled="starting" x-text="starting ? 'Starting…' : 'Start run'"></button>
        </div>
      </section>

      <section class="card">
        <div class="card-h"><h2>Runs</h2><span class="spacer"></span><span class="muted" x-text="runs.length + ' total'"></span></div>
        <div class="card-b">
          <div class="runs">
            <template x-for="r in runs" :key="r.id">
              <div class="run-row" :class="{sel: selectedId===r.id}" @click="select(r.id)">
                <div style="flex:1">
                  <div class="id mono" x-text="r.id"></div>
                  <div class="muted" style="font-size:12px" x-text="runScore(r)"></div>
                </div>
                <span class="badge" :class="r.error ? 'rejected' : 'accepted'" x-text="r.error ? 'error' : 'audit'"></span>
              </div>
            </template>
            <div class="empty" x-show="runs.length===0">No runs yet. Start one above.</div>
          </div>
          <div x-show="jobs.length" style="margin-top:12px;">
            <div class="muted" style="font-size:12px; margin-bottom:6px;">Active jobs</div>
            <template x-for="j in jobs" :key="j.id">
              <div class="run-row" style="cursor:default">
                <div style="flex:1"><div class="id mono" x-text="j.run_id"></div><div class="muted" style="font-size:12px" x-text="j.status + (j.error ? (' — ' + j.error) : '')"></div></div>
                <span class="badge" :class="j.status==='failed' ? 'rejected' : (j.status==='completed' ? 'accepted' : 'superseded')" x-text="j.status"></span>
              </div>
            </template>
          </div>
        </div>
      </section>
    </div>

    <!-- RIGHT: detail -->
    <section class="card" style="min-height: 60vh;">
      <div class="card-h">
        <h2 x-text="selectedId ? ('Run ' + selectedId) : 'Run detail'"></h2>
        <span class="spacer"></span>
        <div class="tabs" x-show="selectedId">
          <button :class="{on: tab==='overview'}" @click="tab='overview'">Overview</button>
          <button :class="{on: tab==='trajectory'}" @click="tab='trajectory'">Trajectory</button>
          <button :class="{on: tab==='round'}" @click="tab='round'" x-show="roundData">Round <span x-text="roundData ? roundData.round : ''"></span></button>
          <button :class="{on: tab==='harness'}" @click="loadHarness()">Harness diff</button>
          <button :class="{on: tab==='raw'}" @click="tab='raw'">Raw</button>
        </div>
      </div>
      <div class="card-b">
        <div class="banner" :class="glm.status" x-show="form.proposer==='glm' || glm.status==='needs_funding'">
          <span class="dot" :class="glm.status"></span>
          <div>
            <div style="font-weight:700" x-text="glmLabel()"></div>
            <div class="muted" style="font-size:12px" x-text="glm.detail || ('GLM 5.2 via ' + (glm.mode||'preflight'))"></div>
          </div>
        </div>

        <div class="empty" x-show="!selectedId">Select a run to inspect its trajectory, proposals, evidence, and harness diff.</div>

        <!-- Overview -->
        <div x-show="selectedId && tab==='overview'">
          <template x-if="detail">
            <div>
              <div class="scores">
                <div class="stat"><div class="k">Held-in (final)</div><div class="v" x-text="pct(detail.summary.final_held_in_score)"></div></div>
                <div class="stat"><div class="k">Held-out (final)</div><div class="v" x-text="pct(detail.summary.final_held_out_score)"></div></div>
                <div class="stat"><div class="k">Rounds</div><div class="v" x-text="detail.summary.rounds"></div></div>
                <div class="stat"><div class="k">Accepted / Rejected</div><div class="v"><span class="delta-up" x-text="detail.summary.accepted_count"></span> / <span class="delta-down" x-text="detail.summary.rejected_count"></span></div></div>
              </div>
              <div class="scores" style="margin-top:12px" x-show="hasUsage()">
                <div class="stat"><div class="k">GLM input tokens</div><div class="v" x-text="(detail.token_usage.input_tokens||0).toLocaleString()"></div></div>
                <div class="stat"><div class="k">GLM output tokens</div><div class="v" x-text="(detail.token_usage.output_tokens||0).toLocaleString()"></div></div>
                <div class="stat"><div class="k">GLM total tokens</div><div class="v" x-text="(detail.token_usage.total_tokens||0).toLocaleString()"></div></div>
              </div>
              <p class="muted" style="font-size:12px; margin-top:14px;">Protocol <code x-text="detail.summary.protocol_version"></code> · schema <code x-text="detail.summary.schema_version"></code> · not benchmark reproduction evidence.</p>
            </div>
          </template>
        </div>

        <!-- Trajectory -->
        <div x-show="selectedId && tab==='trajectory'">
          <div class="traj">
            <template x-for="row in (detail ? detail.trajectory : [])" :key="row.round">
              <div class="row" @click="loadRound(row.round)">
                <div class="rnum" x-text="'R' + row.round"></div>
                <div>
                  <div style="display:flex; gap:14px; font-size:13px; margin-bottom:6px;">
                    <span>held-in <b x-text="row.after_held_in_passed"></b> <span class="muted" x-text="'(' + deltaTxt(row.after_held_in_passed - row.baseline_held_in_passed) + ')'"></span></span>
                    <span>held-out <b x-text="row.after_held_out_passed"></b> <span class="muted" x-text="'(' + deltaTxt(row.after_held_out_passed - row.baseline_held_out_passed) + ')'"></span></span>
                    <span class="muted" x-text="(row.proposals ? row.proposals.length : 0) + ' proposals'"></span>
                  </div>
                  <div class="bar"><span :style="'width:' + barPct(row) + '%'"></span></div>
                </div>
                <span class="badge" :class="row.merged ? 'merged' : (acceptedIn(row) ? 'accepted' : 'superseded')" x-text="row.merged ? 'merged' : (acceptedIn(row) ? 'accepted' : 'carry')"></span>
              </div>
            </template>
          </div>
        </div>

        <!-- Round drill-down -->
        <div x-show="selectedId && tab==='round'">
          <template x-if="roundData">
            <div>
              <h3 style="margin:4px 0 10px; font-size:14px;">Mined failure patterns (evidence bundle B<sub x-text="roundData.round"></sub>)</h3>
              <template x-for="p in roundData.patterns" :key="p.id">
                <div class="pattern">
                  <div style="display:flex; justify-content:space-between;">
                    <b class="mono" x-text="p.signature.mechanism"></b>
                    <span class="muted" x-text="'support ' + p.support"></span>
                  </div>
                  <div class="sig" x-text="'φ = (' + p.signature.terminal_cause + ', ' + p.signature.causal_status + ', ' + p.signature.mechanism + ')'"></div>
                  <div class="kchips">
                    <template x-for="t in p.task_ids"><span class="kchip mono" x-text="t"></span></template>
                  </div>
                </div>
              </template>
              <div class="empty" x-show="roundData.patterns.length===0">No held-in failures mined this round.</div>

              <h3 style="margin:18px 0 10px; font-size:14px;">Proposals</h3>
              <template x-for="p in roundData.proposals" :key="p.id">
                <div class="prop">
                  <div class="top">
                    <div>
                      <span class="surf mono" x-text="p.surface"></span>
                      <span class="meta" x-text="' · ' + p.op + ' · priority ' + p.priority"></span>
                    </div>
                    <span class="badge" :class="p.status" x-text="p.status"></span>
                  </div>
                  <div style="margin-top:8px; font-size:13px;" x-text="p.rationale"></div>
                  <div class="meta" style="margin-top:6px;" x-text="'Expected: ' + p.expected_effect"></div>
                  <div style="margin-top:8px; display:flex; gap:14px; font-size:12px;">
                    <span class="muted">held-in <b x-text="p.passed_held_in"></b> vs <span x-text="p.baseline_passed_held_in"></span></span>
                    <span class="muted">held-out <b x-text="p.passed_held_out"></b> vs <span x-text="p.baseline_passed_held_out"></span></span>
                  </div>
                  <ul class="risks" x-show="p.regression_risks && p.regression_risks.length">
                    <template x-for="r in p.regression_risks"><li class="muted" style="font-size:12px" x-text="r"></li></template>
                  </ul>
                  <div class="meta" style="margin-top:6px;" x-show="p.rejection_reason" x-text="'Decision: ' + (p.decision_reason || p.rejection_reason)"></div>
                </div>
              </template>
            </div>
          </template>
        </div>

        <!-- Harness diff -->
        <div x-show="selectedId && tab==='harness'">
          <template x-if="harnessData">
            <div>
              <p class="muted" style="font-size:12px; margin-top:0;">Initial (Figure 3) → final promoted harness. Changed surfaces are highlighted.</p>
              <template x-for="name in surfaceNames()" :key="name">
                <div class="surface" :class="{changed: surfaceChanged(name)}">
                  <div class="sh"><span x-text="name"></span><span class="badge" :class="surfaceChanged(name)?'accepted':'superseded'" x-text="surfaceChanged(name)?'changed':'unchanged'"></span></div>
                  <div class="sb">
                    <div class="col"><div class="lab">initial</div><pre class="box" x-text="fmt(harnessData.initial_harness[name])"></pre></div>
                    <div class="col"><div class="lab">final</div><pre class="box" x-text="fmt(finalSurface(name))"></pre></div>
                  </div>
                </div>
              </template>
            </div>
          </template>
        </div>

        <!-- Raw -->
        <div x-show="selectedId && tab==='raw'">
          <pre class="box mono" style="font-size:12px" x-text="JSON.stringify(detail, null, 2)"></pre>
        </div>
      </div>
    </section>
  </main>

  <div class="footer-note" x-cloak>SelfHarness is a paper-faithful toy implementation (arXiv:2606.09498). It does not claim Terminal-Bench reproduction. Model: <b>GLM 5.2</b> via Z.ai when configured.</div>

  <div class="toast" x-show="toast" x-transition x-cloak x-text="toast"></div>

  <script>
    function shConsole() {
      return {
        theme: localStorage.getItem('sh-theme') || 'dark',
        glm: { status: 'not_checked', detail: '', key_present: false, mode: 'dry-run' },
        serverProposer: 'heuristic',
        runs: [], jobs: [], selectedId: null, detail: null, roundData: null, harnessData: null,
        tab: 'overview', starting: false, toast: '',
        form: { proposer: 'heuristic', rounds: 3, seed: 0, evaluation_repeats: 2, max_proposals: 8, max_payload_bytes: 600 },

        async init() {
          await this.loadPreflight();
          await this.refresh();
          setInterval(() => this.refresh(), 2500);
        },
        async api(path, opts) {
          const res = await fetch(path, opts);
          const text = await res.text();
          const data = text ? JSON.parse(text) : {};
          if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
          return data;
        },
        async loadPreflight() {
          try {
            this.glm = await this.api('/api/preflight');
          } catch (e) { this.glm = { status: 'unreachable', detail: String(e), key_present: false }; }
        },
        async refresh() {
          try {
            const state = await this.api('/api/state');
            this.runs = state.runs || [];
            this.jobs = (state.jobs || []).filter(j => j.status === 'running' || j.status === 'queued' || j.status === 'failed');
            this.serverProposer = state.proposer_mode || 'heuristic';
            if (!this.selectedId && this.runs.length) this.select(this.runs[0].id);
            if (this.selectedId && this.detail) await this.loadDetail(this.selectedId, true);
          } catch (e) { this.flash('refresh failed: ' + e.message); }
        },
        async select(id) {
          this.selectedId = id; this.tab = 'overview'; this.roundData = null; this.harnessData = null;
          await this.loadDetail(id);
        },
        async loadDetail(id, quiet) {
          try { this.detail = await this.api('/api/runs/' + encodeURIComponent(id)); }
          catch (e) { if (!quiet) this.flash('load failed: ' + e.message); }
        },
        async loadRound(n) {
          try { this.roundData = await this.api('/api/runs/' + encodeURIComponent(this.selectedId) + '/rounds/' + n); this.tab = 'round'; }
          catch (e) { this.flash('round load failed: ' + e.message); }
        },
        async loadHarness() {
          this.tab = 'harness';
          try { this.harnessData = await this.api('/api/runs/' + encodeURIComponent(this.selectedId) + '/harness'); }
          catch (e) { this.flash('harness load failed: ' + e.message); }
        },
        async startRun() {
          this.starting = true;
          try {
            const body = { rounds: this.form.rounds, seed: this.form.seed, evaluation_repeats: this.form.evaluation_repeats, max_proposals: this.form.max_proposals, max_payload_bytes: this.form.max_payload_bytes };
            const job = await this.api('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            this.flash('Run ' + job.run_id + ' started (' + this.serverProposer + ')');
            setTimeout(() => this.refresh(), 600);
          } catch (e) { this.flash('start failed: ' + e.message); }
          finally { this.starting = false; }
        },
        toggleTheme() { this.theme = this.theme === 'dark' ? 'light' : 'dark'; localStorage.setItem('sh-theme', this.theme); },
        flash(msg) { this.toast = msg; setTimeout(() => { if (this.toast === msg) this.toast = ''; }, 4000); },
        glmLabel() {
          return { operational: 'GLM 5.2 · operational', needs_funding: 'GLM 5.2 · needs funding', unreachable: 'GLM 5.2 · unreachable', not_checked: 'GLM 5.2 · ' + (this.glm.key_present ? 'idle' : 'no key') }[this.glm.status] || 'GLM 5.2';
        },
        pct(x) { return (x == null) ? '—' : (Math.round(x * 1000) / 10) + '%'; },
        deltaTxt(d) { return d > 0 ? '+' + d : '' + d; },
        runScore(r) {
          const s = r.summary || {};
          if (s.final_held_in_score == null) return r.error ? 'audit error' : 'pending';
          return 'in ' + this.pct(s.final_held_in_score) + ' · out ' + this.pct(s.final_held_out_score);
        },
        barPct(row) { const t = (row.after_held_in_passed||0) + (row.after_held_out_passed||0); return Math.min(100, t * 8); },
        acceptedIn(row) { return (row.after_held_in_passed > row.baseline_held_in_passed) || (row.after_held_out_passed > row.baseline_held_out_passed); },
        hasUsage() { return this.detail && this.detail.token_usage && Object.keys(this.detail.token_usage).length > 0; },
        surfaceNames() { return this.harnessData ? Object.keys(this.harnessData.initial_harness) : []; },
        finalSurface(name) {
          const f = this.harnessData.final_harness;
          if (f && typeof f === 'object' && name in f) return f[name];
          return this.harnessData.initial_harness[name];
        },
        surfaceChanged(name) { return this.fmt(this.harnessData.initial_harness[name]) !== this.fmt(this.finalSurface(name)); },
        fmt(v) { return (typeof v === 'string') ? v : JSON.stringify(v, null, 2); },
      };
    }
  </script>
</body>
</html>
"""
