from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, cast

from self_harness.adapters.container_preflight import run_container_preflight
from self_harness.adapters.container_verifier import (
    ContainerMode,
    ContainerVerifierTaskAdapter,
    parse_container_command,
)
from self_harness.adapters.http_verifier import HttpVerifierTaskAdapter
from self_harness.adapters.in_process_python import InProcessPythonTaskAdapter
from self_harness.adapters.local_subprocess import LocalSubprocessTaskAdapter
from self_harness.adapters.terminal_bench.agent_adapter import ClaudeCodeAgentAdapter, DeepAgentAdapter
from self_harness.adapters.terminal_bench.capture import capture_single_task
from self_harness.adapters.terminal_bench.corpus import load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.harbor_artifacts import inspect_run_dir
from self_harness.adapters.terminal_bench.ingest import ingest_harbor_run
from self_harness.adapters.terminal_bench.preflight import (
    run_preflight,
    write_preflight_report,
)
from self_harness.adapters.terminal_bench.runner import HarborRunner, RunnerMode, validate_harbor_image_trust
from self_harness.attestations import (
    AttestationError,
    attestation_report_to_jsonable,
    verify_attestation,
)
from self_harness.audit import (
    audit_trajectory_rows,
    diff_audit_runs,
    inspect_harness_run,
    summarize_audit_run,
    write_audit_trajectory,
    write_harness_inspection,
)
from self_harness.audit_migration import (
    LATEST_AUDIT_SCHEMA_VERSION,
    AuditMigrationError,
    audit_migration_report_to_jsonable,
    migrate_audit_tree,
)
from self_harness.audit_verify import audit_verification_report_to_jsonable, verify_audit_run
from self_harness.audit_verify_live import (
    live_audit_verification_report_to_jsonable,
    verify_live_audit_run,
)
from self_harness.capture_admit import (
    CAPTURE_ADMISSION_BOUNDARY,
    CaptureAdmissionError,
    capture_admission_report_to_jsonable,
    run_capture_admission,
)
from self_harness.capture_extract import (
    CAPTURE_EXTRACT_BOUNDARY,
    EXTRACTABLE_ARTIFACT_CLASSES,
    CaptureExtractError,
    extract_artifact_from_paths,
    parse_proposer_backend_map,
)
from self_harness.capture_manifest import capture_manifest_report_to_jsonable, verify_capture_manifest
from self_harness.capture_manifest_build import (
    CaptureManifestBuildError,
    build_capture_manifest,
    capture_manifest_document_to_jsonable,
    load_planned_artifact,
    write_capture_manifest_document,
)
from self_harness.capture_manifest_diff import (
    capture_manifest_diff_report_to_jsonable,
    diff_capture_manifest_to_bundle,
)
from self_harness.capture_rehearsal import (
    CaptureRehearsalError,
    capture_rehearsal_report_to_jsonable,
    run_capture_rehearsal,
)
from self_harness.config import EngineConfig
from self_harness.corpus import TaskCorpus, corpus_checksum, load_corpus, split_counts
from self_harness.corpus_keyring import (
    CorpusKeyring,
    KeyringEntry,
    KeyringStatus,
    add_keyring_entry,
    empty_keyring,
    keyring_to_jsonable,
    load_keyring,
    save_keyring,
    set_keyring_entry_status,
    verify_corpus_with_keyring,
)
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    PRIVATE_KEY_ENCRYPTION_PROFILE,
    generate_keypair,
    public_key_fingerprint,
    sign_corpus,
)
from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.engine import RoundSummary, SelfHarnessEngine
from self_harness.exceptions import (
    AuditCorruptError,
    ContainerVerifierError,
    CorpusSigningError,
    HttpVerifierError,
    InProcessVerifierError,
    KeyringError,
    TaskLoadError,
)
from self_harness.image_policy import ImagePolicyError, load_image_policy
from self_harness.model_backend_preflight import (
    MODEL_BACKEND_PREFLIGHT_BOUNDARY,
    ModelBackendPreflightError,
    evaluate_model_backend_preflight,
    model_backend_preflight_report_to_jsonable,
)
from self_harness.operator_promotion import (
    POLICY_KINDS,
    PROMOTION_STATUSES,
    PromotionError,
    PromotionManifest,
    add_promotion_entry,
    init_promotion_manifest,
    promotion_manifest_to_jsonable,
    promotion_signature_to_jsonable,
    promotion_verification_report_to_jsonable,
    set_promotion_status,
    sign_promotion_manifest,
    verify_promotion_manifest,
)
from self_harness.project_manager import git_sync, list_projects, load_project, save_project
from self_harness.proposer import HeuristicProposer
from self_harness.reporting import write_benchmark_report
from self_harness.reproduction_readiness import (
    ReproductionReadinessError,
    load_readiness_matrix_report,
    load_reproduction_requirements,
)
from self_harness.research import ResearchConfig, ResearchIntegrator
from self_harness.signing import (
    DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    DEFAULT_SIGNER_TIMEOUT_SECONDS,
    EXTERNAL_SIGNER_PROTOCOL_VERSION,
    ExternalSignerError,
    parse_external_signer_command,
    sign_corpus_with_external_signer,
)
from self_harness.types import ProposalBudget, stable_json_dumps, to_jsonable

# Coding-agent step budget per turn. A "review this 10k-LOC workspace and implement 5 fixes" task needs
# far more than the old default of 24 tool-calling steps; 80 keeps big tasks moving while bounding cost.
DEFAULT_CODE_MAX_STEPS = 80

# Loop evaluation repeats. GLM solving is stochastic, so a single attempt per task (repeats=1) makes a
# real fix indistinguishable from a coin flip — a genuine improvement registers as a "tie" and is dropped
# (a recall failure). Repeating each task N times and counting passes across all N×task records both
# averages out that noise AND gives the strict acceptance gate graded resolution (a task going 1/3→3/3 is
# a real +2, not a tie), so it raises recall WITHOUT weakening precision. 3 is a good cost/signal balance.
DEFAULT_LOOP_EVAL_REPEATS = 3
HEADLESS_CODE_MODELS = {
    "codex": "codex",
    "codex-cli": "codex",
    "agy": "agy",
    "agy-cli": "agy",
    "claude": "claude",
    "claude-cli": "claude",
    "claude-code": "claude",
}


def _headless_backend_for_model(model: str) -> str | None:
    normalized = model.strip().lower().replace("_", "-")
    return HEADLESS_CODE_MODELS.get(normalized)


def _headless_binary_for_backend(backend: str) -> str:
    specific = os.environ.get(f"SELF_HARNESS_{backend.upper()}_BINARY")
    if specific:
        return specific
    generic = os.environ.get("SELF_HARNESS_HEADLESS_BINARY")
    if generic:
        return generic
    return backend


def run_code_default() -> int:
    """Launch the coding agent with sensible defaults (used by the home menu / bare flow).

    Uses the current directory, the central shared harness, and saved settings. This is what the menu's
    [1] Code action calls so the user never has to assemble flags.
    """

    from self_harness import user_config

    cfg = user_config.load_config()
    # _run_code already defaults to the shared central harness + inbox (unless --local-harness); just pass
    # the user's saved interactive defaults through.
    return _run_code(
        root=Path.cwd(),
        harness_state=None,
        inbox_dir=None,
        max_steps=int(cfg.get("max_steps", DEFAULT_CODE_MAX_STEPS) or DEFAULT_CODE_MAX_STEPS),
        tool_timeout_seconds=int(cfg.get("tool_timeout_seconds", 30) or 30),
        harvest=bool(cfg.get("harvest", True)),
        resume=None,
        plain=False,
        local_harness=bool(cfg.get("share_central_harness", True) is False),
    )


def run_console_default() -> int:
    """Start the web console with defaults (home menu [3] Console)."""

    from self_harness.ui import serve_ui

    return serve_ui(
        host="127.0.0.1",
        port=8765,
        root=Path("."),
        runs_dir=Path("runs"),
        proposer_mode="glm",
        harness_state=None,
        max_steps=12,
        tool_timeout_seconds=30,
        codex_binary="codex",
        auto_promote_to_source=True,
        task_generation=True,
        generation_guard=False,
    )


def _resolve_eval_repeats(explicit: int | None, cfg: Any) -> int:
    """Resolve loop eval repeats: explicit flag → saved setting → default. Clamped to >= 1."""

    if explicit is not None:
        return max(1, explicit)
    saved = cfg.get("loop_eval_repeats")
    if saved is not None:
        try:
            return max(1, int(saved))
        except (TypeError, ValueError):
            pass
    return DEFAULT_LOOP_EVAL_REPEATS


