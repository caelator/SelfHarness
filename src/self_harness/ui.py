from __future__ import annotations

import json
import os
import threading
import time
import uuid
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
from self_harness.model_backend_preflight import UrlLibChatCompletionTransport
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
            "reproduction_claimed": False,
        }

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
            engine = SelfHarnessEngine(
                tasks=demo_tasks(),
                runner=ToyRunner(seed=config["seed"]),
                proposer=_build_proposer(self.proposer_mode),
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
                if parsed.path.startswith("/api/runs/"):
                    run_id = parsed.path.removeprefix("/api/runs/").strip("/")
                    if "/" in run_id:
                        self._send_error(HTTPStatus.NOT_FOUND, "unknown route")
                        return
                    self._send_json(app.run_detail(run_id))
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


def _build_proposer(mode: str) -> HeuristicProposer | LLMProposer:
    if mode == "heuristic":
        return HeuristicProposer()
    if mode == "glm":
        api_key = os.environ.get("ZAI_API_KEY")
        if not api_key:
            raise RuntimeError("missing ZAI_API_KEY for GLM proposer")
        base_url = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
        transport = UrlLibChatCompletionTransport(base_url=base_url, api_key=api_key)
        return LLMProposer(GLMClient(transport=transport, max_tokens=4096, temperature=0.0))
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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SelfHarness Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --ink: #161a1d;
      --muted: #606b74;
      --line: #d8ddd6;
      --panel: #ffffff;
      --accent: #0f6b63;
      --accent-2: #8a4b12;
      --danger: #a12622;
      --ok: #1f7a3a;
      --soft: #eef3f1;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: #fff;
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: 0; }
    h2 { margin: 0 0 12px; font-size: 15px; letter-spacing: 0; }
    button, input {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
    }
    button {
      cursor: pointer;
      padding: 8px 12px;
      min-height: 36px;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-weight: 700;
    }
    button:disabled { cursor: not-allowed; opacity: 0.6; }
    input { width: 100%; padding: 7px 9px; }
    main {
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      gap: 18px;
      padding: 18px;
      max-width: 1320px;
      margin: 0 auto;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }
    aside { overflow: hidden; }
    .section-body { padding: 14px; }
    .toolbar { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 13px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    .run-list { display: grid; gap: 0; border-top: 1px solid var(--line); }
    .run-row {
      display: grid;
      gap: 4px;
      padding: 11px 14px;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
      background: #fff;
    }
    .run-row:hover, .run-row.selected { background: var(--soft); }
    .run-title { display: flex; justify-content: space-between; gap: 8px; font-weight: 700; font-size: 13px; }
    .run-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .status {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      white-space: nowrap;
    }
    .status.ok { color: var(--ok); border-color: #acd4b5; background: #f1f8f2; }
    .status.warn { color: var(--accent-2); border-color: #dfc39f; background: #fbf5ec; }
    .status.bad { color: var(--danger); border-color: #e6aaa7; background: #fff0ef; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      min-width: 0;
    }
    .metric b { display: block; font-size: 22px; }
    .metric span { color: var(--muted); font-size: 12px; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      text-align: left;
      padding: 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 700; background: #fafaf8; }
    pre {
      margin: 0;
      padding: 12px;
      background: #111820;
      color: #f4f7f8;
      border-radius: 8px;
      overflow: auto;
      font-size: 12px;
      max-height: 360px;
    }
    .tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--line); padding: 8px 10px 0; }
    .tabs button { border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
    .tabs button.active { background: var(--soft); border-bottom-color: var(--soft); }
    .tab-panel { display: none; padding: 14px; }
    .tab-panel.active { display: block; }
    .empty { color: var(--muted); padding: 14px; }
    @media (max-width: 920px) {
      main { grid-template-columns: 1fr; padding: 12px; }
      header { align-items: flex-start; flex-direction: column; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <h1>SelfHarness Console</h1>
    <div class="toolbar">
      <span id="root"></span>
      <span id="model" class="status">model: heuristic</span>
      <span id="claim" class="status warn">reproduction claim: false</span>
      <button id="refresh" title="Refresh state">Refresh</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="section-body">
        <h2>Run Harness</h2>
        <div class="grid">
          <label>Rounds <input id="rounds" type="number" min="1" max="20" value="3"></label>
          <label>Repeats <input id="repeats" type="number" min="1" max="10" value="2"></label>
          <label>Seed <input id="seed" type="number" min="0" value="0"></label>
          <label>Proposals <input id="proposals" type="number" min="1" max="64" value="8"></label>
        </div>
        <div style="display:flex; gap:8px; margin-top:12px;">
          <button id="start" class="primary">Run</button>
          <span id="jobStatus" class="status">idle</span>
        </div>
      </div>
      <h2 style="padding:0 14px 10px; margin:0;">Runs</h2>
      <div id="runs" class="run-list"><div class="empty">No runs yet.</div></div>
    </aside>
    <section>
      <div class="tabs">
        <button data-tab="summary" class="active">Summary</button>
        <button data-tab="trajectory">Trajectory</button>
        <button data-tab="harness">Harness</button>
        <button data-tab="raw">Raw</button>
      </div>
      <div id="summary" class="tab-panel active">
        <div class="metrics" id="metrics"></div>
      </div>
      <div id="trajectory" class="tab-panel"></div>
      <div id="harness" class="tab-panel"></div>
      <div id="raw" class="tab-panel"><pre id="rawJson">{}</pre></div>
    </section>
  </main>
  <script>
    let state = null;
    let selectedRun = null;
    let selectedDetail = null;

    const $ = (id) => document.getElementById(id);

    function text(value) {
      return value === null || value === undefined ? "" : String(value);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: {"Content-Type": "application/json"},
        ...options
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    }

    async function refresh() {
      state = await api("/api/state");
      $("root").textContent = state.root;
      $("model").textContent = state.proposer_mode === "glm" ? "model: glm-5.2" : "model: heuristic";
      $("claim").textContent = "reproduction claim: " + state.reproduction_claimed;
      renderRuns();
      const active = state.jobs.find((job) => job.status === "running" || job.status === "queued");
      $("jobStatus").textContent = active ? active.status : "idle";
      $("jobStatus").className = "status " + (active ? "warn" : "");
      if (!selectedRun && state.runs.length) await selectRun(state.runs[0].id);
      if (selectedRun && !state.runs.find((run) => run.id === selectedRun)) selectedRun = null;
    }

    function renderRuns() {
      const container = $("runs");
      container.innerHTML = "";
      if (!state.runs.length) {
        container.innerHTML = '<div class="empty">No runs yet.</div>';
        return;
      }
      for (const run of state.runs) {
        const summary = run.summary || {};
        const row = document.createElement("div");
        row.className = "run-row" + (run.id === selectedRun ? " selected" : "");
        row.onclick = () => selectRun(run.id);
        row.innerHTML = `
          <div class="run-title">
            <span>${run.id}</span>
            <span class="status ok">${text(summary.rounds || "?")} rounds</span>
          </div>
          <div class="run-meta">
            held-in ${text(summary.final_held_in_score)} / held-out ${text(summary.final_held_out_score)}
          </div>
          <div class="run-meta">${run.updated_at}</div>
        `;
        container.appendChild(row);
      }
    }

    async function selectRun(id) {
      selectedRun = id;
      selectedDetail = await api("/api/runs/" + encodeURIComponent(id));
      renderRuns();
      renderDetail();
    }

    function renderDetail() {
      const detail = selectedDetail;
      if (!detail) return;
      const summary = detail.summary || {};
      $("metrics").innerHTML = [
        metric("Held-in", summary.final_held_in_score),
        metric("Held-out", summary.final_held_out_score),
        metric("Accepted", summary.accepted_count),
        metric("Rejected", summary.rejected_count)
      ].join("");
      renderTrajectory(detail.trajectory || []);
      renderHarness(detail.inspection || {});
      $("rawJson").textContent = JSON.stringify(detail, null, 2);
    }

    function metric(label, value) {
      return `<div class="metric"><b>${text(value)}</b><span>${label}</span></div>`;
    }

    function renderTrajectory(rows) {
      if (!rows.length) {
        $("trajectory").innerHTML = '<div class="empty">No trajectory rows.</div>';
        return;
      }
      const body = rows.map((row) => `
        <tr>
          <td>${row.round}</td>
          <td>${row.baseline_held_in_passed} -> ${row.after_held_in_passed}</td>
          <td>${row.baseline_held_out_passed} -> ${row.after_held_out_passed}</td>
          <td>${(row.proposals || []).length}</td>
          <td>${row.merged}</td>
        </tr>
      `).join("");
      $("trajectory").innerHTML = `
        <table>
          <thead><tr><th>Round</th><th>Held-in</th><th>Held-out</th><th>Proposals</th><th>Merged</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      `;
    }

    function renderHarness(inspection) {
      const surfaces = inspection.final_harness_surfaces || {};
      const rows = Object.keys(surfaces).sort().map((name) => `
        <tr><td>${name}</td><td><pre>${JSON.stringify(surfaces[name], null, 2)}</pre></td></tr>
      `).join("");
      $("harness").innerHTML = rows ? `
        <div class="metrics" style="margin-bottom:12px;">
          ${metric("Retained Ops", inspection.retained_ops_count)}
          ${metric("Surfaces", (inspection.retained_changed_surfaces || []).length)}
          ${metric("Schema", inspection.schema_version)}
          ${metric("Audit", inspection.audit_schema_version)}
        </div>
        <table><thead><tr><th>Surface</th><th>Value</th></tr></thead><tbody>${rows}</tbody></table>
      ` : '<div class="empty">No harness inspection.</div>';
    }

    $("start").onclick = async () => {
      $("start").disabled = true;
      try {
        await api("/api/runs", {
          method: "POST",
          body: JSON.stringify({
            rounds: $("rounds").value,
            evaluation_repeats: $("repeats").value,
            seed: $("seed").value,
            max_proposals: $("proposals").value
          })
        });
        await refresh();
      } catch (err) {
        $("jobStatus").textContent = err.message;
        $("jobStatus").className = "status bad";
      } finally {
        $("start").disabled = false;
      }
    };

    $("refresh").onclick = refresh;
    for (const button of document.querySelectorAll(".tabs button")) {
      button.onclick = () => {
        for (const item of document.querySelectorAll(".tabs button, .tab-panel")) item.classList.remove("active");
        button.classList.add("active");
        $(button.dataset.tab).classList.add("active");
      };
    }
    refresh();
    setInterval(refresh, 2500);
  </script>
</body>
</html>
"""
