from __future__ import annotations

import json
import os
import shutil
import tempfile
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
from self_harness.adapters.terminal_bench.agent_render import render_system_prompt
from self_harness.audit import (
    audit_trajectory_rows,
    inspect_harness_run,
    summarize_audit_run,
)
from self_harness.config import EngineConfig
from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.exceptions import InvalidPatchError
from self_harness.harness import (
    INITIAL_HARNESS_END_MARKER,
    INITIAL_HARNESS_START_MARKER,
    dump_harness_spec,
    harness_hash,
    load_harness_spec,
    render_initial_harness_source,
)
from self_harness.llm_proposer import LLMProposer
from self_harness.model_backend_preflight import (
    ModelBackendPreflightError,
    build_zai_transport,
    evaluate_model_backend_preflight,
    model_backend_preflight_report_to_jsonable,
)
from self_harness.proposer import HeuristicProposer
from self_harness.types import HarnessSpec, ProposalBudget, stable_json_dumps, to_jsonable, write_stable_json

UI_SCHEMA_VERSION = "1.0"

_STATIC_DIR = Path(__file__).resolve().parent / "static"
# Allowlisted vendored assets served at /static/<name>. Keeping this explicit means the static route
# never resolves arbitrary filesystem paths.
_STATIC_ASSETS = {
    "alpine-3.14.1.min.js": "text/javascript; charset=utf-8",
}

_CHAT_SYSTEM_PROMPT = (
    "You are GLM 5.2 operating inside the SelfHarness console as a development assistant. "
    "Be concise and concrete. When asked to write or change code, produce minimal, correct edits."
)

# Directory names skipped when copying this repo into a dev-task workspace: large, regenerable, or
# secret. Keeps "use this repo as the workspace" fast and avoids leaking the venv / credentials.
_REPO_COPY_SKIP = {
    ".git",
    ".venv",
    "runs",
    "var",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}


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
    run_mode: str = "deterministic"
    source_promotion: dict[str, Any] | None = None