def run_loop_default(*, rounds: int = 1, seed: int = 0, eval_repeats: int | None = None) -> int:
    """Run the continuous self-improvement loop in the foreground until Ctrl-C / SIGTERM.

    Reuses the web app's autoloop controller (HarnessUiApp.start_autoloop) without serving HTTP, then
    blocks, printing periodic status, until interrupted. SIGTERM (sent by `loop stop` when this is the
    backgrounded process) is handled the same as Ctrl-C: stop after the current run, then exit cleanly.
    """

    import signal
    import time

    from self_harness import user_config
    from self_harness.agentic_session import HOST_EXEC_WARNING_LINES
    from self_harness.console_style import console
    from self_harness.loop_paths import loop_root
    from self_harness.ui import HarnessUiApp

    repeats = _resolve_eval_repeats(eval_repeats, user_config.load_config())
    root = loop_root()

    for line in HOST_EXEC_WARNING_LINES:
        console.line(line, "warn")
    console.blank()

    app = HarnessUiApp(
        root=root,
        runs_dir=Path("runs"),
        proposer_mode="glm",
        auto_promote_to_source=True,
    )
    result = app.start_autoloop({"rounds": rounds, "seed": seed, "evaluation_repeats": repeats})
    if not result.get("ok"):
        console.error(f"could not start loop: {result.get('message')}")
        return 1
    console.line(f"  eval repeats per task: {repeats}", "system")

    # Translate SIGTERM into KeyboardInterrupt so the graceful-stop path below handles both.
    def _on_term(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)

    console.status("Continuous self-improvement loop started. Press Ctrl-C to stop.", "success")
    console.blank()
    try:
        while True:
            time.sleep(10)
            state = app.state().get("autoloop", {})
            done = state.get("runs_completed", 0)
            edits = state.get("edits_promoted", 0)
            last = state.get("last_outcome") or "running…"
            console.line(
                f"  loop: {done} run(s) completed, {edits} edit(s) promoted — {last}",
                "accent" if edits else "system",
            )
            if not state.get("active") and state.get("error"):
                console.error(f"loop error: {state['error']}")
                return 1
    except KeyboardInterrupt:
        console.blank()
        console.status("stopping loop after the current run…", "warn")
        app.stop_autoloop()
        # Give the controller a moment to observe the stop flag.
        for _ in range(6):
            if not app.state().get("autoloop", {}).get("active"):
                break
            time.sleep(0.5)
        console.status("loop stopped.", "success")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="self-harness",
        description=(
            "SelfHarness — a coding agent that improves its own harness. "
            "Use GLM 5.2 or a headless local CLI backend (codex, agy, claude). "
            "Run with no command for an interactive menu, or `self-harness help` for a guide."
        ),
    )
    # Subcommand is OPTIONAL: bare `self-harness` opens the interactive home menu.
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("menu", help="open the interactive home menu (default when no command given)")

    help_parser = subparsers.add_parser("help", help="descriptive, plain-language guide to everything")
    help_parser.add_argument("topic", nargs="?", default=None, help="overview, code, loop, settings, key, ...")

    settings_parser = subparsers.add_parser(
        "settings", help="view or change configuration, including model/backend settings and API keys"
    )
    settings_parser.add_argument(
        "settings_args",
        nargs=argparse.REMAINDER,
        help="show | get <key> | set <key> <value> | unset <key> | path (no args = interactive editor)",
    )

    loop_parser = subparsers.add_parser(
        "loop", help="start the continuous self-improvement loop (foreground, or --background)"
    )
    loop_parser.add_argument(
        "loop_action",
        nargs="?",
        choices=("start", "status", "stop"),
        default="start",
        help="start (default), status (is it running + recent log), or stop a background loop",
    )
    loop_parser.add_argument("--rounds", type=int, default=1, help="evolution rounds per iteration")
    loop_parser.add_argument("--seed", type=int, default=0)
    loop_parser.add_argument(
        "--eval-repeats",
        type=int,
        default=None,
        help=f"times each task is attempted per evaluation (default: settings or {DEFAULT_LOOP_EVAL_REPEATS}); "
        "more repeats reduce noise and let the gate see graded improvements",
    )
    loop_parser.add_argument(
        "--background",
        "-b",
        action="store_true",
        help="run detached so it survives closing the terminal (manage with `loop status` / `loop stop`)",
    )

    save_parser = subparsers.add_parser(
        "save",
        help="save the current workspace as a resumable project",
    )
    save_parser.add_argument("--name", default=None, help="project name (default: directory name)")
    save_parser.add_argument("--notes", default="", help="what you were working on")
    save_parser.add_argument("--json", action="store_true", help="output JSON instead of text")

    resume_parser = subparsers.add_parser(
        "resume",
        help="resume a saved project",
    )
    resume_parser.add_argument("project", nargs="?", default=None, help="project number, ID, or name")
    resume_parser.add_argument("--json", action="store_true", help="output project details as JSON")
    resume_parser.add_argument("--list", action="store_true", help="list saved projects")

    projects_parser = subparsers.add_parser(
        "projects",
        help="list saved projects",
    )
    projects_parser.add_argument("--json", action="store_true", help="output JSON")

    demo_parser = subparsers.add_parser("demo", help="run the deterministic Self-Harness demo")
    demo_parser.add_argument("--rounds", type=int, default=3)
    demo_parser.add_argument("--seed", type=int, default=0)
    demo_parser.add_argument("--out", type=Path, default=Path("runs/demo"))
    demo_parser.add_argument("--evaluation-repeats", type=int, default=2)
    demo_parser.add_argument("--max-proposals", type=int, default=8)
    demo_parser.add_argument("--max-payload-bytes", type=int, default=600)
    demo_parser.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="exit non-zero if no proposal is accepted during the run",
    )
    demo_parser.add_argument(
        "--research-dir",
        default=None,
        help="enable research-radar integration, scanning this project dir for keywords",
    )
    demo_parser.add_argument(
        "--research-skip-scan",
        action="store_true",
        help="use cached research findings without triggering a new scan",
    )

    ui_parser = subparsers.add_parser("ui", help="serve the SelfHarness web operator interface")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--root", type=Path, default=Path("."))
    ui_parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ui_parser.add_argument(
        "--proposer",
        choices=("heuristic", "glm"),
        default=os.environ.get("SELF_HARNESS_UI_PROPOSER", "heuristic"),
        help="proposal backend for UI-started runs; glm requires ZAI_API_KEY",
    )
    ui_parser.add_argument(
        "--harness-state",
        type=Path,
        default=None,
        help="path to the evolving harness lineage file (default: <runs-dir>/harness_state.json)",
    )
    ui_parser.add_argument(
        "--max-steps",
        type=int,
        default=12,
        help="max agent tool-calling steps per attempt for agentic/dev-task runs",
    )
    ui_parser.add_argument(
        "--tool-timeout-seconds",
        type=int,
        default=30,
        help="per-command timeout for agentic/dev-task tool calls",
    )
    ui_parser.add_argument(
        "--codex-binary",
        default="codex",
        help="Codex CLI binary used as the agentic/dev-task judge",
    )
    ui_parser.add_argument(
        "--no-auto-promote",
        dest="auto_promote_to_source",
        action="store_false",
        help="do not auto-integrate reviewer-approved edits into harness.py (preview only via the API)",
    )
    ui_parser.set_defaults(auto_promote_to_source=True)
    ui_parser.add_argument(
        "--no-task-generation",
        dest="task_generation",
        action="store_false",
        help="disable adversarial task generation when the continuous loop is starved of real failures",
    )
    ui_parser.set_defaults(task_generation=True)
    ui_parser.add_argument(
        "--generation-guard",
        dest="generation_guard",
        action="store_true",
        help="quarantine generated tasks behind a solve+verify check before they enter the corpus",
    )
    ui_parser.set_defaults(generation_guard=False)

    code_parser = subparsers.add_parser(
        "code",
        help=(
            "interactive coding agent in the current directory; settings model can be glm-5.2, "
            "codex, agy, or claude"
        ),
    )
    code_parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="working directory the agent acts in (default: current directory)",
    )
    code_parser.add_argument(
        "--harness-state",
        type=Path,
        default=None,
        help="evolving harness lineage file to drive the agent (default: <root>/runs/harness_state.json)",
    )
    code_parser.add_argument(
        "--inbox-dir",
        type=Path,
        default=None,
        help="where harvested failing commands are dropped for the loop (default: <root>/runs/inbox)",
    )
    code_parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_CODE_MAX_STEPS,
        help=f"max agent tool-calling steps per turn (default {DEFAULT_CODE_MAX_STEPS})",
    )
    code_parser.add_argument(
        "--tool-timeout-seconds", type=int, default=30, help="per-command timeout for tool calls"
    )
    code_parser.add_argument(
        "--no-harvest",
        dest="harvest",
        action="store_false",
        help="do not harvest failing commands into the self-improvement inbox",
    )
    code_parser.set_defaults(harvest=True)
    code_parser.add_argument(
        "--resume",
        nargs="?",
        const="__latest__",
        default=None,
        metavar="SESSION_ID",
        help="resume a saved session (most recent if no id given) so prior context continues",
    )
    code_parser.add_argument(
        "--plain",
        action="store_true",
        help="disable the rich TUI (plain text output; auto-enabled when stdout is not a terminal)",
    )
    code_parser.add_argument(
        "--local-harness",
        action="store_true",
        help="use a per-project runs/ harness + inbox instead of the shared central one "
        "(by default `code` shares the central harness so the loop learns from your sessions)",
    )

    model_preflight_parser = subparsers.add_parser(
        "model-preflight",
        help="check a paper model backend (e.g. GLM 5.2) for reachability without claiming reproduction",
    )
    model_preflight_parser.add_argument(
        "--mode",
        choices=("dry-run", "replay", "live"),
        default="dry-run",
        help="dry-run (no network), replay (offline fixture), or live (contacts the provider)",
    )
    model_preflight_parser.add_argument(
        "--backend",
        choices=("all", "minimax", "qwen", "glm"),
        action="append",
        default=[],
        help="model backend(s) to check; repeatable, defaults to all",
    )
    model_preflight_parser.add_argument("--replay", type=Path, help="replay fixture file or directory")
    model_preflight_parser.add_argument("--today", help="optional YYYY-MM-DD stamp for deterministic reports")
    model_preflight_parser.add_argument("--out", type=Path, help="optional path to write the JSON report")
    model_preflight_parser.add_argument("--json", action="store_true", help="print the JSON report to stdout")

    audit_parser = subparsers.add_parser("audit-summary", help="summarize a Self-Harness audit directory")
    audit_parser.add_argument("path", type=Path)

    audit_verify_parser = subparsers.add_parser(
        "audit-verify",
        help="verify an audit directory's internal consistency without executing tasks",
    )
    audit_verify_parser.add_argument("path", type=Path)
    audit_verify_parser.add_argument("--json", action="store_true", help="print the structured verification report")
    audit_verify_parser.add_argument("--out", type=Path, help="write the structured verification report")
    audit_verify_parser.add_argument(
        "--lenient-migration",
        action="store_true",
        help="allow unknown migration_provenance fields when present",
    )

    audit_verify_live_parser = subparsers.add_parser(
        "audit-verify-live",
        help="verify audit integrity against signed live Harbor provenance without executing tasks",
    )
    audit_verify_live_parser.add_argument("--audit-dir", type=Path, required=True)
    audit_verify_live_parser.add_argument("--live-harbor-audit", type=Path, required=True)
    audit_verify_live_parser.add_argument("--provenance", type=Path, required=True)
    audit_verify_live_parser.add_argument("--provenance-signature", type=Path)
    audit_verify_live_parser.add_argument("--public-key", type=Path)
    audit_verify_live_parser.add_argument("--require-signature", action="store_true")
    audit_verify_live_parser.add_argument(
        "--json",
        action="store_true",
        help="print the structured verification report",
    )
    audit_verify_live_parser.add_argument("--out", type=Path, help="write the structured verification report")
    audit_verify_live_parser.add_argument(
        "--lenient-migration",
        action="store_true",
        help="allow unknown migration_provenance fields when present",
    )

    migrate_parser = subparsers.add_parser(
        "audit-migrate",
        help="copy an audit directory and upgrade its audit schema metadata",
    )
    migrate_parser.add_argument("source", type=Path)
    migrate_parser.add_argument("--out", type=Path, required=True, help="destination audit directory to create")
    migrate_parser.add_argument(
        "--target-schema-version",
        default=LATEST_AUDIT_SCHEMA_VERSION,
        help=f"target audit schema version; default: {LATEST_AUDIT_SCHEMA_VERSION}",
    )
    migrate_parser.add_argument(
        "--target-major",
        help="target the latest supported audit schema in this major version; requires explicit source schema_version",
    )
    migrate_parser.add_argument(
        "--allow-lossy",
        action="store_true",
        help="allow explicit drop-only lossy migration transforms from --transforms-json",
    )
    migrate_parser.add_argument(
        "--transforms-json",
        type=Path,
        help="operator-owned in-repo JSON transform registry; no plugin transforms are loaded",
    )

    trajectory_parser = subparsers.add_parser("audit-trajectory", help="write a paper-style audit trajectory JSONL")
    trajectory_parser.add_argument("path", type=Path)
    trajectory_parser.add_argument("--out", type=Path)
    trajectory_parser.add_argument("--pretty", action="store_true")

    inspect_harness_parser = subparsers.add_parser(
        "inspect-harness",
        help="write a paper-style retained harness edit report from an audit directory",
    )
    inspect_harness_parser.add_argument("path", type=Path)
    inspect_harness_parser.add_argument("--out", type=Path)
    inspect_harness_parser.add_argument("--json", action="store_true")
    inspect_harness_parser.add_argument("--pretty", action="store_true")

    local_parser = subparsers.add_parser(
        "local-demo",
        help="run JSON-defined local subprocess tasks; not a benchmark reproduction",
    )
    local_parser.add_argument("tasks_json", nargs="?", type=Path, help="legacy positional corpus path")
    local_parser.add_argument("--corpus", type=Path, help="versioned task corpus JSON path")
    local_parser.add_argument("--rounds", type=int, default=1)
    local_parser.add_argument("--seed", type=int, default=0)
    local_parser.add_argument("--out", type=Path, default=Path("runs/local-demo"))
    local_parser.add_argument("--evaluation-repeats", type=int, default=1)
    local_parser.add_argument("--max-proposals", type=int, default=8)
    local_parser.add_argument("--max-payload-bytes", type=int, default=600)
    local_parser.add_argument("--keep-workdir", action="store_true")
    local_trust_group = local_parser.add_mutually_exclusive_group()
    local_trust_group.add_argument(
        "--require-corpus-signature",
        type=Path,
        help="Ed25519 public key file required to verify the corpus signature",
    )
    local_trust_group.add_argument(
        "--require-corpus-keyring",
        type=Path,
        help="corpus keyring JSON required to verify the corpus signature against active trusted keys",
    )

    python_parser = subparsers.add_parser(
        "python-demo",
        help="run trusted in-process Python verifier tasks; not a benchmark reproduction",
    )
    python_parser.add_argument("corpus", type=Path)
    python_parser.add_argument(
        "--trust-verifier-module",
        required=True,
        help="trusted Python verifier module path or dotted module name; corpus JSON never selects executable code",
    )
    python_parser.add_argument("--verifier-symbol", default="verify")
    python_parser.add_argument("--setup-symbol", default="setup")
    python_parser.add_argument("--rounds", type=int, default=1)
    python_parser.add_argument("--seed", type=int, default=0)
    python_parser.add_argument("--out", type=Path, default=Path("runs/python-demo"))
    python_parser.add_argument("--evaluation-repeats", type=int, default=1)
    python_parser.add_argument("--max-proposals", type=int, default=8)
    python_parser.add_argument("--max-payload-bytes", type=int, default=600)
    python_parser.add_argument("--keep-workdir", action="store_true")
    python_trust_group = python_parser.add_mutually_exclusive_group()
    python_trust_group.add_argument(
        "--require-corpus-signature",
        type=Path,
        help="Ed25519 public key file required to verify the corpus signature",
    )
    python_trust_group.add_argument(
        "--require-corpus-keyring",
        type=Path,
        help="corpus keyring JSON required to verify the corpus signature against active trusted keys",
    )

    glm_agentic_parser = subparsers.add_parser(
        "glm-agentic-demo",
        help="run GLM 5.2 as a real tool-using agent, judged by the Codex CLI; NOT a benchmark reproduction",
    )
    glm_agentic_parser.add_argument("corpus", type=Path)
    glm_agentic_parser.add_argument(
        "--proposer",
        choices=("heuristic", "glm"),
        default="glm",
        help="harness-edit proposer; glm uses GLM 5.2 for both solving and proposing (within-model)",
    )
    glm_agentic_parser.add_argument("--rounds", type=int, default=2)
    glm_agentic_parser.add_argument("--seed", type=int, default=0)
    glm_agentic_parser.add_argument("--out", type=Path, default=Path("runs/glm-agentic-demo"))
    glm_agentic_parser.add_argument("--evaluation-repeats", type=int, default=1)
    glm_agentic_parser.add_argument("--max-proposals", type=int, default=8)
    glm_agentic_parser.add_argument("--max-payload-bytes", type=int, default=600)
    glm_agentic_parser.add_argument(
        "--max-steps", type=int, default=12, help="max agent tool-calling steps per attempt"
    )
    glm_agentic_parser.add_argument("--tool-timeout-seconds", type=int, default=30)
    glm_agentic_parser.add_argument("--codex-binary", default="codex", help="Codex CLI binary used as the judge")
    glm_agentic_parser.add_argument("--keep-workdir", action="store_true")
    glm_agentic_trust_group = glm_agentic_parser.add_mutually_exclusive_group()
    glm_agentic_trust_group.add_argument("--require-corpus-signature", type=Path)
    glm_agentic_trust_group.add_argument("--require-corpus-keyring", type=Path)

    http_parser = subparsers.add_parser(
        "http-demo",
        help="run trusted HTTP verifier tasks; not a benchmark reproduction",
    )
    http_parser.add_argument("corpus", type=Path)
    http_parser.add_argument(
        "--trust-verifier-url",
        required=True,
        help="trusted HTTP verifier URL supplied by the operator; corpus JSON never selects endpoints",
    )
    http_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    http_parser.add_argument("--tls-ca-bundle", type=Path, help="trusted CA bundle for HTTPS verifier requests")
    http_parser.add_argument("--tls-client-cert", type=Path, help="client certificate for HTTP verifier mTLS")
    http_parser.add_argument("--tls-client-key", type=Path, help="client private key for HTTP verifier mTLS")
    http_parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="extra HTTP header as KEY: VALUE; repeatable",
    )
    http_parser.add_argument("--rounds", type=int, default=1)
    http_parser.add_argument("--seed", type=int, default=0)
    http_parser.add_argument("--out", type=Path, default=Path("runs/http-demo"))
    http_parser.add_argument("--evaluation-repeats", type=int, default=1)
    http_parser.add_argument("--max-proposals", type=int, default=8)
    http_parser.add_argument("--max-payload-bytes", type=int, default=600)
    http_parser.add_argument("--keep-workdir", action="store_true")
    http_trust_group = http_parser.add_mutually_exclusive_group()
    http_trust_group.add_argument(
        "--require-corpus-signature",
        type=Path,
        help="Ed25519 public key file required to verify the corpus signature",
    )
    http_trust_group.add_argument(
        "--require-corpus-keyring",
        type=Path,
        help="corpus keyring JSON required to verify the corpus signature against active trusted keys",
    )

    container_parser = subparsers.add_parser(
        "container-demo",
        help="run trusted container verifier tasks; dry-run by default; not a benchmark reproduction",
    )
    container_parser.add_argument("corpus", type=Path)
    container_parser.add_argument("--trust-container-image", required=True)
    container_parser.add_argument("--trust-container-image-digest")
    container_parser.add_argument("--image-policy", type=Path, help="operator-owned image allowlist policy JSON")
    container_parser.add_argument("--require-image-digest", action="store_true")
    container_parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    container_parser.add_argument("--container-command", default="verify")
    container_parser.add_argument("--fixture-dir", type=Path)
    container_parser.add_argument("--docker-executable", default="docker")
    container_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    container_parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="container environment as KEY=VALUE; use --env-file for secrets",
    )
    container_parser.add_argument("--env-file", action="append", default=[], type=Path, help="container env-file path")
    container_parser.add_argument("--docker-config", type=Path, help="operator-owned Docker config directory")
    container_parser.add_argument("--keep-workdir", action="store_true")
    container_parser.add_argument("--skip-docker-preflight", action="store_true")
    container_parser.add_argument("--require-image-present", action="store_true")
    container_parser.add_argument("--rounds", type=int, default=1)
    container_parser.add_argument("--seed", type=int, default=0)
    container_parser.add_argument("--out", type=Path, default=Path("runs/container-demo"))
    container_parser.add_argument("--evaluation-repeats", type=int, default=1)
    container_parser.add_argument("--max-proposals", type=int, default=8)
    container_parser.add_argument("--max-payload-bytes", type=int, default=600)
    container_trust_group = container_parser.add_mutually_exclusive_group()
    container_trust_group.add_argument(
        "--require-corpus-signature",
        type=Path,
        help="Ed25519 public key file required to verify the corpus signature",
    )
    container_trust_group.add_argument(
        "--require-corpus-keyring",
        type=Path,
        help="corpus keyring JSON required to verify the corpus signature against active trusted keys",
    )

    validate_parser = subparsers.add_parser("validate-tasks", help="validate a versioned task corpus")
    validate_parser.add_argument("path", type=Path)
    validate_parser.add_argument("--allow-legacy", action="store_true")
    validate_parser.add_argument("--min-per-split", type=int, default=0)
    validate_parser.add_argument("--no-verify-checksum", action="store_true")
    validate_trust_group = validate_parser.add_mutually_exclusive_group()
    validate_trust_group.add_argument(
        "--require-corpus-signature",
        type=Path,
        help="Ed25519 public key file required to verify the corpus signature",
    )
    validate_trust_group.add_argument(
        "--require-corpus-keyring",
        type=Path,
        help="corpus keyring JSON required to verify the corpus signature against active trusted keys",
    )

    keygen_parser = subparsers.add_parser("corpus-keygen", help="generate an offline Ed25519 corpus signing keypair")
    keygen_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="private key output path; public key is PATH.pub",
    )
    keygen_parser.add_argument("--force", action="store_true", help="overwrite existing key files")
    _add_passphrase_args(keygen_parser)

    sign_parser = subparsers.add_parser("corpus-sign", help="sign a versioned task corpus")
    sign_parser.add_argument("--corpus", type=Path, required=True)
    sign_key_group = sign_parser.add_mutually_exclusive_group(required=True)
    sign_key_group.add_argument("--private-key", type=Path)
    sign_key_group.add_argument(
        "--external-signer",
        help="trusted external signer command; invoked without shell=True and fed the canonical corpus payload",
    )
    sign_parser.add_argument("--out", type=Path, required=True)
    sign_parser.add_argument("--signer-provider", default="external")
    sign_parser.add_argument("--key-id", default="")
    sign_parser.add_argument("--signer-timeout", type=float, default=DEFAULT_SIGNER_TIMEOUT_SECONDS)
    sign_parser.add_argument("--signer-max-output", type=int, default=DEFAULT_SIGNER_MAX_OUTPUT_BYTES)
    sign_parser.add_argument("--public-key", type=Path, help="expected signer public key for fingerprint validation")
    sign_parser.add_argument("--fingerprint", help="expected signer public-key fingerprint")
    _add_passphrase_args(sign_parser)

    fingerprint_parser = subparsers.add_parser("corpus-fingerprint", help="print an Ed25519 public key fingerprint")
    fingerprint_parser.add_argument("--public-key", type=Path, required=True)

    keyring_parser = subparsers.add_parser("corpus-keyring", help="manage trusted corpus public keys")
    keyring_subparsers = keyring_parser.add_subparsers(dest="keyring_command", required=True)

    keyring_init = keyring_subparsers.add_parser("init", help="create an empty corpus keyring")
    keyring_init.add_argument("--out", type=Path, required=True)
    keyring_init.add_argument("--force", action="store_true", help="overwrite an existing keyring")

    keyring_add = keyring_subparsers.add_parser("add", help="add a public key to a corpus keyring")
    keyring_add.add_argument("--keyring", type=Path, required=True)
    keyring_add.add_argument("--corpus-id", required=True)
    keyring_add.add_argument("--public-key", type=Path, required=True)
    keyring_add.add_argument(
        "--status",
        choices=[status.value for status in KeyringStatus],
        default=KeyringStatus.ACTIVE.value,
    )
    keyring_add.add_argument("--label", action="append", default=[], help="string label as KEY=VALUE; repeatable")

    keyring_status = keyring_subparsers.add_parser("set-status", help="update a trusted key status")
    keyring_status.add_argument("--keyring", type=Path, required=True)
    keyring_status.add_argument("--corpus-id", required=True)
    keyring_status.add_argument("--fingerprint", required=True)
    keyring_status.add_argument("--status", choices=[status.value for status in KeyringStatus], required=True)

    keyring_inspect = keyring_subparsers.add_parser("inspect", help="inspect trusted corpus public keys")
    keyring_inspect.add_argument("--keyring", type=Path, required=True)
    keyring_inspect.add_argument("--corpus-id")
    keyring_inspect.add_argument("--json", action="store_true")

    promotion_parser = subparsers.add_parser(
        "operator-promotion",
        help="manage operator-owned release policy promotion manifests",
    )
    promotion_subparsers = promotion_parser.add_subparsers(dest="promotion_command", required=True)

    promotion_init = promotion_subparsers.add_parser("init", help="create an empty promotion manifest")
    promotion_init.add_argument("--manifest", type=Path, required=True)
    promotion_init.add_argument("--force", action="store_true", help="overwrite an existing manifest")

    promotion_add = promotion_subparsers.add_parser("add", help="add a policy file to a promotion manifest")
    promotion_add.add_argument("--manifest", type=Path, required=True)
    promotion_add.add_argument("--name", required=True)
    promotion_add.add_argument("--kind", choices=sorted(POLICY_KINDS), required=True)
    promotion_add.add_argument("--file", type=Path, required=True)
    promotion_add.add_argument("--status", choices=sorted(PROMOTION_STATUSES), default="draft")

    promotion_status = promotion_subparsers.add_parser("set-status", help="advance a promotion entry lifecycle")
    promotion_status.add_argument("--manifest", type=Path, required=True)
    promotion_status.add_argument("--name", required=True)
    promotion_status.add_argument("--status", choices=sorted(PROMOTION_STATUSES), required=True)

    promotion_sign = promotion_subparsers.add_parser("sign", help="write an Ed25519 promotion signature sidecar")
    promotion_sign.add_argument("--manifest", type=Path, required=True)
    promotion_sign.add_argument("--out", type=Path, required=True)
    promotion_sign_key_group = promotion_sign.add_mutually_exclusive_group(required=True)
    promotion_sign_key_group.add_argument("--private-key", type=Path)
    promotion_sign_key_group.add_argument(
        "--external-signer",
        help="trusted external signer command; invoked without shell=True and fed canonical manifest bytes",
    )
    promotion_sign.add_argument("--provider", default="local-pem")
    promotion_sign.add_argument("--key-id", default="")
    promotion_sign.add_argument("--signer-timeout", type=float, default=DEFAULT_SIGNER_TIMEOUT_SECONDS)
    promotion_sign.add_argument("--signer-max-output", type=int, default=DEFAULT_SIGNER_MAX_OUTPUT_BYTES)
    promotion_sign.add_argument("--public-key", type=Path, help="expected signer public key for fingerprint validation")
    promotion_sign.add_argument("--fingerprint", help="expected signer public-key fingerprint")
    _add_passphrase_args(promotion_sign)

    promotion_verify = promotion_subparsers.add_parser("verify", help="verify a promotion manifest and signature")
    promotion_verify.add_argument("--manifest", type=Path, required=True)
    promotion_verify.add_argument("--signature", type=Path)
    promotion_verify.add_argument("--trusted-public-key", type=Path)
    promotion_verify.add_argument("--out", type=Path)
    promotion_verify.add_argument("--json", action="store_true")

    capture_manifest_parser = subparsers.add_parser(
        "capture-manifest",
        help="verify operator live-evidence capture manifests and diff them against bundles",
    )
    capture_manifest_subparsers = capture_manifest_parser.add_subparsers(
        dest="capture_manifest_command",
        required=True,
    )

    capture_manifest_build = capture_manifest_subparsers.add_parser("build", help="build a capture manifest")
    capture_manifest_build.add_argument("--manifest-id", required=True)
    capture_manifest_build.add_argument("--bundle-id", required=True)
    capture_manifest_build.add_argument("--operator-label", required=True)
    capture_manifest_build.add_argument("--created-at", required=True)
    capture_manifest_build.add_argument("--run-id", required=True)
    capture_manifest_build.add_argument("--mode", default="live")
    capture_manifest_build.add_argument("--benchmark-protocol", default="terminal-bench@2.0")
    capture_manifest_build.add_argument("--model-backend", action="append", required=True)
    capture_manifest_build.add_argument("--evaluator", required=True)
    capture_manifest_build.add_argument("--tool-set", required=True)
    capture_manifest_build.add_argument("--tool-budget-json", required=True)
    capture_manifest_build.add_argument("--outbound-bandwidth-cap-bps", type=int, required=True)
    capture_manifest_build.add_argument("--mirrored-resource", action="append", required=True)
    capture_manifest_build.add_argument("--source-provider", required=True)
    capture_manifest_build.add_argument("--source-captured-after", required=True)
    capture_manifest_build.add_argument("--source-captured-before", required=True)
    capture_manifest_build.add_argument("--signing-provider", required=True)
    capture_manifest_build.add_argument("--key-id", default="")
    capture_manifest_build.add_argument("--fingerprint")
    capture_manifest_build.add_argument("--planned-artifact", action="append", default=[], help="CLASS=PATH")
    capture_manifest_build.add_argument("--entry-source", action="append", default=[], help="CLASS:KEY=VALUE")
    capture_manifest_build.add_argument("--entry-note", action="append", default=[], help="CLASS=TEXT")
    capture_manifest_build.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    capture_manifest_build.add_argument("--out", type=Path, default=Path("dist/self-harness-capture-manifest.json"))
    capture_manifest_build.add_argument("--strict-shapes", dest="strict_shapes", action="store_true", default=True)
    capture_manifest_build.add_argument("--no-strict-shapes", dest="strict_shapes", action="store_false")
    capture_manifest_build.add_argument("--json", action="store_true")

    capture_manifest_verify = capture_manifest_subparsers.add_parser("verify", help="verify a capture manifest")
    capture_manifest_verify.add_argument("--manifest", type=Path, required=True)
    capture_manifest_verify.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    capture_manifest_verify.add_argument("--signature", type=Path)
    capture_manifest_verify.add_argument("--public-key")
    capture_manifest_verify.add_argument("--require-signature", action="store_true")
    capture_manifest_verify.add_argument("--out", type=Path)
    capture_manifest_verify.add_argument("--json", action="store_true")

    capture_manifest_diff = capture_manifest_subparsers.add_parser(
        "diff",
        help="diff a capture manifest against a reproduction evidence bundle",
    )
    capture_manifest_diff.add_argument("--manifest", type=Path, required=True)
    capture_manifest_diff.add_argument("--bundle", type=Path, required=True)
    capture_manifest_diff.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    capture_manifest_diff.add_argument("--manifest-signature", type=Path)
    capture_manifest_diff.add_argument("--bundle-signature", type=Path)
    capture_manifest_diff.add_argument("--require-manifest-signature", action="store_true")
    capture_manifest_diff.add_argument("--require-bundle-signature", action="store_true")
    capture_manifest_diff.add_argument("--out", type=Path)
    capture_manifest_diff.add_argument("--json", action="store_true")

    capture_manifest_rehearse = capture_manifest_subparsers.add_parser(
        "rehearse",
        help="rehearse a capture manifest against synthetic offline artifacts",
    )
    capture_manifest_rehearse.add_argument("--manifest", type=Path, required=True)
    capture_manifest_rehearse.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    capture_manifest_rehearse.add_argument(
        "--readiness-matrix-result",
        type=Path,
        default=Path("dist/self-harness-readiness-matrix.json"),
    )
    capture_manifest_rehearse.add_argument("--manifest-signature", type=Path)
    capture_manifest_rehearse.add_argument("--public-key")
    capture_manifest_rehearse.add_argument("--require-manifest-signature", action="store_true")
    capture_manifest_rehearse.add_argument("--rehearsal-id", required=True)
    capture_manifest_rehearse.add_argument("--operator-label", required=True)
    capture_manifest_rehearse.add_argument("--out-dir", type=Path, required=True)
    capture_manifest_rehearse.add_argument("--report-out", type=Path)
    bundle_signer = capture_manifest_rehearse.add_mutually_exclusive_group()
    bundle_signer.add_argument("--bundle-private-key", type=Path)
    bundle_signer.add_argument("--bundle-external-signer")
    capture_manifest_rehearse.add_argument("--bundle-public-key", type=Path)
    capture_manifest_rehearse.add_argument("--bundle-fingerprint")
    capture_manifest_rehearse.add_argument("--bundle-signature-out", type=Path)
    capture_manifest_rehearse.add_argument("--bundle-signature-provider")
    capture_manifest_rehearse.add_argument("--bundle-key-id")
    capture_manifest_rehearse.add_argument("--require-bundle-signature", action="store_true")
    capture_manifest_rehearse.add_argument("--json", action="store_true")

    capture_extract_parser = subparsers.add_parser(
        "capture-extract",
        help="extract required live evidence artifact JSON from operator-captured raw files",
    )
    capture_extract_parser.add_argument(
        "--class",
        dest="artifact_class",
        choices=sorted(EXTRACTABLE_ARTIFACT_CLASSES),
        required=True,
    )
    capture_extract_parser.add_argument("--out", type=Path)
    capture_extract_parser.add_argument("--json", action="store_true")
    capture_extract_parser.add_argument("--capture-run-id")
    capture_extract_parser.add_argument("--harbor-discovery-result", type=Path)
    capture_extract_parser.add_argument("--harbor-version")
    capture_extract_parser.add_argument("--image-policy", type=Path)
    capture_extract_parser.add_argument("--model-backend-preflight-result", type=Path)
    capture_extract_parser.add_argument("--network-controls", type=Path)
    capture_extract_parser.add_argument("--harbor-run-dir", type=Path)
    capture_extract_parser.add_argument("--capture-envelope", type=Path)
    capture_extract_parser.add_argument("--attempts-jsonl", type=Path)
    capture_extract_parser.add_argument("--split-manifest-result", type=Path)
    capture_extract_parser.add_argument("--fixed-protocol-declaration", type=Path)
    capture_extract_parser.add_argument("--fixed-protocol-result", type=Path)
    capture_extract_parser.add_argument("--fixed-protocol-sha256")
    capture_extract_parser.add_argument("--proposer-request-log", type=Path)
    capture_extract_parser.add_argument("--proposer-request-log-artifact", type=Path)
    capture_extract_parser.add_argument("--proposer-context-log", type=Path)
    capture_extract_parser.add_argument("--audit-run-dir", type=Path)
    capture_extract_parser.add_argument("--proposer-backend-map", action="append", default=[])

    capture_admit_parser = subparsers.add_parser(
        "capture-admit",
        help="admit post-capture evidence into a verified local bundle report",
    )
    capture_admit_parser.add_argument("--admission-id", required=True)
    capture_admit_parser.add_argument("--operator-label", required=True)
    capture_admit_parser.add_argument("--created-at", required=True)
    capture_admit_parser.add_argument("--bundle-id", required=True)
    capture_admit_parser.add_argument("--source-provider", required=True)
    capture_admit_parser.add_argument("--source-captured-at", required=True)
    capture_admit_parser.add_argument("--source-url")
    capture_admit_parser.add_argument("--artifact-dir", type=Path, required=True)
    capture_admit_parser.add_argument("--bundle-out", type=Path)
    capture_admit_parser.add_argument("--raw-input", action="append", default=[])
    capture_admit_parser.add_argument("--raw-flag", action="append", default=[])
    capture_admit_parser.add_argument("--artifact", action="append", default=[])
    capture_admit_parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    capture_admit_parser.add_argument("--readiness-matrix-result", type=Path)
    capture_admit_parser.add_argument("--skip-readiness", action="store_true")
    capture_admit_parser.add_argument("--bundle-signature", type=Path)
    capture_admit_parser.add_argument("--bundle-public-key")
    capture_admit_parser.add_argument("--require-bundle-signature", action="store_true")
    capture_admit_parser.add_argument("--out", type=Path)

    attestation_parser = subparsers.add_parser(
        "verify-attestation",
        help="verify local release attestation material without contacting live trust services",
    )
    attestation_parser.add_argument("--bundle", type=Path, required=True)
    attestation_parser.add_argument("--material", type=Path, required=True)
    attestation_parser.add_argument("--trust-root", type=Path, required=True)
    attestation_parser.add_argument("--backend", choices=["structural", "sigstore"], default="structural")
    attestation_parser.add_argument("--out", type=Path)
    attestation_parser.add_argument("--json", action="store_true")

    diff_parser = subparsers.add_parser("audit-diff", help="compare two audit directories byte-for-byte")
    diff_parser.add_argument("left", type=Path)
    diff_parser.add_argument("right", type=Path)
    diff_parser.add_argument("--json", action="store_true")

    report_parser = subparsers.add_parser(
        "benchmark-report",
        help="build a paper-style benchmark report from audit directories",
    )
    report_parser.add_argument(
        "--audit-dir",
        action="append",
        required=True,
        help="model_label:path/to/audit directory; repeat for each model",
    )
    report_parser.add_argument("--out", type=Path, required=True)

    inspect_parser = subparsers.add_parser("harbor-inspect", help="inspect a preserved Harbor run directory")
    inspect_parser.add_argument("run_dir", type=Path)
    inspect_parser.add_argument("--out", type=Path)
    inspect_parser.add_argument("--json", action="store_true")

    ingest_parser = subparsers.add_parser("harbor-ingest", help="ingest preserved Harbor artifacts into an audit")
    ingest_parser.add_argument("run_dir", type=Path)
    ingest_parser.add_argument("--manifest", type=Path, required=True)
    ingest_parser.add_argument("--out", type=Path, required=True)
    ingest_parser.add_argument("--dataset", default="terminal-bench@2.0")

    tb_parser = subparsers.add_parser(
        "terminal-bench",
        help="run the experimental Terminal-Bench/Harbor adapter scaffold; not a reproduction",
    )
    tb_parser.add_argument("--dataset", default="terminal-bench@2.0")
    tb_parser.add_argument("--manifest", type=Path, required=True)
    tb_parser.add_argument("--fixture-dir", type=Path)
    tb_parser.add_argument("--corpus-cache", type=Path)
    tb_parser.add_argument("--harbor-executable", default="harbor")
    tb_parser.add_argument("--docker-executable", default="docker")
    tb_parser.add_argument("--agent", default="deepagent")
    tb_parser.add_argument("--model", default="anthropic/claude-haiku-4-5")
    tb_parser.add_argument("--n-concurrent", type=int, default=1)
    tb_parser.add_argument("--env")
    tb_parser.add_argument("--keep-run-dir", type=Path)
    tb_parser.add_argument("--image-policy", type=Path, help="operator-owned Harbor image allowlist policy JSON")
    tb_parser.add_argument("--trust-container-image")
    tb_parser.add_argument("--trust-container-image-digest")
    tb_parser.add_argument("--require-image-digest", action="store_true")
    tb_parser.add_argument("--require-uv", action="store_true")
    tb_parser.add_argument(
        "--skip-docker-preflight",
        action="store_true",
        help="skip Docker daemon preflight checks for controlled test harnesses",
    )
    tb_parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    tb_parser.add_argument("--rounds", type=int, default=1)
    tb_parser.add_argument("--seed", type=int, default=0)
    tb_parser.add_argument("--out", type=Path, default=Path("runs/terminal-bench-dry-run"))
    tb_parser.add_argument("--evaluation-repeats", type=int, default=1)
    tb_parser.add_argument("--max-proposals", type=int, default=8)
    tb_parser.add_argument("--max-payload-bytes", type=int, default=600)

    preflight_parser = subparsers.add_parser(
        "terminal-bench-preflight",
        help="check whether live Terminal-Bench/Harbor execution can run",
    )
    preflight_parser.add_argument("--dataset", default="terminal-bench@2.0")
    preflight_parser.add_argument("--manifest", type=Path, required=True)
    preflight_parser.add_argument("--corpus-cache", type=Path)
    preflight_parser.add_argument("--out", type=Path, default=Path("runs/tb-preflight"))
    preflight_parser.add_argument("--harbor-executable", default="harbor")
    preflight_parser.add_argument("--docker-executable", default="docker")
    preflight_parser.add_argument("--require-uv", action="store_true")
    preflight_parser.add_argument("--skip-docker-preflight", action="store_true")
    preflight_parser.add_argument("--json", action="store_true")

    capture_parser = subparsers.add_parser(
        "terminal-bench-capture",
        help="capture one live Terminal-Bench/Harbor task into a replay fixture",
    )
    capture_parser.add_argument("--dataset", default="terminal-bench@2.0")
    capture_parser.add_argument("--manifest", type=Path, required=True)
    capture_parser.add_argument("--task", required=True)
    capture_parser.add_argument("--fixture-out", type=Path, required=True)
    capture_parser.add_argument("--corpus-cache", type=Path)
    capture_parser.add_argument("--harbor-executable", default="harbor")
    capture_parser.add_argument("--docker-executable", default="docker")
    capture_parser.add_argument("--skip-docker-preflight", action="store_true")

    args = parser.parse_args(argv)

    # Bare `self-harness` (or `menu`) → interactive home menu.
    if args.command is None or args.command == "menu":
        from self_harness import cli_home

        return cli_home.run_home()
    if args.command == "help":
        from self_harness import cli_home

        return cli_home.print_help(args.topic)
    if args.command == "settings":
        from self_harness import cli_home

        return cli_home.run_settings(list(args.settings_args or []))
    if args.command == "loop":
        from self_harness import loop_daemon

        if args.loop_action == "status":
            return loop_daemon.status()
        if args.loop_action == "stop":
            return loop_daemon.stop_background()
        if args.background:
            return loop_daemon.start_background(rounds=args.rounds, seed=args.seed, eval_repeats=args.eval_repeats)
        return run_loop_default(rounds=args.rounds, seed=args.seed, eval_repeats=args.eval_repeats)
    if args.command == "save":
        return _run_save(args)
    if args.command == "resume":
        return _run_resume(args)
    if args.command == "projects":
        return _run_projects(args)
    if args.command == "demo":
        return _run_demo(
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
            fail_on_empty=args.fail_on_empty,
            research_dir=getattr(args, "research_dir", None),
            research_skip_scan=getattr(args, "research_skip_scan", False),
        )
    if args.command == "ui":
        from self_harness.ui import serve_ui

        return serve_ui(
            host=args.host,
            port=args.port,
            root=args.root,
            runs_dir=args.runs_dir,
            proposer_mode=args.proposer,
            harness_state=args.harness_state,
            max_steps=args.max_steps,
            tool_timeout_seconds=args.tool_timeout_seconds,
            codex_binary=args.codex_binary,
            auto_promote_to_source=args.auto_promote_to_source,
            task_generation=args.task_generation,
            generation_guard=args.generation_guard,
        )
    if args.command == "code":
        return _run_code(
            root=args.root,
            harness_state=args.harness_state,
            inbox_dir=args.inbox_dir,
            max_steps=args.max_steps,
            tool_timeout_seconds=args.tool_timeout_seconds,
            harvest=args.harvest,
            resume=args.resume,
            plain=args.plain,
            local_harness=args.local_harness,
        )
    if args.command == "model-preflight":
        return _run_model_preflight(
            mode=args.mode,
            backend_ids=args.backend,
            replay=args.replay,
            today=args.today,
            out_path=args.out,
            json_output=args.json,
        )
    if args.command == "audit-summary":
        print(stable_json_dumps(summarize_audit_run(args.path)))
        return 0
    if args.command == "audit-verify":
        return _run_audit_verify(
            args.path,
            json_output=args.json,
            out_path=args.out,
            strict_migration=not args.lenient_migration,
        )
    if args.command == "audit-verify-live":
        return _run_audit_verify_live(
            args.audit_dir,
            live_harbor_audit=args.live_harbor_audit,
            provenance=args.provenance,
            provenance_signature=args.provenance_signature,
            public_key=args.public_key,
            require_signature=args.require_signature,
            json_output=args.json,
            out_path=args.out,
            strict_migration=not args.lenient_migration,
        )
    if args.command == "audit-migrate":
        return _run_audit_migrate(
            args.source,
            out_path=args.out,
            target_schema_version=args.target_schema_version,
            target_major=args.target_major,
            allow_lossy=args.allow_lossy,
            transforms_json=args.transforms_json,
        )
    if args.command == "audit-trajectory":
        return _run_audit_trajectory(args.path, out_path=args.out, pretty=args.pretty)
    if args.command == "inspect-harness":
        return _run_inspect_harness(args.path, out_path=args.out, json_output=args.json, pretty=args.pretty)
    if args.command == "local-demo":
        corpus_path, allow_legacy = _resolve_local_corpus_path(parser, args.tasks_json, args.corpus)
        return _run_local_demo(
            corpus_path=corpus_path,
            allow_legacy=allow_legacy,
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
            keep_workdir=args.keep_workdir,
            signature_key=args.require_corpus_signature,
            keyring_path=args.require_corpus_keyring,
        )
    if args.command == "python-demo":
        return _run_python_demo(
            corpus_path=args.corpus,
            trusted_module=args.trust_verifier_module,
            verifier_symbol=args.verifier_symbol,
            setup_symbol=args.setup_symbol,
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
            keep_workdir=args.keep_workdir,
            signature_key=args.require_corpus_signature,
            keyring_path=args.require_corpus_keyring,
        )
    if args.command == "glm-agentic-demo":
        return _run_glm_agentic_demo(
            corpus_path=args.corpus,
            proposer_mode=args.proposer,
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
            max_steps=args.max_steps,
            tool_timeout_seconds=args.tool_timeout_seconds,
            codex_binary=args.codex_binary,
            keep_workdir=args.keep_workdir,
            signature_key=args.require_corpus_signature,
            keyring_path=args.require_corpus_keyring,
        )
    if args.command == "http-demo":
        return _run_http_demo(
            corpus_path=args.corpus,
            trusted_url=args.trust_verifier_url,
            timeout_seconds=args.timeout_seconds,
            tls_ca_bundle=args.tls_ca_bundle,
            tls_client_cert=args.tls_client_cert,
            tls_client_key=args.tls_client_key,
            header_args=args.header,
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
            keep_workdir=args.keep_workdir,
            signature_key=args.require_corpus_signature,
            keyring_path=args.require_corpus_keyring,
        )
    if args.command == "container-demo":
        return _run_container_demo(
            corpus_path=args.corpus,
            image=args.trust_container_image,
            image_digest=args.trust_container_image_digest,
            image_policy_path=args.image_policy,
            require_image_digest=args.require_image_digest,
            mode=args.mode,
            container_command=args.container_command,
            fixture_dir=args.fixture_dir,
            docker_executable=args.docker_executable,
            timeout_seconds=args.timeout_seconds,
            env_args=args.env,
            env_file_args=args.env_file,
            docker_config_dir=args.docker_config,
            keep_workdir=args.keep_workdir,
            skip_docker_preflight=args.skip_docker_preflight,
            require_image_present=args.require_image_present,
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
            signature_key=args.require_corpus_signature,
            keyring_path=args.require_corpus_keyring,
        )
    if args.command == "validate-tasks":
        return _run_validate_tasks(
            path=args.path,
            allow_legacy=args.allow_legacy,
            min_per_split=args.min_per_split,
            verify_checksum=not args.no_verify_checksum,
            signature_key=args.require_corpus_signature,
            keyring_path=args.require_corpus_keyring,
        )
    if args.command == "corpus-keygen":
        try:
            passphrase = _resolve_passphrase_args(args)
        except CorpusSigningError as exc:
            return _run_corpus_signing_error(exc)
        return _run_corpus_keygen(
            args.out,
            force=args.force,
            passphrase=passphrase,
        )
    if args.command == "corpus-sign":
        if args.external_signer is not None:
            if args.passphrase is not None or args.passphrase_file is not None or args.passphrase_env is not None:
                return _run_corpus_signing_error(
                    CorpusSigningError("passphrase arguments are only valid with --private-key")
                )
            passphrase = None
        else:
            try:
                passphrase = _resolve_passphrase_args(args)
            except CorpusSigningError as exc:
                return _run_corpus_signing_error(exc)
        return _run_corpus_sign(
            args.corpus,
            private_key=args.private_key,
            out_path=args.out,
            passphrase=passphrase,
            external_signer=args.external_signer,
            signer_provider=args.signer_provider,
            signer_key_id=args.key_id,
            signer_timeout_seconds=args.signer_timeout,
            signer_max_output_bytes=args.signer_max_output,
            expected_public_key=args.public_key,
            expected_fingerprint=args.fingerprint,
        )
    if args.command == "corpus-fingerprint":
        return _run_corpus_fingerprint(args.public_key)
    if args.command == "corpus-keyring":
        return _run_corpus_keyring(args)
    if args.command == "operator-promotion":
        return _run_operator_promotion(args)
    if args.command == "capture-manifest":
        return _run_capture_manifest(args)
    if args.command == "capture-extract":
        return _run_capture_extract(args)
    if args.command == "capture-admit":
        return _run_capture_admit(args)
    if args.command == "verify-attestation":
        return _run_verify_attestation(args)
    if args.command == "audit-diff":
        return _run_audit_diff(args.left, args.right, json_output=args.json)
    if args.command == "benchmark-report":
        return _run_benchmark_report(args.audit_dir, args.out)
    if args.command == "harbor-inspect":
        return _run_harbor_inspect(args.run_dir, out_path=args.out, json_output=args.json)
    if args.command == "harbor-ingest":
        return _run_harbor_ingest(args.run_dir, manifest=args.manifest, out_dir=args.out, dataset=args.dataset)
    if args.command == "terminal-bench":
        return _run_terminal_bench(
            dataset=args.dataset,
            manifest=args.manifest,
            fixture_dir=args.fixture_dir,
            corpus_cache=args.corpus_cache,
            harbor_executable=args.harbor_executable,
            docker_executable=args.docker_executable,
            agent=args.agent,
            model=args.model,
            n_concurrent=args.n_concurrent,
            cloud_env=args.env,
            keep_run_dir=args.keep_run_dir,
            image_policy_path=args.image_policy,
            trusted_image=args.trust_container_image,
            trusted_image_digest=args.trust_container_image_digest,
            require_image_digest=args.require_image_digest,
            require_uv=args.require_uv,
            require_docker=not args.skip_docker_preflight,
            mode=args.mode,
            rounds=args.rounds,
            seed=args.seed,
            out_dir=args.out,
            evaluation_repeats=args.evaluation_repeats,
            max_proposals=args.max_proposals,
            max_payload_bytes=args.max_payload_bytes,
        )
    if args.command == "terminal-bench-preflight":
        return _run_terminal_bench_preflight(
            dataset=args.dataset,
            manifest=args.manifest,
            corpus_cache=args.corpus_cache,
            out_dir=args.out,
            harbor_executable=args.harbor_executable,
            docker_executable=args.docker_executable,
            require_uv=args.require_uv,
            require_docker=not args.skip_docker_preflight,
            json_output=args.json,
        )
    if args.command == "terminal-bench-capture":
        return _run_terminal_bench_capture(
            dataset=args.dataset,
            manifest=args.manifest,
            task_id=args.task,
            fixture_out=args.fixture_out,
            corpus_cache=args.corpus_cache,
            harbor_executable=args.harbor_executable,
            docker_executable=args.docker_executable,
            require_docker=not args.skip_docker_preflight,
        )
    raise AssertionError(f"unhandled command: {args.command}")


def _add_passphrase_args(parser: argparse.ArgumentParser) -> None:
    passphrase_group = parser.add_mutually_exclusive_group()
    passphrase_group.add_argument(
        "--passphrase",
        help="literal private-key passphrase; prefer --passphrase-env or --passphrase-file in CI",
    )
    passphrase_group.add_argument("--passphrase-file", type=Path, help="file containing the private-key passphrase")
    passphrase_group.add_argument("--passphrase-env", help="environment variable containing the private-key passphrase")


def _resolve_passphrase_args(args: argparse.Namespace) -> str | None:
    return _resolve_passphrase(
        literal=args.passphrase,
        passphrase_file=args.passphrase_file,
        passphrase_env=args.passphrase_env,
    )


def _resolve_passphrase(
    *,
    literal: str | None,
    passphrase_file: Path | None,
    passphrase_env: str | None,
) -> str | None:
    if literal is not None:
        return _require_passphrase(literal, "private key passphrase")
    if passphrase_file is not None:
        try:
            return _require_passphrase(
                passphrase_file.read_text(encoding="utf-8").rstrip("\r\n"),
                "private key passphrase file",
            )
        except OSError as exc:
            raise CorpusSigningError("private key passphrase file could not be read") from exc
    if passphrase_env is not None:
        value = os.environ.get(passphrase_env)
        if value is None:
            raise CorpusSigningError(f"private key passphrase environment variable is not set: {passphrase_env}")
        return _require_passphrase(value, "private key passphrase environment variable")
    return None