class HarnessUiApp:
    def __init__(
        self,
        *,
        root: Path,
        runs_dir: Path,
        proposer_mode: str = "heuristic",
        harness_state: Path | None = None,
        max_steps: int = 12,
        tool_timeout_seconds: int = 30,
        codex_binary: str = "codex",
        auto_promote_to_source: bool = True,
    ) -> None:
        self.root = root.resolve()
        self.runs_dir = _resolve_child(self.root, runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.proposer_mode = _normalize_proposer_mode(proposer_mode)
        # Persisted/evolving harness lineage: promoted edits are written here and auto-loaded as the
        # starting harness for the next run, so the harness genuinely evolves across runs and sessions.
        self.harness_state = (
            _resolve_child(self.root, harness_state)
            if harness_state is not None
            else self.runs_dir / "harness_state.json"
        )
        self.max_steps = max_steps
        self.tool_timeout_seconds = tool_timeout_seconds
        self.codex_binary = codex_binary
        # When the acceptance gate (the reviewer: Δin≥0 ∧ Δho≥0 ∧ max>0) promotes an edit, integrate it
        # straight into harness.py — no separate manual approval. The correctness gate (ruff/mypy/import
        # round-trip) still runs and auto-restores on failure, so source is never left broken.
        self.auto_promote_to_source = auto_promote_to_source
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
            "harness_state": self._harness_state_status(),
            "auto_promote_to_source": self.auto_promote_to_source,
            "reproduction_claimed": False,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return runs
        # Map run_id -> live job status so a run still being produced reads as "running", not "error"
        # (its lineage.json doesn't exist yet, which would otherwise look like an audit failure).
        with self._lock:
            job_status = {job.run_id: job.status for job in self._jobs.values()}
        for path in sorted(self.runs_dir.iterdir(), key=lambda item: _mtime(item), reverse=True):
            if not path.is_dir() or not (path / "manifest.json").is_file():
                continue
            runs.append(_run_listing(path, job_status.get(path.name)))
        return runs

    def run_detail(self, run_id: str) -> dict[str, Any]:
        path = self._run_path(run_id)
        with self._lock:
            job_status = next(
                (job.status for job in self._jobs.values() if job.run_id == run_id),
                None,
            )
        try:
            summary = to_jsonable(summarize_audit_run(path))
            trajectory = audit_trajectory_rows(path)
            inspection = to_jsonable(inspect_harness_run(path))
        except Exception as exc:
            # A run still being produced hasn't written lineage.json/round artifacts yet. Surface a
            # partial, non-error detail (status: running) instead of failing the whole console load, so
            # selecting an in-progress agentic run shows "running" rather than "load failed".
            incomplete = job_status in {"queued", "running"}
            return {
                "schema_version": UI_SCHEMA_VERSION,
                "id": path.name,
                "path": str(path),
                "summary": None,
                "trajectory": [],
                "inspection": None,
                "token_usage": self._usage_for_run(run_id),
                "status": job_status,
                "incomplete": True,
                "error": None if incomplete else str(exc),
                "reproduction_claimed": False,
            }
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "id": path.name,
            "path": str(path),
            "summary": summary,
            "trajectory": trajectory,
            "inspection": inspection,
            "token_usage": self._usage_for_run(run_id),
            "status": job_status,
            "incomplete": False,
            "error": None,
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

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Single-shot GLM 5.2 chat — direct access to the model, independent of the harness loop.

        The request carries the prior turns as ``messages`` (role/content); the latest user turn is the
        prompt and everything before it is folded into the system context so a plain chat-completions call
        stays stateless on the server.
        """

        from self_harness.agentic_session import resolve_zai_api_key, resolve_zai_base_url

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        turns: list[tuple[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                raise ValueError("each message must be an object")
            role = str(item.get("role", "")).strip().lower()
            content = item.get("content")
            if role not in {"user", "assistant", "system"} or not isinstance(content, str):
                raise ValueError("each message needs a role (user/assistant/system) and string content")
            turns.append((role, content))
        if turns[-1][0] != "user":
            raise ValueError("the final message must be a user turn")

        system_text = payload.get("system")
        system_prompt = system_text if isinstance(system_text, str) and system_text.strip() else _CHAT_SYSTEM_PROMPT
        history = turns[:-1]
        if history:
            transcript = "\n".join(f"{role}: {content}" for role, content in history)
            system_prompt = f"{system_prompt}\n\nConversation so far:\n{transcript}"
        user_prompt = turns[-1][1]

        api_key = resolve_zai_api_key()
        base_url = resolve_zai_base_url()
        usage: dict[str, int] = {}
        client = GLMClient(
            transport=build_zai_transport(base_url=base_url, api_key=api_key),
            max_tokens=2048,
            temperature=0.2,
            on_usage=lambda counts: usage.update(counts),
        )
        reply = client.complete(system_prompt, user_prompt)
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "model": "glm-5.2",
            "reply": reply,
            "token_usage": usage,
            "reproduction_claimed": False,
        }

    def dev_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run GLM 5.2 as a real dev agent on a single user-described task, judged by Codex.

        This is the 'use GLM for development' surface: the caller supplies instructions + success criteria
        (and optionally seed files, or asks to use this repo as the workspace). GLM solves with real
        bash/read_file/write_file tools in an isolated copy, Codex grades the result. No harness mutation.
        """

        from self_harness.adapters.agentic.agent_loop import run_agent_loop
        from self_harness.adapters.agentic.codex_verifier import CodexVerifier
        from self_harness.adapters.llm.messages import AnthropicAgentTransport
        from self_harness.agentic_session import resolve_zai_api_key, resolve_zai_base_url
        from self_harness.harness import initial_harness

        instructions = payload.get("instructions")
        success_criteria = payload.get("success_criteria")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("instructions must be a non-empty string")
        if not isinstance(success_criteria, str) or not success_criteria.strip():
            raise ValueError("success_criteria must be a non-empty string")
        workspace_files = payload.get("workspace_files")
        if workspace_files is not None and (
            not isinstance(workspace_files, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in workspace_files.items())
        ):
            raise ValueError("workspace_files must be an object of string paths to string contents")
        use_repo = bool(payload.get("use_repo", False))
        max_steps = _int_payload(payload, "max_steps", default=self.max_steps, minimum=1, maximum=40)

        spec = self._load_persisted_harness() or initial_harness()
        api_key = resolve_zai_api_key()
        base_url = resolve_zai_base_url()

        workdir = Path(tempfile.mkdtemp(prefix="self-harness-devtask-"))
        try:
            if use_repo:
                _seed_repo_workspace(self.root, workdir)
            if workspace_files:
                _seed_workspace_files(workspace_files, workdir)
            system_prompt = render_system_prompt(spec)
            task_prompt = (
                f"{instructions.strip()}\n\n"
                "Work in the current directory. Use the available tools to inspect, edit, and verify."
            )
            loop = run_agent_loop(
                transport=AnthropicAgentTransport(base_url=base_url, api_key=api_key, model="glm-5.2"),
                system_prompt=system_prompt,
                task_prompt=task_prompt,
                workdir=workdir,
                env=dict(os.environ),
                max_steps=max_steps,
                tool_timeout_seconds=self.tool_timeout_seconds,
            )
            verdict = CodexVerifier(binary=self.codex_binary).judge(
                success_criteria=success_criteria,
                task_description=instructions,
                workdir=workdir,
            )
            return {
                "schema_version": UI_SCHEMA_VERSION,
                "model": "glm-5.2",
                "passed": verdict.passed,
                "verdict": {
                    "passed": verdict.passed,
                    "mechanism": verdict.mechanism,
                    "message": verdict.message,
                },
                "stop_reason": loop.stop_reason,
                "steps": loop.steps,
                "tool_calls": loop.tool_calls,
                "final_text": loop.final_text,
                "trajectory": [to_jsonable(event) for event in loop.trace],
                "token_usage": dict(loop.usage),
                "used_repo_workspace": use_repo,
                "reproduction_claimed": False,
            }
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = {
            "rounds": _int_payload(payload, "rounds", default=3, minimum=1, maximum=20),
            "seed": _int_payload(payload, "seed", default=0, minimum=0, maximum=1_000_000),
            "evaluation_repeats": _int_payload(payload, "evaluation_repeats", default=2, minimum=1, maximum=10),
            "max_proposals": _int_payload(payload, "max_proposals", default=8, minimum=1, maximum=64),
            "max_payload_bytes": _int_payload(payload, "max_payload_bytes", default=600, minimum=32, maximum=10_000),
        }
        run_mode = _normalize_run_mode(payload.get("run_mode", "deterministic"))
        evolve = bool(payload.get("evolve", True))
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
            run_mode=run_mode,
        )
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job.id,),
            kwargs={"evolve": evolve},
            name=f"self-harness-ui-{run_id}",
            daemon=True,
        )
        thread.start()
        return _job_to_jsonable(job)

    def _run_job(self, job_id: str, *, evolve: bool = True) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
            job.events.append("running")
            config = dict(job.config)
            path = job.path
            run_mode = job.run_mode
        _log(f"run {job.run_id} started ({run_mode}, rounds={config.get('rounds')}, evolve={evolve})")
        try:
            usage_lock = self._lock

            def _accumulate_usage(counts: dict[str, int]) -> None:
                with usage_lock:
                    accumulated = self._jobs[job_id].token_usage
                    for key, value in counts.items():
                        accumulated[key] = accumulated.get(key, 0) + value

            initial_spec = self._load_persisted_harness() if evolve else None
            engine = self._build_engine(
                run_mode=run_mode,
                config=config,
                out_dir=path,
                initial_spec=initial_spec,
                on_usage=_accumulate_usage,
            )
            summaries = engine.run()
            promotion: dict[str, Any] | None = None
            if evolve:
                self._persist_final_harness(path, summaries)
                promotion = self._auto_promote(path, summaries)
            summary = to_jsonable(summarize_audit_run(path))
            with self._lock:
                job = self._jobs[job_id]
                job.status = "completed"
                job.ended_at = _now()
                job.summary = summary
                job.source_promotion = promotion
                job.events.append("completed")
            _log(
                f"run {job.run_id} completed: held-in {summary.get('final_held_in_score')} "
                f"held-out {summary.get('final_held_out_score')} "
                f"accepted {summary.get('accepted_count')}/{summary.get('rejected_count')} rejected"
            )
            if promotion is not None:
                if promotion.get("applied"):
                    _log(f"run {job.run_id}: reviewer-approved edit integrated into harness.py (gate passed)")
                elif promotion.get("ok") is False:
                    _log(f"run {job.run_id}: auto-integration skipped — {promotion.get('message')}")
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.ended_at = _now()
                job.error = str(exc)
                job.events.append("failed")
            _log(f"run {job.run_id} FAILED: {exc}")

    def _build_engine(
        self,
        *,
        run_mode: str,
        config: dict[str, int],
        out_dir: Path,
        initial_spec: HarnessSpec | None,
        on_usage: Callable[[dict[str, int]], None],
    ) -> SelfHarnessEngine:
        """Construct the engine for the requested run mode.

        ``deterministic`` uses the deterministic runner over ``demo_tasks`` (fast, offline, byte-reproducible
        — used by the test suite and the ``demo`` CLI). ``agentic`` uses GLM 5.2 as a real tool-using solver
        judged by Codex — harness edits change genuine task-success rates, so the acceptance gate promotes
        edits that truly help. The console launches agentic runs.
        """

        if run_mode == "deterministic":
            engine_config = EngineConfig(
                rounds=config["rounds"],
                seed=config["seed"],
                evaluation_repeats=config["evaluation_repeats"],
                proposal_budget=ProposalBudget(
                    max_proposals=config["max_proposals"],
                    max_payload_bytes=config["max_payload_bytes"],
                ),
            )
            return SelfHarnessEngine(
                tasks=demo_tasks(),
                runner=DeterministicRunner(seed=config["seed"]),
                proposer=_build_proposer(self.proposer_mode, on_usage=on_usage),
                out_dir=out_dir,
                config=engine_config,
                initial_spec=initial_spec,
            )
        if run_mode == "agentic":
            return self._build_agentic_engine(
                config=config,
                out_dir=out_dir,
                initial_spec=initial_spec,
                on_usage=on_usage,
            )
        raise ValueError(f"unsupported run mode: {run_mode}")

    def _build_agentic_engine(
        self,
        *,
        config: dict[str, int],
        out_dir: Path,
        initial_spec: HarnessSpec | None,
        on_usage: Callable[[dict[str, int]], None],
        corpus_path: Path | None = None,
    ) -> SelfHarnessEngine:
        from self_harness.agentic_session import (
            build_agentic_adapter,
            build_agentic_config,
            build_proposer,
            resolve_zai_api_key,
            resolve_zai_base_url,
        )
        from self_harness.corpus import load_corpus

        api_key = resolve_zai_api_key()
        base_url = resolve_zai_base_url()
        engine_config = build_agentic_config(
            rounds=config["rounds"],
            seed=config["seed"],
            evaluation_repeats=config["evaluation_repeats"],
            max_proposals=config["max_proposals"],
            max_payload_bytes=config["max_payload_bytes"],
        )
        adapter = build_agentic_adapter(
            api_key=api_key,
            base_url=base_url,
            max_steps=self.max_steps,
            tool_timeout_seconds=self.tool_timeout_seconds,
            codex_binary=self.codex_binary,
            keep_workdir=False,
        )
        corpus = load_corpus(corpus_path or self._default_agentic_corpus(), allow_legacy=False)
        # The agentic proposer is always GLM 5.2 (within-model setup); the solver token usage lands in
        # RunRecord metadata, while the proposer's tokens flow through on_usage.
        proposer = build_proposer("glm", api_key=api_key, base_url=base_url, on_usage=on_usage)
        return SelfHarnessEngine(
            tasks=adapter.load(corpus),
            runner=adapter.runner(),
            proposer=proposer,
            out_dir=out_dir,
            config=engine_config,
            initial_spec=initial_spec,
        )

    def _default_agentic_corpus(self) -> Path:
        candidate = self.root / "examples" / "agentic_corpus.json"
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(
            "no agentic corpus available; expected examples/agentic_corpus.json under the UI root"
        )

    def _load_persisted_harness(self) -> HarnessSpec | None:
        if not self.harness_state.is_file():
            return None
        try:
            value = json.loads(self.harness_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        surfaces = value.get("harness") if isinstance(value, dict) else None
        if not isinstance(surfaces, dict):
            return None
        try:
            return load_harness_spec(surfaces)
        except InvalidPatchError:
            return None

    def _final_harness_spec(self, run_path: Path) -> HarnessSpec | None:
        """Load the run's final harness from the last round's harness_after.json (raw surface dict)."""

        rounds_dir = run_path / "rounds"
        if not rounds_dir.is_dir():
            return None
        round_indices = sorted(
            (int(p.name) for p in rounds_dir.iterdir() if p.is_dir() and p.name.isdigit()),
        )
        for index in reversed(round_indices):
            snapshot = rounds_dir / str(index) / "harness_after.json"
            if not snapshot.is_file():
                continue
            try:
                surfaces = json.loads(snapshot.read_text(encoding="utf-8"))
                return load_harness_spec(surfaces)
            except (OSError, json.JSONDecodeError, InvalidPatchError):
                return None
        return None

    def _persist_final_harness(self, run_path: Path, summaries: list[Any]) -> None:
        # Only advance the persisted lineage when this run actually promoted an edit, so a no-op run never
        # rewrites the evolving harness with an identical (or, on a bad read, reset) snapshot.
        promoted = any(getattr(summary, "accepted", 0) for summary in summaries)
        if not promoted:
            return
        spec = self._final_harness_spec(run_path)
        if spec is None:
            return
        payload = {
            "schema_version": UI_SCHEMA_VERSION,
            "updated_at": _now(),
            "source_run": run_path.name,
            "harness_hash": harness_hash(spec),
            "harness": dump_harness_spec(spec),
        }
        self.harness_state.parent.mkdir(parents=True, exist_ok=True)
        write_stable_json(self.harness_state, payload)

    def _auto_promote(self, run_path: Path, summaries: list[Any]) -> dict[str, Any] | None:
        """Integrate reviewer-approved edits into source automatically (no manual approval step).

        The "reviewer" is the engine's acceptance gate (Δin≥0 ∧ Δho≥0 ∧ max>0). If it promoted at least one
        edit this run, write the evolved harness back into harness.py via the same correctness-gated path the
        manual button uses. Returns the promotion result (or None when nothing was accepted / disabled).
        """

        if not self.auto_promote_to_source:
            return None
        promoted = any(getattr(summary, "accepted", 0) for summary in summaries)
        if not promoted:
            return None
        spec = self._final_harness_spec(run_path)
        if spec is None:
            return None
        try:
            return self._promote_spec_to_source(spec, write=True)
        except (OSError, ValueError, InvalidPatchError) as exc:
            return {"ok": False, "applied": False, "message": f"auto-integration failed: {exc}"}

    def _harness_state_status(self) -> dict[str, Any]:
        if not self.harness_state.is_file():
            return {"evolving": False, "source_run": None, "harness_hash": None, "updated_at": None}
        try:
            value = json.loads(self.harness_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"evolving": False, "source_run": None, "harness_hash": None, "updated_at": None}
        return {
            "evolving": isinstance(value.get("harness"), dict),
            "source_run": value.get("source_run"),
            "harness_hash": value.get("harness_hash"),
            "updated_at": value.get("updated_at"),
        }

    def reset_harness_state(self) -> dict[str, Any]:
        """Discard the evolving lineage so the next run starts from initial_harness() (Figure 3)."""

        try:
            self.harness_state.unlink()
        except FileNotFoundError:
            pass
        return {"schema_version": UI_SCHEMA_VERSION, "ok": True, "harness_state": self._harness_state_status()}

    def promote_to_source(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Write a run's final (evolved) harness back into ``harness.py``'s ``initial_harness()``.

        This closes the self-improvement loop into real source. Reviewer-approved edits are integrated by
        default: passing no ``apply`` (or ``{"apply": true}``) writes and gates immediately. Pass
        ``{"apply": false}`` for a dry-run preview that only returns the diff without touching source.

        The write itself stays correctness-checked, not approval-gated: it computes a unified diff, backs up
        the original to ``harness.py.bak``, rewrites the marker block, and runs the gate (ruff + mypy + an
        import round-trip). If the gate fails the original is restored automatically, so a bad rewrite can
        never be left in the tree.
        """

        run_path = self._run_path(run_id)
        spec = self._final_harness_spec(run_path)
        if spec is None:
            raise ValueError("run has no final harness to promote")

        result = self._promote_spec_to_source(spec, write=bool(payload.get("apply", True)))
        result["run_id"] = run_id
        return result

    def _promote_spec_to_source(self, spec: HarnessSpec, *, write: bool) -> dict[str, Any]:
        """Render ``spec`` into ``harness.py``'s ``initial_harness()`` and (optionally) write + gate it.

        Shared by the promote endpoint and the post-run auto-integration path. ``write=False`` returns the
        diff only (dry-run preview). ``write=True`` backs up, rewrites the marker block, runs the gate, and
        restores from backup if the gate fails — so source is never left in a broken state.
        """

        import difflib

        harness_path = self.root / "src" / "self_harness" / "harness.py"
        if not harness_path.is_file():
            raise FileNotFoundError("cannot locate src/self_harness/harness.py under the UI root")
        original = harness_path.read_text(encoding="utf-8")
        new_block = render_initial_harness_source(spec)
        rewritten = _replace_marked_block(
            original,
            INITIAL_HARNESS_START_MARKER,
            INITIAL_HARNESS_END_MARKER,
            new_block,
        )
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                rewritten.splitlines(keepends=True),
                fromfile="harness.py",
                tofile="harness.py (promoted)",
            )
        )
        result: dict[str, Any] = {
            "schema_version": UI_SCHEMA_VERSION,
            "harness_hash": harness_hash(spec),
            "diff": diff,
            "changed": rewritten != original,
            "applied": False,
            "reproduction_claimed": False,
        }
        if not write:
            result["ok"] = True
            result["message"] = "dry-run: pass {\"apply\": true} to write and gate"
            return result
        if rewritten == original:
            result["ok"] = True
            result["message"] = "final harness already equals source initial_harness(); nothing to write"
            return result

        backup_path = harness_path.with_suffix(".py.bak")
        backup_path.write_text(original, encoding="utf-8")
        harness_path.write_text(rewritten, encoding="utf-8")
        gate = _run_source_gate(self.root, harness_hash(spec))
        if gate["ok"]:
            result.update(ok=True, applied=True, backup=str(backup_path), gate=gate)
            result["message"] = "promoted to source; gate passed"
        else:
            harness_path.write_text(original, encoding="utf-8")
            backup_path.unlink(missing_ok=True)
            result.update(ok=False, applied=False, gate=gate)
            result["message"] = "gate failed; source restored from backup"
        return result

    def _run_path(self, run_id: str) -> Path:
        if Path(run_id).name != run_id or run_id in {"", ".", ".."}:
            raise ValueError("invalid run id")
        path = (self.runs_dir / run_id).resolve()
        if path.parent != self.runs_dir:
            raise ValueError("run id escapes runs directory")
        if not (path / "manifest.json").is_file():
            raise FileNotFoundError(f"run not found: {run_id}")
        return path


def serve_ui(
    *,
    host: str,
    port: int,
    root: Path,
    runs_dir: Path,
    proposer_mode: str = "heuristic",
    harness_state: Path | None = None,
    max_steps: int = 12,
    tool_timeout_seconds: int = 30,
    codex_binary: str = "codex",
    auto_promote_to_source: bool = True,
) -> int:
    app = HarnessUiApp(
        root=root,
        runs_dir=runs_dir,
        proposer_mode=proposer_mode,
        harness_state=harness_state,
        max_steps=max_steps,
        tool_timeout_seconds=tool_timeout_seconds,
        codex_binary=codex_binary,
        auto_promote_to_source=auto_promote_to_source,
    )
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
                if parsed.path.startswith("/static/"):
                    self._send_static(parsed.path.removeprefix("/static/"))
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
                if parsed.path == "/api/chat":
                    self._send_json(app.chat(self._read_json()))
                    return
                if parsed.path == "/api/dev-task":
                    self._send_json(app.dev_task(self._read_json()))
                    return
                if parsed.path == "/api/harness/reset":
                    self._send_json(app.reset_harness_state())
                    return
                if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/promote-to-source"):
                    run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/promote-to-source").strip("/")
                    self._send_json(app.promote_to_source(run_id, self._read_json()))
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "unknown route")
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def log_message(self, format: str, *args: Any) -> None:
            # Quiet the high-frequency console polling so the window shows meaningful activity (runs,
            # dev-tasks, chat, promotions) rather than a flood of /api/state + /api/preflight GETs.
            message = format % args
            if any(noisy in message for noisy in ('GET /api/state', 'GET /api/preflight', 'GET /static/')):
                return
            timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            print(f"{timestamp} {self.address_string()} {message}")

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

        def _send_static(self, name: str) -> None:
            # Vendored front-end assets only: a strict allowlist keeps this off the filesystem-traversal
            # path entirely (the console must work offline, so Alpine is served locally rather than via CDN).
            content_type = _STATIC_ASSETS.get(name)
            if content_type is None:
                self._send_error(HTTPStatus.NOT_FOUND, "unknown asset")
                return
            asset = _STATIC_DIR / name
            try:
                payload = asset.read_bytes()
            except OSError:
                self._send_error(HTTPStatus.NOT_FOUND, "asset unavailable")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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


def _seed_repo_workspace(root: Path, workdir: Path) -> None:
    """Copy this repo into the dev-task workspace, skipping heavy/secret dirs.

    GLM edits a *copy*, never the live tree — consistent with the per-attempt workdir model and the host
    decision (no in-place mutation, which would need container isolation).
    """

    for entry in root.iterdir():
        if entry.name in _REPO_COPY_SKIP or entry.name.startswith(".env"):
            continue
        target = workdir / entry.name
        if entry.is_dir():
            shutil.copytree(
                entry,
                target,
                ignore=shutil.ignore_patterns(*_REPO_COPY_SKIP, ".env*"),
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(entry, target)


def _seed_workspace_files(files: dict[str, str], workdir: Path) -> None:
    """Write inline {relative_path: content} files, confining every target inside the workspace."""

    for rel, content in files.items():
        target = (workdir / rel).resolve()
        if workdir not in target.parents and target != workdir:
            raise ValueError(f"workspace file escapes workspace: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _replace_marked_block(source: str, start_marker: str, end_marker: str, new_block: str) -> str:
    start = source.find(start_marker)
    end = source.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise ValueError("initial_harness() marker block not found in harness.py")
    end_full = end + len(end_marker)
    return source[:start] + new_block + source[end_full:]


def _run_source_gate(root: Path, expected_hash: str) -> dict[str, Any]:
    """Validate a promoted harness.py rewrite: ruff + mypy + a fresh-process import round-trip.

    The import check runs in a subprocess so it picks up the just-written source (not the already-imported
    module), reconstructs ``initial_harness()``, and confirms it hashes to the promoted spec. Baseline-
    coupled test suites are intentionally NOT run here: a promoted harness is, by design, no longer the
    Figure-3 baseline those fixtures assume, so running them would always fail a valid promotion.
    """

    import subprocess

    python = root / ".venv" / "bin" / "python"
    interpreter = str(python) if python.is_file() else "python3"
    roundtrip = (
        "import sys; from self_harness.harness import initial_harness, harness_hash; "
        f"sys.exit(0 if harness_hash(initial_harness()) == {expected_hash!r} else 17)"
    )
    checks = [
        ("ruff", [interpreter, "-m", "ruff", "check", "src/self_harness/harness.py"]),
        ("mypy", [interpreter, "-m", "mypy", "src/self_harness/harness.py"]),
        ("import-roundtrip", [interpreter, "-c", roundtrip]),
    ]
    results: list[dict[str, Any]] = []
    ok = True
    for name, cmd in checks:
        try:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=300)
            passed = proc.returncode == 0
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
            results.append({"name": name, "ok": passed, "output": "\n".join(tail)})
            ok = ok and passed
        except (OSError, subprocess.SubprocessError) as exc:
            results.append({"name": name, "ok": False, "output": f"{name} could not run: {exc}"})
            ok = False
    return {"ok": ok, "checks": results}


def _normalize_proposer_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"heuristic", "deterministic"}:
        return "heuristic"
    if normalized in {"glm", "glm-5.2", "zai", "z.ai"}:
        return "glm"
    raise ValueError("proposer_mode must be heuristic or glm")


def _normalize_run_mode(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"deterministic", "", "demo"}:
        return "deterministic"
    if normalized in {"agentic", "glm", "real"}:
        return "agentic"
    raise ValueError("run_mode must be deterministic or agentic")


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
        base_url = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/anthropic")
        transport = build_zai_transport(base_url=base_url, api_key=api_key)
        return LLMProposer(
            GLMClient(transport=transport, max_tokens=4096, temperature=0.0, on_usage=on_usage)
        )
    raise ValueError(f"unsupported proposer mode: {mode}")


def _run_listing(path: Path, job_status: str | None = None) -> dict[str, Any]:
    try:
        summary: dict[str, Any] | None = to_jsonable(summarize_audit_run(path))
        error = None
    except Exception as exc:
        summary = None
        # A run whose job is still active simply hasn't written its audit artifacts yet — surface that as
        # "running", not as an audit error, so the UI badge doesn't cry wolf during long agentic runs.
        error = None if job_status in {"queued", "running"} else str(exc)
    return {
        "id": path.name,
        "path": str(path),
        "updated_at": _timestamp(_mtime(path)),
        "summary": summary,
        "status": job_status,
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
        "run_mode": job.run_mode,
        "source_promotion": job.source_promotion,
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


def _log(message: str) -> None:
    """Emit a concise lifecycle line to the console window so an operator can follow activity live."""

    print(f"{_now()} [run] {message}", flush=True)


_HTML = r"""<!doctype html>
<html lang="en" x-data="shConsole()" x-init="init()" :data-theme="theme">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SelfHarness Console</title>
  <script defer src="/static/alpine-3.14.1.min.js"></script>
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

  <div id="sh-load-error" style="display:none; padding:22px; color:#f85149; font-family:ui-monospace,Menlo,monospace;">
    SelfHarness console: the front-end framework (Alpine.js) failed to load from <code>/static/alpine-3.14.1.min.js</code>.
    The page cannot become interactive. The raw JSON API is still available at
    <code>/api/state</code>, <code>/api/preflight</code>, and <code>/api/runs/&lt;id&gt;</code>.
  </div>
  <script>
    // Fail loud, never blank: if Alpine never initializes (vendored asset missing/corrupt), strip the
    // x-cloak gate so the operator sees an explanation instead of an empty black page.
    setTimeout(function () {
      if (!window.Alpine) {
        var err = document.getElementById('sh-load-error');
        if (err) err.style.display = 'block';
        document.querySelectorAll('[x-cloak]').forEach(function (el) { el.removeAttribute('x-cloak'); });
      }
    }, 1500);
  </script>

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
            <label>Run mode</label>
            <div class="muted" style="font-size:12px;">GLM 5.2 solves real tasks with bash/file tools on this host (no container); Codex judges. Promoted edits change genuine pass rates.</div>
            <div class="muted" style="font-size:12px; margin-top:6px; color: var(--warn);" x-show="!glm.key_present">Set ZAI_API_KEY and restart to enable runs.</div>
          </div>
          <div class="field">
            <label>Harness lineage</label>
            <div style="display:flex; gap:8px; align-items:center;">
              <label style="display:flex; gap:6px; align-items:center; font-size:13px; color:var(--ink);">
                <input type="checkbox" style="width:auto" x-model="form.evolve"> evolve from persisted
              </label>
              <span class="spacer"></span>
              <button class="ghost tiny" @click="resetHarness()" :disabled="!harnessState.evolving" title="Discard the evolving lineage; next run starts from initial_harness()">Reset</button>
            </div>
            <div class="muted" style="font-size:12px; margin-top:6px;"
                 x-text="harnessState.evolving ? ('Evolving from ' + (harnessState.source_run||'?') + ' · ' + (harnessState.harness_hash||'').slice(0,12)) : 'Starting from initial_harness() (Figure 3).'"></div>
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
                <span class="badge" :class="runBadgeClass(r)" x-text="runBadge(r)"></span>
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
        <div class="tabs">
          <button :class="{on: view==='runs'}" @click="view='runs'">Runs</button>
          <button :class="{on: view==='devtask'}" @click="view='devtask'">Dev task</button>
          <button :class="{on: view==='chat'}" @click="view='chat'">Chat</button>
        </div>
        <span class="spacer"></span>
        <div class="tabs" x-show="view==='runs' && selectedId">
          <button :class="{on: tab==='overview'}" @click="tab='overview'">Overview</button>
          <button :class="{on: tab==='trajectory'}" @click="tab='trajectory'">Trajectory</button>
          <button :class="{on: tab==='round'}" @click="tab='round'" x-show="roundData">Round <span x-text="roundData ? roundData.round : ''"></span></button>
          <button :class="{on: tab==='harness'}" @click="loadHarness()">Harness diff</button>
          <button :class="{on: tab==='raw'}" @click="tab='raw'">Raw</button>
        </div>
      </div>
      <div class="card-b">
        <div class="banner" :class="glm.status" x-show="view!=='runs' || glm.status==='needs_funding' || glm.status==='operational'">
          <span class="dot" :class="glm.status"></span>
          <div>
            <div style="font-weight:700" x-text="glmLabel()"></div>
            <div class="muted" style="font-size:12px" x-text="glm.detail || ('GLM 5.2 via ' + (glm.mode||'preflight'))"></div>
          </div>
        </div>

        <!-- ============ DEV TASK VIEW ============ -->
        <div x-show="view==='devtask'">
          <p class="muted" style="font-size:13px; margin-top:0;">Hand GLM 5.2 a development task. It solves with real <code>bash</code>/<code>read_file</code>/<code>write_file</code> tools in an isolated workspace; the Codex CLI judges the result. Runs under the current evolving harness.</p>
          <div class="field"><label>Instructions (what GLM should do)</label><textarea rows="4" style="width:100%" x-model="dev.instructions" placeholder="e.g. Create fizzbuzz.py that prints 1..15 with Fizz/Buzz/FizzBuzz."></textarea></div>
          <div class="field"><label>Success criteria (how Codex judges)</label><textarea rows="3" style="width:100%" x-model="dev.success_criteria" placeholder="e.g. fizzbuzz.py exists and python3 fizzbuzz.py prints the expected sequence."></textarea></div>
          <div class="field">
            <label style="display:flex; gap:8px; align-items:center;"><input type="checkbox" style="width:auto" x-model="dev.use_repo"> Use the SelfHarness repo as the workspace (a copy)</label>
            <div class="muted" style="font-size:12px; margin-top:4px;">GLM edits a copy of this repo, never the live tree.</div>
          </div>
          <div class="field"><label>Max steps</label><input type="number" min="1" max="40" x-model.number="dev.max_steps" style="width:120px;"></div>
          <button class="primary" @click="runDevTask()" :disabled="dev.running || !glm.key_present" x-text="dev.running ? 'GLM working…' : 'Run dev task'"></button>
          <div x-show="dev.error" class="muted" style="color:var(--danger); margin-top:10px;" x-text="dev.error"></div>
          <template x-if="dev.result">
            <div style="margin-top:16px;">
              <div class="scores">
                <div class="stat"><div class="k">Verdict</div><div class="v" :class="dev.result.passed ? 'delta-up' : 'delta-down'" x-text="dev.result.passed ? 'PASS' : 'FAIL'"></div></div>
                <div class="stat"><div class="k">Steps / tools</div><div class="v" x-text="dev.result.steps + ' / ' + dev.result.tool_calls"></div></div>
                <div class="stat"><div class="k">Stop reason</div><div class="v" style="font-size:15px" x-text="dev.result.stop_reason"></div></div>
                <div class="stat"><div class="k">Solver tokens</div><div class="v" x-text="(dev.result.token_usage.total_tokens||0).toLocaleString()"></div></div>
              </div>
              <p class="muted" style="font-size:12px; margin-top:10px;" x-text="'Judge: ' + (dev.result.verdict ? dev.result.verdict.message : '')"></p>
              <h3 style="font-size:14px; margin:14px 0 8px;">Final message</h3>
              <pre class="box" x-text="dev.result.final_text || '(none)'"></pre>
              <h3 style="font-size:14px; margin:14px 0 8px;">Trajectory</h3>
              <div class="traj">
                <template x-for="(ev, i) in dev.result.trajectory" :key="i">
                  <div class="run-row" style="cursor:default; display:block;">
                    <div class="muted mono" style="font-size:11px;" x-text="ev.kind"></div>
                    <pre class="box" style="font-size:12px; margin-top:4px;" x-text="(ev.message||'').slice(0,1200)"></pre>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>

        <!-- ============ CHAT VIEW ============ -->
        <div x-show="view==='chat'">
          <p class="muted" style="font-size:13px; margin-top:0;">Talk to GLM 5.2 directly. Single-shot calls with conversation context — independent of the harness loop.</p>
          <div class="traj" style="max-height:50vh; overflow:auto; margin-bottom:12px;">
            <template x-for="(m, i) in chat.messages" :key="i">
              <div class="run-row" style="cursor:default; display:block;">
                <div class="muted mono" style="font-size:11px;" x-text="m.role"></div>
                <pre class="box" style="margin-top:4px;" x-text="m.content"></pre>
              </div>
            </template>
            <div class="empty" x-show="chat.messages.length===0">No messages yet.</div>
          </div>
          <div style="display:flex; gap:8px;">
            <input style="flex:1" x-model="chat.input" @keydown.enter="sendChat()" placeholder="Ask GLM 5.2 something…">
            <button class="primary" @click="sendChat()" :disabled="chat.sending || !glm.key_present" x-text="chat.sending ? '…' : 'Send'"></button>
          </div>
          <div x-show="chat.error" class="muted" style="color:var(--danger); margin-top:8px;" x-text="chat.error"></div>
          <div class="muted" style="font-size:12px; margin-top:8px;" x-show="chat.usage.total_tokens" x-text="'Last turn: ' + chat.usage.total_tokens + ' tokens'"></div>
        </div>

        <!-- ============ RUNS VIEW ============ -->
        <div x-show="view==='runs'">
        <div class="empty" x-show="!selectedId">Select a run to inspect its trajectory, proposals, evidence, and harness diff.</div>

        <!-- Overview -->
        <div x-show="selectedId && tab==='overview'">
          <template x-if="detail && detail.incomplete">
            <div class="box" style="padding:16px;">
              <div style="font-weight:600;" x-text="detail.status === 'failed' ? 'Run failed' : 'Run in progress…'"></div>
              <p class="muted" style="font-size:13px; margin:8px 0 0;" x-show="detail.status === 'queued' || detail.status === 'running'">GLM 5.2 is solving the task corpus and Codex is judging each result. Audit artifacts (scores, trajectory, harness diff) appear here once the run completes. This view refreshes automatically.</p>
              <p class="muted" style="font-size:13px; margin:8px 0 0; color:var(--danger);" x-show="detail.error" x-text="detail.error"></p>
              <div class="scores" style="margin-top:12px" x-show="hasUsage()">
                <div class="stat"><div class="k">GLM input tokens</div><div class="v" x-text="(detail.token_usage.input_tokens||0).toLocaleString()"></div></div>
                <div class="stat"><div class="k">GLM output tokens</div><div class="v" x-text="(detail.token_usage.output_tokens||0).toLocaleString()"></div></div>
                <div class="stat"><div class="k">GLM total tokens</div><div class="v" x-text="(detail.token_usage.total_tokens||0).toLocaleString()"></div></div>
              </div>
            </div>
          </template>
          <template x-if="detail && detail.summary && !detail.incomplete">
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
              <p class="muted" style="font-size:12px; margin-top:-4px;" x-show="autoPromote">Reviewer-approved edits are integrated into <span class="mono">harness.py</span> automatically when a run accepts an edit (correctness-gated: ruff + mypy + import round-trip, auto-restored on failure). Use the buttons below to preview or re-integrate manually.</p>
              <div style="display:flex; gap:8px; align-items:center; margin-bottom:12px;">
                <button class="tiny" @click="previewPromote()" :disabled="promote.busy">Preview diff</button>
                <button class="primary tiny" @click="applyPromote()" :disabled="promote.busy || !promote.diff" x-text="promote.busy ? 'Working…' : 'Integrate into harness.py'"></button>
                <span class="muted" style="font-size:12px;" x-show="promote.message" x-text="promote.message"></span>
              </div>
              <pre class="box mono" style="font-size:12px; max-height:30vh; overflow:auto;" x-show="promote.diff" x-text="promote.diff"></pre>
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
        </div><!-- /view==='runs' -->
      </div>
    </section>
  </main>

  <div class="footer-note" x-cloak>SelfHarness is a paper-faithful implementation (arXiv:2606.09498). It does not claim Terminal-Bench reproduction. Model: <b>GLM 5.2</b> via Z.ai when configured.</div>

  <div class="toast" x-show="toast" x-transition x-cloak x-text="toast"></div>

  <script>
    function shConsole() {
      return {
        theme: localStorage.getItem('sh-theme') || 'dark',
        glm: { status: 'not_checked', detail: '', key_present: false, mode: 'dry-run' },
        serverProposer: 'heuristic',
        runs: [], jobs: [], selectedId: null, detail: null, roundData: null, harnessData: null,
        tab: 'overview', view: 'runs', starting: false, toast: '',
        harnessState: { evolving: false, source_run: null, harness_hash: null },
        autoPromote: true, seenPromotions: {},
        form: { evolve: true, rounds: 3, seed: 0, evaluation_repeats: 2, max_proposals: 8, max_payload_bytes: 600 },
        dev: { instructions: '', success_criteria: '', use_repo: false, max_steps: 12, running: false, result: null, error: '' },
        chat: { messages: [], input: '', sending: false, error: '', usage: {} },
        promote: { busy: false, diff: '', message: '' },

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
            this.serverProposer = state.proposer_mode || 'heuristic';
            this.harnessState = state.harness_state || { evolving: false };
            this.autoPromote = state.auto_promote_to_source !== false;
            for (const j of (state.jobs || [])) {
              if (j.status === 'completed' && j.source_promotion && !this.seenPromotions[j.id]) {
                this.seenPromotions[j.id] = true;
                const p = j.source_promotion;
                if (p.applied) this.flash('Reviewer-approved edit integrated into harness.py (gate passed).');
                else if (p.ok === false) this.flash('Auto-integration: ' + (p.message || 'gate failed; source restored.'));
              }
            }
            this.jobs = (state.jobs || []).filter(j => j.status === 'running' || j.status === 'queued' || j.status === 'failed');
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
            const body = { run_mode: 'agentic', evolve: this.form.evolve, rounds: this.form.rounds, seed: this.form.seed, evaluation_repeats: this.form.evaluation_repeats, max_proposals: this.form.max_proposals, max_payload_bytes: this.form.max_payload_bytes };
            const job = await this.api('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            this.flash('Run ' + job.run_id + ' started (agentic)');
            setTimeout(() => this.refresh(), 600);
          } catch (e) { this.flash('start failed: ' + e.message); }
          finally { this.starting = false; }
        },
        async resetHarness() {
          try { const r = await this.api('/api/harness/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); this.harnessState = r.harness_state || { evolving: false }; this.flash('Harness lineage reset to initial.'); }
          catch (e) { this.flash('reset failed: ' + e.message); }
        },
        async runDevTask() {
          this.dev.running = true; this.dev.error = ''; this.dev.result = null;
          try {
            const body = { instructions: this.dev.instructions, success_criteria: this.dev.success_criteria, use_repo: this.dev.use_repo, max_steps: this.dev.max_steps };
            this.dev.result = await this.api('/api/dev-task', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          } catch (e) { this.dev.error = e.message; }
          finally { this.dev.running = false; }
        },
        async sendChat() {
          const text = (this.chat.input || '').trim();
          if (!text || this.chat.sending) return;
          this.chat.messages.push({ role: 'user', content: text });
          this.chat.input = ''; this.chat.sending = true; this.chat.error = '';
          try {
            const data = await this.api('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages: this.chat.messages }) });
            this.chat.messages.push({ role: 'assistant', content: data.reply });
            this.chat.usage = data.token_usage || {};
          } catch (e) { this.chat.error = e.message; }
          finally { this.chat.sending = false; }
        },
        async previewPromote() {
          if (!this.selectedId) return;
          this.promote.busy = true; this.promote.message = '';
          try {
            const r = await this.api('/api/runs/' + encodeURIComponent(this.selectedId) + '/promote-to-source', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ apply: false }) });
            this.promote.diff = r.diff || ''; this.promote.message = r.changed ? (r.message || 'preview ready') : 'no change vs source';
          } catch (e) { this.promote.message = 'preview failed: ' + e.message; }
          finally { this.promote.busy = false; }
        },
        async applyPromote() {
          if (!this.selectedId) return;
          this.promote.busy = true; this.promote.message = '';
          try {
            const r = await this.api('/api/runs/' + encodeURIComponent(this.selectedId) + '/promote-to-source', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ apply: true }) });
            this.promote.message = r.message || (r.ok ? 'applied' : 'failed');
            this.flash(r.ok ? 'Promoted to source; gate passed.' : 'Promote failed; source restored.');
          } catch (e) { this.promote.message = 'apply failed: ' + e.message; }
          finally { this.promote.busy = false; }
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
          if (r.status === 'running' || r.status === 'queued') return r.status + '…';
          if (s.final_held_in_score == null) return r.error ? 'audit error' : 'pending';
          return 'in ' + this.pct(s.final_held_in_score) + ' · out ' + this.pct(s.final_held_out_score);
        },
        runBadge(r) {
          if (r.status === 'running' || r.status === 'queued') return r.status;
          return r.error ? 'error' : 'audit';
        },
        runBadgeClass(r) {
          if (r.status === 'running' || r.status === 'queued') return 'superseded';
          return r.error ? 'rejected' : 'accepted';
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