def _require_passphrase(value: str, label: str) -> str:
    if not value:
        raise CorpusSigningError(f"{label} must be non-empty")
    return value


def _run_save(args: argparse.Namespace) -> int:
    """Save the current workspace as a project snapshot."""
    name = args.name or Path.cwd().name
    from self_harness.loop_paths import central_runs_dir

    harness_state: dict[str, Any] | None = None
    runs_dir = central_runs_dir()
    if runs_dir:
        state_file = runs_dir / "harness_state.json"
        if state_file.is_file():
            try:
                harness_state = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    corpus_path: str | None = None
    for candidate in [Path.cwd() / "examples" / "agentic_corpus.json",
                      Path.cwd() / "examples" / "local_corpus.json"]:
        if candidate.is_file():
            corpus_path = str(candidate)
            break

    project = save_project(
        name=name,
        working_dir=str(Path.cwd()),
        corpus_path=corpus_path,
        harness_state=harness_state,
        notes=args.notes,
    )

    # Commit, merge, and push to GitHub
    sync = git_sync(
        str(Path.cwd()),
        f"save project: {name}",
    )

    if args.json:
        print(json.dumps({
            "id": project.id,
            "name": project.name,
            "working_dir": project.working_dir,
            "saved_at": project.saved_at,
            "git_committed": sync.committed,
            "git_pushed": sync.pushed,
            "git_merged": sync.merged,
            "git_errors": sync.errors,
        }))
    else:
        print(f"Saved '{name}'")
        print(f"  directory: {project.working_dir}")
        print(f"  resume:    self-harness resume {project.id.split('-')[-1]}")
        if sync.committed:
            sha = sync.commit_sha or "?"
            print(f"  git:       committed {sha}")
        if sync.merged:
            print(f"  git:       merged {', '.join(sync.remote_ahead)}")
        if sync.pushed:
            print("  git:       pushed to origin")
        if sync.errors:
            for err in sync.errors:
                print(f"  git:       {err}", file=sys.stderr)
    return 0


def _run_resume(args: argparse.Namespace) -> int:
    """Resume a saved project."""
    if args.list or args.project is None:
        return _run_projects(args)

    project = load_project(args.project)
    if project is None:
        print(f"No project matching '{args.project}'", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "id": project.id,
            "name": project.name,
            "working_dir": project.working_dir,
            "corpus_path": project.corpus_path,
            "rounds_completed": project.rounds_completed,
            "notes": project.notes,
            "saved_at": project.saved_at,
        }))
    else:
        print(f"Project: {project.name}")
        print(f"  directory: {project.working_dir}")
        print(f"  saved:    {project.saved_at}")
        if project.notes:
            print(f"  notes:    {project.notes}")
        print()
        print("To resume:")
        print(f"  cd {project.working_dir}")
        print("  self-harness")
    return 0


def _run_projects(args: argparse.Namespace) -> int:
    """List saved projects."""
    projects = list_projects()
    if args.json:
        print(json.dumps([
            {"id": p.id, "name": p.name, "working_dir": p.working_dir,
             "saved_at": p.saved_at, "rounds_completed": p.rounds_completed,
             "notes": p.notes}
            for p in projects
        ]))
    elif not projects:
        print("No saved projects. Run 'self-harness save' to create one.")
    else:
        print(f"{'#':>3}  {'Name':<30} {'Directory':<20} {'Saved':<18} Notes")
        print("-" * 90)
        for i, p in enumerate(projects, 1):
            name = p.name[:30]
            directory = Path(p.working_dir).name[:20] if p.working_dir else "?"
            notes = (p.notes[:30] + "…") if len(p.notes) > 30 else p.notes
            print(f"{i:>3}  {name:<30} {directory:<20} {p.saved_at:<18} {notes}")
        print()
        print("Resume:  self-harness resume <#>")
        print("Delete:  self-harness projects --json | jq 'del(.[])'  # or use the menu")
    return 0


def _run_demo(
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
    fail_on_empty: bool,
    research_dir: str | None = None,
    research_skip_scan: bool = False,
) -> int:
    config = EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(
            max_proposals=max_proposals,
            max_payload_bytes=max_payload_bytes,
        ),
        fail_on_empty=fail_on_empty,
    )
    research_integrator = None
    if research_dir:
        research_integrator = ResearchIntegrator(
            ResearchConfig(
                enabled=True,
                project_dir=research_dir,
                skip_scan=research_skip_scan,
            )
        )

    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=DeterministicRunner(seed=seed),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=config,
        research_integrator=research_integrator,
    )
    summaries = engine.run()

    print("Self-Harness demo complete")
    print("round  before_in  before_out  proposals  accepted  rejected  after_in  after_out")
    for summary in summaries:
        print(
            f"{summary.round:<5}  "
            f"{summary.baseline_held_in:<9}  "
            f"{summary.baseline_held_out:<10}  "
            f"{summary.proposals:<9}  "
            f"{summary.accepted:<8}  "
            f"{summary.rejected:<8}  "
            f"{summary.after_held_in:<8}  "
            f"{summary.after_held_out:<9}"
        )
    print(f"Artifacts: {out_dir}")
    if config.fail_on_empty and not any(summary.accepted for summary in summaries):
        return 2
    return 0


def _run_local_demo(
    corpus_path: Path,
    allow_legacy: bool,
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
    keep_workdir: bool,
    signature_key: Path | None,
    keyring_path: Path | None,
) -> int:
    config = EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(
            max_proposals=max_proposals,
            max_payload_bytes=max_payload_bytes,
        ),
        model_id="local-subprocess-runner",
    )
    adapter = LocalSubprocessTaskAdapter(keep_workdir=keep_workdir)
    try:
        corpus, _trusted_entry = _load_trusted_corpus(
            corpus_path,
            allow_legacy=allow_legacy,
            signature_key=signature_key,
            keyring_path=keyring_path,
        )
    except (KeyringError, TaskLoadError) as exc:
        print(stable_json_dumps(_trust_error_payload(exc)))
        return 2
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=config,
    )
    summaries = engine.run()

    print("Self-Harness local subprocess demo complete")
    print("This is not a benchmark reproduction.")
    print("round  before_in  before_out  proposals  accepted  rejected  after_in  after_out")
    for summary in summaries:
        print(
            f"{summary.round:<5}  "
            f"{summary.baseline_held_in:<9}  "
            f"{summary.baseline_held_out:<10}  "
            f"{summary.proposals:<9}  "
            f"{summary.accepted:<8}  "
            f"{summary.rejected:<8}  "
            f"{summary.after_held_in:<8}  "
            f"{summary.after_held_out:<9}"
        )
    print(f"Artifacts: {out_dir}")
    return 0


def _run_python_demo(
    corpus_path: Path,
    trusted_module: str,
    verifier_symbol: str,
    setup_symbol: str,
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
    keep_workdir: bool,
    signature_key: Path | None,
    keyring_path: Path | None,
) -> int:
    config = EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(
            max_proposals=max_proposals,
            max_payload_bytes=max_payload_bytes,
        ),
        model_id="in-process-python-verifier",
    )
    adapter = InProcessPythonTaskAdapter(
        module_path=trusted_module,
        verifier_symbol=verifier_symbol,
        setup_symbol=setup_symbol,
        keep_workdir=keep_workdir,
    )
    try:
        corpus, _trusted_entry = _load_trusted_corpus(
            corpus_path,
            allow_legacy=False,
            signature_key=signature_key,
            keyring_path=keyring_path,
        )
        runner = adapter.runner()
    except (InProcessVerifierError, KeyringError, TaskLoadError) as exc:
        reason = "invalid-verifier" if isinstance(exc, InProcessVerifierError) else _trust_error_payload(exc)["reason"]
        print(stable_json_dumps({"ok": False, "reason": reason, "message": str(exc)}))
        return 2
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=runner,
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=config,
    )
    try:
        summaries = engine.run()
    except InProcessVerifierError as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-verifier", "message": str(exc)}))
        return 2

    print("Self-Harness trusted in-process Python verifier demo complete")
    print("This is not a benchmark reproduction.")
    _print_summaries(summaries)
    print(f"Artifacts: {out_dir}")
    return 0


def _run_code(
    *,
    root: Path,
    harness_state: Path | None,
    inbox_dir: Path | None,
    max_steps: int,
    tool_timeout_seconds: int,
    harvest: bool,
    resume: str | None = None,
    plain: bool = False,
    local_harness: bool = False,
) -> int:
    """Launch the interactive coding agent (`self-harness code`)."""

    import uuid
    from datetime import UTC, datetime

    from self_harness import user_config
    from self_harness.agentic_session import (
        HOST_EXEC_WARNING_LINES,
        resolve_zai_api_key,
        resolve_zai_base_url,
    )
    from self_harness.cli_agent import FailureHarvester, HeadlessCliSession, InteractiveSession, run_repl
    from self_harness.cli_agent.session import load_session_harness
    from self_harness.cli_agent.sessions import latest_session, load_session
    from self_harness.exceptions import AgenticRunnerError
    from self_harness.loop_paths import central_runs_dir

    workdir = root.resolve()
    runs_dir = workdir / "runs"
    # Default to the SHARED central harness + inbox so the continuous loop learns from your sessions
    # (and the agent benefits from the loop's improvements). Explicit flags always win; --local-harness
    # forces the old per-project behavior. Sessions stay in the project's own runs/ either way.
    central = None if local_harness else central_runs_dir()
    default_state = (central / "harness_state.json") if central else (runs_dir / "harness_state.json")
    default_inbox = (central / "inbox") if central else (runs_dir / "inbox")
    state_path = (harness_state or default_state).resolve()
    inbox = (inbox_dir or default_inbox).resolve()

    cfg = user_config.load_config()
    provider = user_config.resolve_code_provider(config=cfg)
    model = user_config.resolve_code_model(provider=provider, config=cfg)
    effort = user_config.resolve_code_effort(provider=provider, config=cfg)
    headless_backend = None if provider == "glm" else _headless_backend_for_model(provider)
    api_key = ""
    base_url = ""
    if headless_backend is None:
        try:
            api_key = resolve_zai_api_key()
        except AgenticRunnerError as exc:
            print(f"error: {exc}")
            return 2
        base_url = resolve_zai_base_url()

    for line in HOST_EXEC_WARNING_LINES:
        print(line)
    if headless_backend is not None:
        model_text = model or "provider default"
        effort_text = effort or "provider default"
        print(
            f"main coding backend: {headless_backend} headless CLI "
            f"({_headless_binary_for_backend(headless_backend)}), model: {model_text}, effort: {effort_text}"
        )
    else:
        print(f"main coding backend: GLM via Z.ai ({model or user_config.DEFAULT_MODEL})")
    print()

    # Resolve a session to resume (explicit id, or most recent), or mint a fresh one.
    resumed = None
    if resume == "__latest__":
        resumed = latest_session(workdir)
        if resumed is None:
            print("no saved sessions to resume; starting a new one")
    elif resume:
        resumed = load_session(workdir, resume)
        if resumed is None:
            print(f"no session '{resume}' found; starting a new one")

    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    session_id = resumed.id if resumed is not None else f"code-{now}-{uuid.uuid4().hex[:8]}"

    harness, evolving = load_session_harness(state_path)
    harvester = FailureHarvester(inbox_dir=inbox, workdir=workdir, enabled=harvest)
    session: HeadlessCliSession | InteractiveSession
    if headless_backend is not None:
        session = HeadlessCliSession(
            backend=headless_backend,
            binary=_headless_binary_for_backend(headless_backend),
            workdir=workdir,
            harness=harness,
            harvester=harvester,
            model=model,
            effort=effort,
            max_steps=max_steps,
            tool_timeout_seconds=tool_timeout_seconds,
            evolving=evolving,
            history=list(resumed.history) if resumed is not None else [],
            turn_index=len(resumed.turns) if resumed is not None else 0,
        )
    else:
        session = InteractiveSession(
            api_key=api_key,
            base_url=base_url,
            workdir=workdir,
            harness=harness,
            harvester=harvester,
            model=model or user_config.DEFAULT_MODEL,
            max_steps=max_steps,
            tool_timeout_seconds=tool_timeout_seconds,
            evolving=evolving,
            history=list(resumed.history) if resumed is not None else [],
            turn_index=len(resumed.turns) if resumed is not None else 0,
        )
    if resumed is not None:
        harvester.seed_written(resumed.harvested)
        print(f"resumed session {session_id} ({len(resumed.turns)} prior turn(s))")
    return run_repl(
        session,
        root=workdir,
        session_id=session_id,
        timestamp=now,
        plain=plain,
    )


def _run_glm_agentic_demo(
    *,
    corpus_path: Path,
    proposer_mode: str,
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
    max_steps: int,
    tool_timeout_seconds: int,
    codex_binary: str,
    keep_workdir: bool,
    signature_key: Path | None,
    keyring_path: Path | None,
) -> int:
    from self_harness.adapters.agentic.runner import GLMAgenticTaskAdapter
    from self_harness.agentic_session import (
        HOST_EXEC_WARNING_LINES,
        build_agentic_config,
        build_proposer,
    )
    from self_harness.exceptions import AgenticRunnerError, LLMClientError

    api_key = os.environ.get("ZAI_API_KEY")
    if not api_key:
        print(stable_json_dumps({"ok": False, "reason": "missing-credentials", "message": "set ZAI_API_KEY"}))
        return 2
    base_url = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/anthropic")

    config = build_agentic_config(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        max_proposals=max_proposals,
        max_payload_bytes=max_payload_bytes,
    )
    adapter = GLMAgenticTaskAdapter(
        api_key=api_key,
        base_url=base_url,
        max_steps=max_steps,
        tool_timeout_seconds=tool_timeout_seconds,
        codex_binary=codex_binary,
        keep_workdir=keep_workdir,
    )
    try:
        corpus, _trusted_entry = _load_trusted_corpus(
            corpus_path,
            allow_legacy=False,
            signature_key=signature_key,
            keyring_path=keyring_path,
        )
        runner = adapter.runner()
    except (AgenticRunnerError, KeyringError, TaskLoadError) as exc:
        reason = "invalid-runner" if isinstance(exc, AgenticRunnerError) else _trust_error_payload(exc)["reason"]
        print(stable_json_dumps({"ok": False, "reason": reason, "message": str(exc)}))
        return 2

    try:
        proposer = build_proposer(proposer_mode, api_key=api_key, base_url=base_url)
    except LLMClientError as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-runner", "message": str(exc)}))
        return 2

    for line in HOST_EXEC_WARNING_LINES:
        print(f"WARNING: {line}")

    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=runner,
        proposer=proposer,
        out_dir=out_dir,
        config=config,
    )
    summaries = engine.run()

    print("Self-Harness GLM 5.2 agentic demo complete")
    print("This is not a benchmark reproduction.")
    _print_summaries(summaries)
    print(f"Artifacts: {out_dir}")
    return 0


def _run_http_demo(
    corpus_path: Path,
    trusted_url: str,
    timeout_seconds: float,
    tls_ca_bundle: Path | None,
    tls_client_cert: Path | None,
    tls_client_key: Path | None,
    header_args: list[str],
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
    keep_workdir: bool,
    signature_key: Path | None,
    keyring_path: Path | None,
) -> int:
    config = EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(
            max_proposals=max_proposals,
            max_payload_bytes=max_payload_bytes,
        ),
        model_id="http-verifier",
    )
    try:
        headers = _parse_http_headers(header_args)
        adapter = HttpVerifierTaskAdapter(
            verifier_url=trusted_url,
            timeout_seconds=timeout_seconds,
            keep_workdir=keep_workdir,
            extra_headers=headers,
            tls_ca_bundle=tls_ca_bundle,
            tls_client_cert=tls_client_cert,
            tls_client_key=tls_client_key,
        )
        corpus, _trusted_entry = _load_trusted_corpus(
            corpus_path,
            allow_legacy=False,
            signature_key=signature_key,
            keyring_path=keyring_path,
        )
        tasks = adapter.load(corpus)
        runner = adapter.runner()
    except (HttpVerifierError, KeyringError, TaskLoadError) as exc:
        reason = "invalid-verifier" if isinstance(exc, HttpVerifierError) else _trust_error_payload(exc)["reason"]
        print(stable_json_dumps({"ok": False, "reason": reason, "message": str(exc)}))
        return 2
    engine = SelfHarnessEngine(
        tasks=tasks,
        runner=runner,
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=config,
    )
    try:
        summaries = engine.run()
    except HttpVerifierError as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-verifier", "message": str(exc)}))
        return 2

    print("Self-Harness trusted HTTP verifier demo complete")
    print("This is not a benchmark reproduction.")
    _print_summaries(summaries)
    print(f"Artifacts: {out_dir}")
    return 0


def _parse_http_headers(values: list[str]) -> tuple[tuple[str, str], ...]:
    headers: list[tuple[str, str]] = []
    for value in values:
        if ":" not in value:
            raise HttpVerifierError("HTTP verifier headers must use KEY: VALUE")
        key, item = value.split(":", 1)
        key = key.strip()
        item = item.strip()
        if not key or "\n" in key or "\r" in key or "\n" in item or "\r" in item:
            raise HttpVerifierError("HTTP verifier headers must be single-line KEY: VALUE pairs")
        headers.append((key, item))
    return tuple(headers)


def _run_container_demo(
    corpus_path: Path,
    image: str,
    image_digest: str | None,
    image_policy_path: Path | None,
    require_image_digest: bool,
    mode: str,
    container_command: str,
    fixture_dir: Path | None,
    docker_executable: str,
    timeout_seconds: float,
    env_args: list[str],
    env_file_args: list[Path],
    docker_config_dir: Path | None,
    keep_workdir: bool,
    skip_docker_preflight: bool,
    require_image_present: bool,
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
    signature_key: Path | None,
    keyring_path: Path | None,
) -> int:
    try:
        command = parse_container_command(container_command)
        env = _parse_key_value_args(env_args, label="container environment")
        image_policy = load_image_policy(image_policy_path) if image_policy_path is not None else None
        ContainerVerifierTaskAdapter(
            image=image,
            image_digest=image_digest,
            command=command,
            mode=cast(ContainerMode, mode),
            fixture_dir=fixture_dir,
            docker_executable=docker_executable,
            timeout_seconds=timeout_seconds,
            keep_workdir=keep_workdir,
            extra_env=env,
            extra_env_files=tuple(env_file_args),
            docker_config_dir=docker_config_dir,
            image_policy=image_policy,
            require_image_digest=require_image_digest,
        )
        if mode == "live" and not skip_docker_preflight:
            report = run_container_preflight(
                image,
                docker_executable=docker_executable,
                require_daemon=True,
                require_image_present=require_image_present,
            )
            write_preflight_report(out_dir / "preflight.json", report)
            if not report.passed:
                print(stable_json_dumps(report))
                return 2
        adapter = ContainerVerifierTaskAdapter(
            image=image,
            image_digest=image_digest,
            command=command,
            mode=cast(ContainerMode, mode),
            fixture_dir=fixture_dir,
            docker_executable=docker_executable,
            timeout_seconds=timeout_seconds,
            keep_workdir=keep_workdir,
            extra_env=env,
            extra_env_files=tuple(env_file_args),
            docker_config_dir=docker_config_dir,
            image_policy=image_policy,
            require_image_digest=require_image_digest,
        )
        corpus, _trusted_entry = _load_trusted_corpus(
            corpus_path,
            allow_legacy=False,
            signature_key=signature_key,
            keyring_path=keyring_path,
        )
        tasks = adapter.load(corpus)
        runner = adapter.runner()
    except (ContainerVerifierError, ImagePolicyError, KeyringError, TaskLoadError) as exc:
        reason = (
            "invalid-verifier"
            if isinstance(exc, (ContainerVerifierError, ImagePolicyError))
            else _trust_error_payload(exc)["reason"]
        )
        print(stable_json_dumps({"ok": False, "reason": reason, "message": str(exc)}))
        return 2
    config = EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(
            max_proposals=max_proposals,
            max_payload_bytes=max_payload_bytes,
        ),
        model_id=f"container-verifier-{mode}",
    )
    try:
        summaries = SelfHarnessEngine(
            tasks=tasks,
            runner=runner,
            proposer=HeuristicProposer(),
            out_dir=out_dir,
            config=config,
        ).run()
    except ContainerVerifierError as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-verifier", "message": str(exc)}))
        return 2

    print("Self-Harness trusted container verifier demo complete")
    print("This is not a benchmark reproduction.")
    _print_summaries(summaries)
    print(f"Artifacts: {out_dir}")
    return 0


def _parse_key_value_args(values: list[str], *, label: str) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    for value in values:
        if "=" not in value:
            raise ContainerVerifierError(f"{label} values must use KEY=VALUE")
        key, item = value.split("=", 1)
        if not key or "\n" in key or "\r" in key or "\n" in item or "\r" in item:
            raise ContainerVerifierError(f"{label} values must be single-line KEY=VALUE pairs")
        parsed.append((key, item))
    return tuple(parsed)


def _run_validate_tasks(
    path: Path,
    allow_legacy: bool,
    min_per_split: int,
    verify_checksum: bool,
    signature_key: Path | None,
    keyring_path: Path | None,
) -> int:
    try:
        corpus, trusted_entry = _load_trusted_corpus(
            path,
            allow_legacy=allow_legacy,
            min_per_split=min_per_split,
            verify_checksum=verify_checksum,
            signature_key=signature_key,
            keyring_path=keyring_path,
        )
    except (KeyringError, TaskLoadError) as exc:
        print(stable_json_dumps(_trust_error_payload(exc)))
        return 2
    payload: dict[str, object] = {
        "ok": True,
        "corpus_version": corpus.corpus_version,
        "corpus_id": corpus.corpus_id,
        "tasks": len(corpus.tasks),
        "split_counts": split_counts(corpus),
        "checksum": corpus.checksum or corpus_checksum(corpus),
    }
    if trusted_entry is not None:
        payload["trusted_key_fingerprint"] = trusted_entry.fingerprint
        payload["trusted_key_status"] = trusted_entry.status.value
    print(stable_json_dumps(payload))
    return 0


def _load_trusted_corpus(
    path: Path,
    *,
    allow_legacy: bool,
    min_per_split: int = 0,
    verify_checksum: bool = True,
    signature_key: Path | None = None,
    keyring_path: Path | None = None,
) -> tuple[TaskCorpus, KeyringEntry | None]:
    corpus = load_corpus(
        path,
        allow_legacy=allow_legacy,
        min_per_split=min_per_split,
        verify_checksum=verify_checksum,
        verify_signature_key=signature_key,
    )
    if keyring_path is None:
        return corpus, None
    trusted_entry = verify_corpus_with_keyring(corpus, load_keyring(keyring_path))
    return corpus, trusted_entry


def _trust_error_payload(exc: KeyringError | TaskLoadError) -> dict[str, object]:
    if isinstance(exc, TaskLoadError):
        return {"ok": False, "reason": exc.reason, "message": str(exc)}
    return {"ok": False, "reason": "invalid-keyring", "message": str(exc)}


def _run_corpus_signing_error(exc: Exception) -> int:
    print(stable_json_dumps({"ok": False, "reason": "corpus-signing-error", "message": str(exc)}))
    return 2


def _run_corpus_keygen(out_path: Path, *, force: bool, passphrase: str | None = None) -> int:
    private_path = Path(out_path)
    public_path = private_path.with_name(private_path.name + ".pub")
    if not force:
        existing = [path for path in [private_path, public_path] if path.exists()]
        if existing:
            print(
                stable_json_dumps(
                    {
                        "ok": False,
                        "reason": "key-exists",
                        "message": "refusing to overwrite existing key file(s)",
                        "paths": [str(path.resolve()) for path in existing],
                    }
                )
            )
            return 2
    try:
        private_pem, public_pem = generate_keypair(passphrase=passphrase)
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_bytes(private_pem)
        os.chmod(private_path, 0o600)
        public_path.write_bytes(public_pem)
        print(
            stable_json_dumps(
                {
                    "ok": True,
                    "private_key": str(private_path.resolve()),
                    "public_key": str(public_path.resolve()),
                    "fingerprint": public_key_fingerprint(public_path),
                    "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
                    "private_key_encrypted": passphrase is not None,
                    "private_key_encryption_profile": (
                        PRIVATE_KEY_ENCRYPTION_PROFILE if passphrase is not None else None
                    ),
                }
            )
        )
        return 0
    except (CorpusSigningError, OSError) as exc:
        return _run_corpus_signing_error(exc)


def _run_corpus_sign(
    corpus_path: Path,
    *,
    private_key: Path | None,
    out_path: Path,
    passphrase: str | None = None,
    external_signer: str | None = None,
    signer_provider: str = "external",
    signer_key_id: str = "",
    signer_timeout_seconds: float = DEFAULT_SIGNER_TIMEOUT_SECONDS,
    signer_max_output_bytes: int = DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    expected_public_key: Path | None = None,
    expected_fingerprint: str | None = None,
) -> int:
    try:
        corpus = load_corpus(corpus_path)
        signer_metadata: dict[str, object] = {"mode": "local-private-key"}
        if external_signer is not None:
            expected = _expected_external_signer_fingerprint(expected_public_key, expected_fingerprint)
            response = sign_corpus_with_external_signer(
                corpus,
                parse_external_signer_command(external_signer),
                provider=signer_provider,
                key_id=signer_key_id,
                timeout_seconds=signer_timeout_seconds,
                max_output_bytes=signer_max_output_bytes,
                expected_fingerprint=expected,
            )
            signature = response.signature
            signer_metadata = {
                "mode": "external-signer",
                "protocol_version": EXTERNAL_SIGNER_PROTOCOL_VERSION,
                "provider": response.provider,
                "key_id": response.key_id,
                "fingerprint": response.fingerprint,
                "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
                "public_key_b64": response.public_key_b64,
            }
        else:
            if private_key is None:
                raise CorpusSigningError("--private-key is required unless --external-signer is used")
            signature = sign_corpus(corpus, private_key.read_bytes(), passphrase=passphrase)
        checksum = corpus_checksum(corpus)
        signed_payload = {
            "corpus_version": corpus.corpus_version,
            "corpus_id": corpus.corpus_id,
            "tasks": to_jsonable(corpus.tasks),
            "checksum": checksum,
            "signature": signature,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(stable_json_dumps(signed_payload) + "\n", encoding="utf-8")
        print(
            stable_json_dumps(
                {
                    "ok": True,
                    "corpus_id": corpus.corpus_id,
                    "checksum": checksum,
                    "signature": signature,
                    "signed_path": str(out_path.resolve()),
                    "signer": signer_metadata,
                }
            )
        )
        return 0
    except ExternalSignerError as exc:
        print(stable_json_dumps(exc.failure.to_jsonable()), file=sys.stderr)
        return 2
    except (CorpusSigningError, OSError, TaskLoadError) as exc:
        reason = exc.reason if isinstance(exc, TaskLoadError) else "corpus-signing-error"
        print(stable_json_dumps({"ok": False, "reason": reason, "message": str(exc)}))
        return 2


def _expected_external_signer_fingerprint(
    expected_public_key: Path | None,
    expected_fingerprint: str | None,
) -> str | None:
    expected = expected_fingerprint.lower() if expected_fingerprint is not None else None
    if expected is not None and (
        len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise CorpusSigningError("--fingerprint must be 64 lowercase hex characters")
    if expected_public_key is None:
        return expected
    public_key_fingerprint_value = public_key_fingerprint(expected_public_key)
    if expected is not None and expected != public_key_fingerprint_value:
        raise CorpusSigningError("--public-key fingerprint does not match --fingerprint")
    return public_key_fingerprint_value


def _run_corpus_fingerprint(public_key: Path) -> int:
    try:
        print(
            stable_json_dumps(
                {
                    "ok": True,
                    "fingerprint": public_key_fingerprint(public_key),
                    "algorithm": FINGERPRINT_ALGORITHM,
                    "public_key_path": str(public_key.resolve()),
                }
            )
        )
        return 0
    except (CorpusSigningError, OSError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "corpus-signing-error", "message": str(exc)}))
        return 2


def _run_operator_promotion(args: argparse.Namespace) -> int:
    try:
        if args.promotion_command == "init":
            manifest = init_promotion_manifest(args.manifest, force=args.force)
            return _print_promotion_manifest(args.manifest, manifest)
        if args.promotion_command == "add":
            manifest = add_promotion_entry(
                args.manifest,
                name=args.name,
                kind=args.kind,
                file_path=args.file,
                status=args.status,
            )
            return _print_promotion_manifest(args.manifest, manifest)
        if args.promotion_command == "set-status":
            manifest = set_promotion_status(args.manifest, name=args.name, status=args.status)
            return _print_promotion_manifest(args.manifest, manifest)
        if args.promotion_command == "sign":
            if args.external_signer is not None and _has_passphrase_args(args):
                raise PromotionError("passphrase options cannot be used with --external-signer")
            passphrase = None if args.external_signer is not None else _resolve_passphrase_args(args)
            signature = sign_promotion_manifest(
                args.manifest,
                out_path=args.out,
                private_key=args.private_key,
                passphrase=passphrase,
                external_signer=args.external_signer,
                provider=args.provider,
                key_id=args.key_id,
                signer_timeout_seconds=args.signer_timeout,
                signer_max_output_bytes=args.signer_max_output,
                expected_public_key=args.public_key,
                expected_fingerprint=args.fingerprint,
            )
            print(
                stable_json_dumps(
                    {
                        "ok": True,
                        "manifest": str(args.manifest.resolve()),
                        "signature_path": str(args.out.resolve()),
                        "signature": promotion_signature_to_jsonable(signature),
                    }
                )
            )
            return 0
        if args.promotion_command == "verify":
            report = verify_promotion_manifest(
                args.manifest,
                signature_path=args.signature,
                trusted_public_key=args.trusted_public_key,
            )
            payload = promotion_verification_report_to_jsonable(report)
            if args.out is not None:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
            if args.json or args.out is None:
                print(stable_json_dumps(payload))
            return 0 if report.ok else 2
    except ExternalSignerError as exc:
        print(stable_json_dumps(exc.failure.to_jsonable()), file=sys.stderr)
        return 2
    except (PromotionError, CorpusSigningError, OSError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "operator-promotion-error", "message": str(exc)}))
        return 2
    raise AssertionError(f"unhandled operator-promotion command: {args.promotion_command}")


def _print_promotion_manifest(manifest_path: Path, manifest: PromotionManifest) -> int:
    print(
        stable_json_dumps(
            {
                "ok": True,
                "manifest": str(manifest_path.resolve()),
                "entry_count": len(manifest.entries),
                "promotion_manifest": promotion_manifest_to_jsonable(manifest),
            }
        )
    )
    return 0


def _has_passphrase_args(args: argparse.Namespace) -> bool:
    return args.passphrase is not None or args.passphrase_file is not None or args.passphrase_env is not None


def _run_capture_manifest(args: argparse.Namespace) -> int:
    try:
        requirements = load_reproduction_requirements(args.requirements)
        if args.capture_manifest_command == "build":
            document = build_capture_manifest(
                requirements=requirements,
                manifest_id=args.manifest_id,
                bundle_id=args.bundle_id,
                operator_label=args.operator_label,
                created_at=args.created_at,
                run_id=args.run_id,
                mode=args.mode,
                benchmark_protocol=args.benchmark_protocol,
                model_backends=args.model_backend,
                evaluator=args.evaluator,
                tool_set=args.tool_set,
                tool_budget=_capture_manifest_tool_budget(args.tool_budget_json),
                outbound_bandwidth_cap_bps=args.outbound_bandwidth_cap_bps,
                mirrored_resources=args.mirrored_resource,
                signing_custody={
                    "provider": args.signing_provider,
                    **({"key_id": args.key_id} if args.key_id is not None else {}),
                    **({"fingerprint": args.fingerprint} if args.fingerprint is not None else {}),
                },
                source_defaults={
                    "provider": args.source_provider,
                    "captured_after": args.source_captured_after,
                    "captured_before": args.source_captured_before,
                    "operator_label": args.operator_label,
                },
                entry_sources=_capture_manifest_entry_sources(args.entry_source),
                planned_artifacts=_capture_manifest_planned_artifacts(args.planned_artifact),
                entry_notes=_capture_manifest_entry_notes(args.entry_note),
                strict_shapes=args.strict_shapes,
            )
            write_capture_manifest_document(document, args.out)
            payload = capture_manifest_document_to_jsonable(document)
            return _write_capture_manifest_payload(
                payload,
                ok=True,
                out_path=args.out,
                json_output=args.json,
            )
        if args.capture_manifest_command == "verify":
            verify_report = verify_capture_manifest(
                args.manifest,
                requirements,
                signature_path=args.signature,
                public_key=args.public_key,
                require_signature=args.require_signature,
            )
            payload = capture_manifest_report_to_jsonable(verify_report)
            return _write_capture_manifest_payload(
                payload,
                ok=verify_report.ok,
                out_path=args.out,
                json_output=args.json,
            )
        if args.capture_manifest_command == "diff":
            diff_report = diff_capture_manifest_to_bundle(
                args.manifest,
                args.bundle,
                requirements,
                manifest_signature_path=args.manifest_signature,
                bundle_signature_path=args.bundle_signature,
                require_manifest_signature=args.require_manifest_signature,
                require_bundle_signature=args.require_bundle_signature,
            )
            payload = capture_manifest_diff_report_to_jsonable(diff_report)
            return _write_capture_manifest_payload(
                payload,
                ok=diff_report.ok,
                out_path=args.out,
                json_output=args.json,
            )
        if args.capture_manifest_command == "rehearse":
            readiness_matrix = load_readiness_matrix_report(args.readiness_matrix_result)
            rehearsal_report = run_capture_rehearsal(
                manifest_path=args.manifest,
                requirements=requirements,
                readiness_matrix_report=readiness_matrix,
                out_dir=args.out_dir,
                rehearsal_id=args.rehearsal_id,
                operator_label=args.operator_label,
                manifest_signature_path=args.manifest_signature,
                manifest_public_key=args.public_key,
                require_manifest_signature=args.require_manifest_signature,
                bundle_private_key=args.bundle_private_key,
                bundle_external_signer=args.bundle_external_signer,
                bundle_public_key=args.bundle_public_key,
                bundle_fingerprint=args.bundle_fingerprint,
                bundle_signature_path=args.bundle_signature_out,
                bundle_signature_provider=args.bundle_signature_provider,
                bundle_key_id=args.bundle_key_id,
                require_bundle_signature=args.require_bundle_signature,
            )
            payload = capture_rehearsal_report_to_jsonable(rehearsal_report)
            return _write_capture_manifest_payload(
                payload,
                ok=rehearsal_report.ok,
                out_path=args.report_out,
                json_output=args.json,
            )
    except (OSError, ReproductionReadinessError, CaptureManifestBuildError, CaptureRehearsalError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "capture-manifest-error", "message": str(exc)}))
        return 2
    raise AssertionError(f"unhandled capture-manifest command: {args.capture_manifest_command}")


def _run_capture_extract(args: argparse.Namespace) -> int:
    try:
        payload = extract_artifact_from_paths(
            args.artifact_class,
            capture_run_id=args.capture_run_id,
            harbor_discovery_result=args.harbor_discovery_result,
            harbor_version=args.harbor_version,
            image_policy=args.image_policy,
            model_backend_preflight_result=args.model_backend_preflight_result,
            network_controls=args.network_controls,
            harbor_run_dir=args.harbor_run_dir,
            capture_envelope=args.capture_envelope,
            attempts_jsonl=args.attempts_jsonl,
            split_manifest_result=args.split_manifest_result,
            fixed_protocol_declaration=args.fixed_protocol_declaration,
            fixed_protocol_result=args.fixed_protocol_result,
            fixed_protocol_sha256=args.fixed_protocol_sha256,
            proposer_request_log=args.proposer_request_log,
            proposer_request_log_artifact=args.proposer_request_log_artifact,
            proposer_context_log=args.proposer_context_log,
            audit_run_dir=args.audit_run_dir,
            proposer_backend_map=parse_proposer_backend_map(args.proposer_backend_map)
            if args.proposer_backend_map
            else {},
        )
    except (OSError, CaptureExtractError, ImagePolicyError) as exc:
        error_payload = {
            "schema_version": "1.0",
            "ok": False,
            "artifact_class": args.artifact_class,
            "reason": "capture-extract-error",
            "message": str(exc),
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        }
        print(stable_json_dumps(error_payload))
        return 2
    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    if args.json:
        print(output, end="")
    else:
        print(args.out if args.out is not None else output, end="" if args.out is None else "\n")
    return 0


def _run_capture_admit(args: argparse.Namespace) -> int:
    try:
        requirements = load_reproduction_requirements(args.requirements)
        result = run_capture_admission(
            admission_id=args.admission_id,
            requirements=requirements,
            artifact_dir=args.artifact_dir,
            bundle_path=args.bundle_out if args.bundle_out is not None else args.artifact_dir / "bundle.json",
            bundle_id=args.bundle_id,
            operator_label=args.operator_label,
            created_at=args.created_at,
            source_provider=args.source_provider,
            source_captured_at=args.source_captured_at,
            source_url=args.source_url,
            raw_inputs=_capture_admit_raw_inputs(args.raw_input),
            raw_flags=_capture_admit_raw_flags(args.raw_flag),
            supplied_artifacts=_capture_admit_artifacts(args.artifact),
            readiness_matrix_result=args.readiness_matrix_result,
            bundle_signature_path=args.bundle_signature,
            bundle_public_key=args.bundle_public_key,
            require_bundle_signature=args.require_bundle_signature,
            skip_readiness=args.skip_readiness,
        )
        payload = capture_admission_report_to_jsonable(result)
    except (OSError, CaptureAdmissionError, ReproductionReadinessError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "capture-admission-error",
            "message": str(exc),
            "reproduction_claimed": False,
            "boundary": CAPTURE_ADMISSION_BOUNDARY,
        }
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
        print(stable_json_dumps(payload))
        return 2
    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result.ok else 2


def _capture_admit_raw_inputs(specs: list[str]) -> dict[str, dict[str, Path]]:
    result: dict[str, dict[str, Path]] = {}
    for spec in specs:
        artifact_class, raw_assignment = _capture_admit_class_spec(spec, flag="--raw-input")
        key, raw_path = _capture_admit_key_value(raw_assignment, flag="--raw-input")
        if key in result.setdefault(artifact_class, {}):
            raise CaptureAdmissionError(f"duplicate raw input for {artifact_class}:{key}")
        result[artifact_class][key] = Path(raw_path)
    return result


def _capture_admit_raw_flags(specs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in specs:
        key, value = _capture_admit_key_value(spec, flag="--raw-flag")
        if key in result:
            raise CaptureAdmissionError(f"duplicate raw flag: {key}")
        result[key] = value
    return result


def _capture_admit_artifacts(specs: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for spec in specs:
        artifact_class, raw_path = _capture_admit_key_value(spec, flag="--artifact")
        if artifact_class in result:
            raise CaptureAdmissionError(f"duplicate supplied artifact: {artifact_class}")
        result[artifact_class] = Path(raw_path)
    return result


def _capture_admit_class_spec(spec: str, *, flag: str) -> tuple[str, str]:
    artifact_class, separator, rest = spec.partition(":")
    if not separator or not artifact_class or not rest:
        raise CaptureAdmissionError(f"{flag} values must use CLASS:KEY=VALUE")
    return artifact_class, rest


def _capture_admit_key_value(spec: str, *, flag: str) -> tuple[str, str]:
    key, separator, value = spec.partition("=")
    if not separator or not key or not value:
        raise CaptureAdmissionError(f"{flag} values must use KEY=VALUE")
    return key, value


def _capture_manifest_planned_artifacts(specs: list[str]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for spec in specs:
        artifact_class, raw_path = _capture_manifest_key_value(spec, flag="--planned-artifact")
        if artifact_class in artifacts:
            raise CaptureManifestBuildError(f"duplicate planned artifact class: {artifact_class}")
        artifacts[artifact_class] = load_planned_artifact(Path(raw_path), artifact_class=artifact_class)
    return artifacts


def _capture_manifest_entry_sources(specs: list[str]) -> dict[str, dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    for spec in specs:
        artifact_class, raw_assignment = _capture_manifest_class_spec(spec, flag="--entry-source")
        key, value = _capture_manifest_key_value(raw_assignment, flag="--entry-source")
        sources.setdefault(artifact_class, {})[key] = value
    return sources


def _capture_manifest_entry_notes(specs: list[str]) -> dict[str, str]:
    notes: dict[str, str] = {}
    for spec in specs:
        artifact_class, note = _capture_manifest_key_value(spec, flag="--entry-note")
        if artifact_class in notes:
            raise CaptureManifestBuildError(f"duplicate entry note class: {artifact_class}")
        notes[artifact_class] = note
    return notes


def _capture_manifest_tool_budget(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CaptureManifestBuildError("--tool-budget-json must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise CaptureManifestBuildError("--tool-budget-json must be a JSON object")
    return cast(dict[str, object], payload)


def _capture_manifest_class_spec(spec: str, *, flag: str) -> tuple[str, str]:
    artifact_class, separator, rest = spec.partition(":")
    if not separator or not artifact_class or not rest:
        raise CaptureManifestBuildError(f"{flag} values must use CLASS:KEY=VALUE")
    return artifact_class, rest


def _capture_manifest_key_value(spec: str, *, flag: str) -> tuple[str, str]:
    key, separator, value = spec.partition("=")
    if not separator or not key or not value:
        raise CaptureManifestBuildError(f"{flag} values must use KEY=VALUE")
    return key, value


def _write_capture_manifest_payload(
    payload: dict[str, object],
    *,
    ok: bool,
    out_path: Path | None,
    json_output: bool,
) -> int:
    output = stable_json_dumps(payload) + "\n"
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    if json_output or out_path is None:
        print(output, end="")
    return 0 if ok else 2


def _run_verify_attestation(args: argparse.Namespace) -> int:
    try:
        report = verify_attestation(
            args.bundle,
            material_path=args.material,
            trust_root_path=args.trust_root,
            backend=args.backend,
        )
        payload = attestation_report_to_jsonable(report)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
        if args.json or args.out is None:
            print(stable_json_dumps(payload))
        return 0 if report.ok else 2
    except (AttestationError, OSError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "attestation-error", "message": str(exc)}))
        return 2


def _run_corpus_keyring(args: argparse.Namespace) -> int:
    if args.keyring_command == "init":
        return _run_corpus_keyring_init(args.out, force=args.force)
    if args.keyring_command == "add":
        return _run_corpus_keyring_add(
            keyring_path=args.keyring,
            corpus_id=args.corpus_id,
            public_key=args.public_key,
            status=args.status,
            label_args=args.label,
        )
    if args.keyring_command == "set-status":
        return _run_corpus_keyring_set_status(
            keyring_path=args.keyring,
            corpus_id=args.corpus_id,
            fingerprint=args.fingerprint,
            status=args.status,
        )
    if args.keyring_command == "inspect":
        return _run_corpus_keyring_inspect(args.keyring, corpus_id=args.corpus_id, json_output=args.json)
    raise AssertionError(f"unhandled corpus-keyring command: {args.keyring_command}")


def _run_corpus_keyring_init(out_path: Path, *, force: bool) -> int:
    if out_path.exists() and not force:
        print(
            stable_json_dumps(
                {
                    "ok": False,
                    "reason": "keyring-exists",
                    "message": "refusing to overwrite existing keyring",
                    "keyring": str(out_path.resolve()),
                }
            )
        )
        return 2
    try:
        save_keyring(empty_keyring(), out_path)
    except OSError as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-keyring", "message": str(exc)}))
        return 2
    print(
        stable_json_dumps(
            {
                "ok": True,
                "keyring": str(out_path.resolve()),
                "keyring_version": "1",
                "entries": 0,
            }
        )
    )
    return 0


def _run_corpus_keyring_add(
    *,
    keyring_path: Path,
    corpus_id: str,
    public_key: Path,
    status: str,
    label_args: list[str],
) -> int:
    try:
        keyring = load_keyring(keyring_path) if keyring_path.exists() else empty_keyring()
        updated = add_keyring_entry(
            keyring,
            corpus_id=corpus_id,
            public_key=public_key,
            status=status,
            labels=_parse_keyring_labels(label_args),
        )
        save_keyring(updated, keyring_path)
        entry = updated.entries[-1]
    except (KeyringError, OSError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-keyring", "message": str(exc)}))
        return 2
    print(
        stable_json_dumps(
            {
                "ok": True,
                "keyring": str(keyring_path.resolve()),
                "corpus_id": entry.corpus_id,
                "fingerprint": entry.fingerprint,
                "fingerprint_algorithm": entry.fingerprint_algorithm,
                "status": entry.status.value,
                "entries": len(updated.entries),
            }
        )
    )
    return 0


def _run_corpus_keyring_set_status(
    *,
    keyring_path: Path,
    corpus_id: str,
    fingerprint: str,
    status: str,
) -> int:
    try:
        updated = set_keyring_entry_status(
            load_keyring(keyring_path),
            corpus_id=corpus_id,
            fingerprint=fingerprint,
            status=status,
        )
        save_keyring(updated, keyring_path)
    except (KeyringError, OSError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-keyring", "message": str(exc)}))
        return 2
    print(
        stable_json_dumps(
            {
                "ok": True,
                "keyring": str(keyring_path.resolve()),
                "corpus_id": corpus_id,
                "fingerprint": fingerprint.lower(),
                "status": status,
            }
        )
    )
    return 0


def _run_corpus_keyring_inspect(keyring_path: Path, *, corpus_id: str | None, json_output: bool) -> int:
    try:
        keyring = load_keyring(keyring_path)
    except (KeyringError, OSError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-keyring", "message": str(exc)}))
        return 2
    entries = tuple(entry for entry in keyring.entries if corpus_id is None or entry.corpus_id == corpus_id)
    filtered = CorpusKeyring(keyring_version=keyring.keyring_version, entries=entries)
    if json_output:
        print(stable_json_dumps({"ok": True, "keyring": str(keyring_path.resolve()), **keyring_to_jsonable(filtered)}))
        return 0
    print(f"Keyring: {keyring_path}")
    for entry in entries:
        print(f"{entry.corpus_id}  {entry.status.value}  {entry.fingerprint}")
    return 0


def _parse_keyring_labels(values: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise KeyringError("keyring labels must use KEY=VALUE")
        key, item = value.split("=", 1)
        if not key:
            raise KeyringError("keyring label keys must be non-empty")
        labels[key] = item
    return labels


def _run_audit_trajectory(path: Path, *, out_path: Path | None, pretty: bool) -> int:
    destination = write_audit_trajectory(path, out_path)
    if pretty:
        print(json.dumps(to_jsonable(audit_trajectory_rows(path)), indent=2, sort_keys=True))
    else:
        print(f"Trajectory: {destination}")
    return 0


def _run_audit_migrate(
    source: Path,
    *,
    out_path: Path,
    target_schema_version: str,
    target_major: str | None,
    allow_lossy: bool,
    transforms_json: Path | None,
) -> int:
    try:
        report = migrate_audit_tree(
            source,
            out_path,
            target_schema_version=target_schema_version,
            target_major=target_major,
            allow_lossy=allow_lossy,
            transforms_json=transforms_json,
        )
    except AuditMigrationError as exc:
        print(
            stable_json_dumps(
                {
                    "schema_version": "1.0",
                    "ok": False,
                    "reason": "audit-migration-error",
                    "message": str(exc),
                }
            )
        )
        return 2
    print(stable_json_dumps({"ok": True, "migration": audit_migration_report_to_jsonable(report)}))
    return 0


def _run_model_preflight(
    *,
    mode: str,
    backend_ids: list[str],
    replay: Path | None,
    today: str | None,
    out_path: Path | None,
    json_output: bool,
) -> int:
    try:
        report = evaluate_model_backend_preflight(
            mode=mode,
            backend_ids=backend_ids,
            env=os.environ,
            replay_path=replay,
            today=today,
        )
        payload: dict[str, object] = model_backend_preflight_report_to_jsonable(report)
        ok = report.ok
    except (OSError, ModelBackendPreflightError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "mode": mode,
            "error": str(exc),
            "reproduction_claimed": False,
            "boundary": MODEL_BACKEND_PREFLIGHT_BOUNDARY,
        }
        ok = False

    output = stable_json_dumps(payload) + "\n"
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    if json_output or out_path is None:
        print(output, end="")
    return 0 if ok else 2


def _run_audit_verify(
    path: Path,
    *,
    json_output: bool,
    out_path: Path | None,
    strict_migration: bool,
) -> int:
    try:
        report = verify_audit_run(path, strict_migration=strict_migration)
    except AuditCorruptError as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "audit-corrupt",
            "message": str(exc),
        }
        print(stable_json_dumps(payload))
        return 3
    payload = audit_verification_report_to_jsonable(report)
    output = stable_json_dumps(payload) + "\n"
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    if json_output:
        print(output, end="")
    else:
        status = "passed" if report.ok else "failed"
        print(f"Audit verification {status}: {path}")
        print(f"Report hash: {report.report_hash}")
        if out_path is not None:
            print(f"Report: {out_path}")
    return 0 if report.ok else 2


def _run_audit_verify_live(
    audit_dir: Path,
    *,
    live_harbor_audit: Path,
    provenance: Path,
    provenance_signature: Path | None,
    public_key: Path | None,
    require_signature: bool,
    json_output: bool,
    out_path: Path | None,
    strict_migration: bool,
) -> int:
    try:
        report = verify_live_audit_run(
            audit_dir,
            live_harbor_audit=live_harbor_audit,
            provenance=provenance,
            provenance_signature=provenance_signature,
            public_key=public_key,
            require_signature=require_signature,
            strict_migration=strict_migration,
        )
    except AuditCorruptError as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "audit-corrupt",
            "message": str(exc),
            "reproduction_claimed": False,
        }
        print(stable_json_dumps(payload))
        return 3
    payload = live_audit_verification_report_to_jsonable(report)
    output = stable_json_dumps(payload) + "\n"
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    if json_output:
        print(output, end="")
    else:
        status = "passed" if report.ok else "blocked"
        print(f"Live audit verification {status}: {audit_dir}")
        print(f"Mode: {report.mode}")
        print(f"Report hash: {report.report_hash}")
        if out_path is not None:
            print(f"Report: {out_path}")
    return 0 if report.ok else 2


def _run_inspect_harness(path: Path, *, out_path: Path | None, json_output: bool, pretty: bool) -> int:
    if json_output:
        inspection = inspect_harness_run(path)
        if pretty:
            print(json.dumps(to_jsonable(inspection), indent=2, sort_keys=True))
        else:
            print(stable_json_dumps(inspection))
        return 0
    destination = write_harness_inspection(path, out_path)
    print(f"Harness inspection: {destination}")
    return 0


def _run_audit_diff(left: Path, right: Path, *, json_output: bool) -> int:
    diff = diff_audit_runs(left, right)
    if json_output:
        print(stable_json_dumps(diff))
    elif diff.equal:
        print("Audit runs match")
    else:
        print("Audit runs differ")
        for label, files in [
            ("changed", diff.changed_files),
            ("missing_from_left", diff.missing_from_left),
            ("missing_from_right", diff.missing_from_right),
        ]:
            if files:
                print(f"{label}: {', '.join(files)}")
    return 0 if diff.equal else 1


def _run_benchmark_report(audit_dir_args: list[str], out_path: Path) -> int:
    audits = _parse_audit_dir_args(audit_dir_args)
    destination = write_benchmark_report(audits, out_path, reproduction_claimed=False)
    print(f"Benchmark report: {destination}")
    return 0


def _run_harbor_inspect(run_dir: Path, *, out_path: Path | None, json_output: bool) -> int:
    inspection = inspect_run_dir(run_dir)
    if json_output:
        print(stable_json_dumps(inspection))
        return 0
    destination = out_path or run_dir / "harbor_inspection.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(stable_json_dumps(inspection) + "\n", encoding="utf-8")
    print(f"Harbor inspection: {destination}")
    return 0


def _run_harbor_ingest(run_dir: Path, *, manifest: Path, out_dir: Path, dataset: str) -> int:
    destination = ingest_harbor_run(run_dir, manifest, out_dir, dataset=dataset)
    print(f"Harbor artifact audit: {destination}")
    print("This is not a Terminal-Bench reproduction.")
    return 0


def _run_terminal_bench(
    dataset: str,
    manifest: Path,
    fixture_dir: Path | None,
    corpus_cache: Path | None,
    harbor_executable: str,
    docker_executable: str,
    agent: str,
    model: str,
    n_concurrent: int,
    cloud_env: str | None,
    keep_run_dir: Path | None,
    image_policy_path: Path | None,
    trusted_image: str | None,
    trusted_image_digest: str | None,
    require_image_digest: bool,
    require_uv: bool,
    require_docker: bool,
    mode: str,
    rounds: int,
    seed: int,
    out_dir: Path,
    evaluation_repeats: int,
    max_proposals: int,
    max_payload_bytes: int,
) -> int:
    if mode == "dry-run" and fixture_dir is None:
        raise SystemExit("--fixture-dir is required for terminal-bench --mode dry-run")
    try:
        image_policy = load_image_policy(image_policy_path) if image_policy_path is not None else None
        validate_harbor_image_trust(
            image_policy,
            trusted_image=trusted_image,
            trusted_image_digest=trusted_image_digest,
            require_image_digest=require_image_digest,
        )
    except ImagePolicyError as exc:
        print(stable_json_dumps({"ok": False, "reason": "invalid-verifier", "message": str(exc)}))
        return 2
    if mode == "live":
        report = run_preflight(
            dataset,
            harbor_executable=harbor_executable,
            docker_executable=docker_executable,
            corpus_cache=corpus_cache,
            require_docker=require_docker,
            require_uv=require_uv,
        )
        write_preflight_report(out_dir / "preflight.json", report)
        if not report.passed:
            print(stable_json_dumps(report))
            return 2
    corpus = load_terminal_bench_manifest(manifest)
    config = EngineConfig(
        rounds=rounds,
        seed=seed,
        evaluation_repeats=evaluation_repeats,
        proposal_budget=ProposalBudget(
            max_proposals=max_proposals,
            max_payload_bytes=max_payload_bytes,
        ),
        schema_version="1.3",
        model_id=f"harbor-{mode}-runner",
        benchmark_metadata={
            "benchmark_protocol": dataset,
            "benchmark_dataset_version": corpus.corpus_id,
            "benchmark_dataset": corpus.corpus_id,
            "harbor_version": "dry-run" if mode == "dry-run" else "unknown-live",
            "container_image_digest": "dry-run" if mode == "dry-run" else "unknown-live",
            "reproduction_claimed": False,
        },
    )
    engine = SelfHarnessEngine(
        tasks=corpus.tasks,
        runner=HarborRunner(
            dataset=dataset,
            mode=cast(RunnerMode, mode),
            fixture_dir=fixture_dir,
            harbor_executable=harbor_executable,
            corpus_cache=corpus_cache,
            model=model,
            n_concurrent=n_concurrent,
            cloud_env=cloud_env,
            agent_adapter=_agent_adapter(agent),
            keep_run_dir=keep_run_dir,
            image_policy=image_policy,
            trusted_image=trusted_image,
            trusted_image_digest=trusted_image_digest,
            require_image_digest=require_image_digest,
        ),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=config,
    )
    try:
        summaries = engine.run()
    except ImagePolicyError as exc:
        shutil.rmtree(out_dir / "rounds", ignore_errors=True)
        print(stable_json_dumps({"ok": False, "reason": "invalid-verifier", "message": str(exc)}))
        return 2
    print("Self-Harness experimental Terminal-Bench adapter run complete")
    print("This is not a Terminal-Bench reproduction.")
    _print_summaries(summaries)
    print(f"Artifacts: {out_dir}")
    return 0


def _run_terminal_bench_preflight(
    dataset: str,
    manifest: Path,
    corpus_cache: Path | None,
    out_dir: Path,
    harbor_executable: str,
    docker_executable: str,
    require_uv: bool,
    require_docker: bool,
    json_output: bool,
) -> int:
    load_terminal_bench_manifest(manifest)
    report = run_preflight(
        dataset,
        harbor_executable=harbor_executable,
        docker_executable=docker_executable,
        corpus_cache=corpus_cache,
        require_docker=require_docker,
        require_uv=require_uv,
    )
    write_preflight_report(out_dir / "preflight.json", report)
    if json_output:
        print(stable_json_dumps(report))
    else:
        print("Terminal-Bench live preflight passed" if report.passed else "Terminal-Bench live preflight failed")
        print(f"Report: {out_dir / 'preflight.json'}")
    return 0 if report.passed else 2


def _run_terminal_bench_capture(
    dataset: str,
    manifest: Path,
    task_id: str,
    fixture_out: Path,
    corpus_cache: Path | None,
    harbor_executable: str,
    docker_executable: str,
    require_docker: bool,
) -> int:
    report = run_preflight(
        dataset,
        harbor_executable=harbor_executable,
        docker_executable=docker_executable,
        corpus_cache=corpus_cache,
        require_docker=require_docker,
    )
    write_preflight_report(fixture_out / "preflight.json", report)
    if not report.passed:
        print(stable_json_dumps(report))
        return 2
    capture = capture_single_task(
        dataset,
        manifest,
        task_id,
        fixture_out,
        harbor_executable=harbor_executable,
        corpus_cache=corpus_cache,
    )
    print(stable_json_dumps(capture))
    print(
        "Replay after collecting fixtures for every manifest task, or with a task-scoped manifest: "
        f"self-harness terminal-bench --mode dry-run --dataset {dataset} "
        f"--manifest {manifest} --fixture-dir {fixture_out}"
    )
    return 0


def _resolve_local_corpus_path(
    parser: argparse.ArgumentParser,
    positional_path: Path | None,
    corpus_path: Path | None,
) -> tuple[Path, bool]:
    if positional_path is not None and corpus_path is not None:
        parser.error("local-demo accepts either a positional path or --corpus, not both")
    if corpus_path is not None:
        return corpus_path, False
    if positional_path is not None:
        return positional_path, True
    parser.error("local-demo requires a task corpus path")


def _parse_audit_dir_args(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if ":" not in value:
            raise SystemExit("--audit-dir must be shaped as model_label:path")
        label, path = value.split(":", 1)
        if not label or not path:
            raise SystemExit("--audit-dir must include a non-empty model_label and path")
        if label in parsed:
            raise SystemExit(f"duplicate benchmark-report model label: {label}")
        parsed[label] = Path(path)
    return parsed


def _agent_adapter(agent: str) -> ClaudeCodeAgentAdapter | DeepAgentAdapter:
    if agent == "claude-code":
        return ClaudeCodeAgentAdapter()
    return DeepAgentAdapter(agent_name=agent)


def _print_summaries(summaries: list[RoundSummary]) -> None:
    print("round  before_in  before_out  proposals  accepted  rejected  after_in  after_out")
    for summary in summaries:
        print(
            f"{summary.round:<5}  "
            f"{summary.baseline_held_in:<9}  "
            f"{summary.baseline_held_out:<10}  "
            f"{summary.proposals:<9}  "
            f"{summary.accepted:<8}  "
            f"{summary.rejected:<8}  "
            f"{summary.after_held_in:<8}  "
            f"{summary.after_held_out:<9}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
