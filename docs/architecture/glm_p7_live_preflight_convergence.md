# Round 1

CONVERGED: YES

## Verdict

P7 should be a **Terminal-Bench live-capture preflight + capture-path slice**. The local machine cannot honestly produce a live Harbor run this turn (Docker daemon unreachable, `harbor` absent, `uv` absent), so the highest-value slice is one that converts that runtime blocker into machine-checkable evidence and a concrete future capture path, without ever claiming reproduction. This is within architect authority to specify; no external evidence beyond what is already cited is required to plan it.

Evidence (verified from repo):
- `src/self_harness/adapters/terminal_bench/runner.py` already has a `_live_run` path and a `_environment_error_record` fallback for a missing `harbor` executable. P6 deliberately deferred live capture to P7.
- `cli.py` wires `terminal-bench --mode live` but nothing checks preconditions before launch; failures are emitted as `ENVIRONMENT_ERROR` evaluation rows only after attempt time.
- `engine.py::validate_benchmark_claims` and `readiness.py::audit_tree_hash` give us the existing invariant + determinism scaffolding P7 can reuse.
- `RELEASE.md` permits schema-minor bumps for additive fields with a changelog entry, which is what a preflight report artifact needs.
- README already states "Live Harbor execution is best-effort until a captured live run fixture exists" — P7 is exactly the slice that produces and validates that fixture.

Inference: a `preflight` command plus a `capture` command (gated on preflight) is the minimal, paper-faithful way to convert the runtime blocker into evidence. It does not require Harbor, Docker, or uv to be present at planning time; it requires only that the checks themselves be machine-checkable.

## Critique

- A live capture slice that just attempts `harbor run` again would not advance anything this turn; the same blockers would reproduce. The correct move is to make the blockers themselves the artifact.
- The current `_live_run` only records a `FileNotFoundError`-style environment error *per task*. That is too late for honest reporting: it pollutes evaluation rows and conflates "environment not provisioned" with "verifier rejected the agent." P7 must check the environment *before* the engine loop starts and refuse to write a `benchmark_protocol` audit when preflight fails in live mode.
- A pure preflight slice with no capture path would defer the blocker rather than convert it. The capture subcommand is what makes the slice a real step toward a future live fixture.
- Cross-Python hash matrix and corpus signing are valuable but do not unblock the explicit P7 ask. They should remain P8.
- Schema churn risk: adding preflight fields to the manifest is fine (additive, schema-minor 1.4), but preflight reports must *not* perturb the canonical toy hash or the TB dry-run hash. Keep them in a sibling artifact, not in `manifest.json`, unless they are null in non-preflight runs.

## Required Changes

1. **Preflight must gate live mode, not just decorate it.** `self-harness terminal-bench --mode live` must run preflight and exit non-zero with a structured report before the engine loop when any hard precondition fails. Dry-run mode must remain preflight-free so the existing deterministic tests and canonical hash are untouched.
2. **Capture path must be reproducible from a recorded fixture, not from a live-only contract.** Introduce a `capture` mode that, when preflight passes, runs a single task through `HarborRunner._live_run`, snapshots stdout/stderr/exit into a fixture file shape already consumed by `_dry_run` (i.e. `pass_if_config_contains` or `passed` + `trace`), and writes a capture manifest. Future dry-run replays can then consume that fixture.
3. **No new hard runtime dependencies.** Preflight probes must use `shutil.which`, `subprocess.run([...], "--version")`, Docker `info`/`ping`, etc., and degrade gracefully. The core install must still import without `harbor`, `uv`, or `docker` SDKs.
4. **No reproduction claim.** Captured fixtures must carry `reproduction_claimed=false` and `capture_source="single-task-harbor-run"` provenance. Readiness invariant must extend to reject any audit or fixture combining `benchmark_protocol="terminal-bench@2.0"` with `reproduction_claimed=true` regardless of artifact kind.
5. **Schema discipline.** New fields go in a sibling `preflight.json` and `capture_manifest.json`, not in `manifest.json`. If any field must enter `manifest.json`, bump to schema `1.4` (additive), add a changelog entry, and verify the canonical toy hash and TB dry-run hash are unchanged.

## Revised Plan

**P7 — Terminal-Bench live preflight and capture-path scaffolding (no reproduction claim)**

Scope:
- `src/self_harness/adapters/terminal_bench/preflight.py`
  - `@dataclass(frozen=True) PreflightReport` with `checks: list[PreflightCheck]`, `passed: bool`, `schema_version`, `generated_at`-free (deterministic).
  - `PreflightCheck(name, status ∈ {"pass","fail","skipped"}, detail, required_for_live: bool)`.
  - `run_preflight(dataset, harbor_executable, require_docker=True, require_uv=False) -> PreflightReport`.
  - Checks: `harbor_present` (`shutil.which`), `harbor_version` (`harbor --version`), `docker_cli_present`, `docker_daemon_reachable` (`docker info --format {{.ServerVersion}}` with short timeout), `uv_present` (skipped unless requested), `dataset_cache_writable` (when `--corpus-cache` given).
- `src/self_harness/adapters/terminal_bench/capture.py`
  - `capture_single_task(dataset, manifest, task_id, harbor_executable, corpus_cache, fixture_out_dir) -> CaptureManifest`.
  - Reuses `HarborRunner._live_run` plumbing; writes a fixture file at `fixture_out_dir/<task_id>.json` with shape `{passed, terminal_cause, mechanism, message, trace, capture_source, harbor_version, container_image_digest_or_unknown, reproduction_claimed:false, captured_at_epoch: <int>}`. (`captured_at_epoch` allowed because capture fixtures are explicitly non-deterministic; dry-run replay hashes remain stable because capture fixtures are opt-in inputs, not part of the canonical hash suite.)
  - `CaptureManifest(corpus_id, task_id, harbor_version, container_image_digest, fixture_path, reproduction_claimed=False, capture_kind="single-task")`.
- CLI:
  - `self-harness terminal-bench-preflight --dataset ... --manifest ... [--corpus-cache ...] [--require-uv] [--json]`. Writes `preflight.json` to `--out` (default `runs/tb-preflight`), exits non-zero if any `required_for_live` check fails.
  - `self-harness terminal-bench --mode live ...` now runs preflight internally; on hard failure, writes `preflight.json` next to the would-be audit dir and exits 2 without running the engine.
  - `self-harness terminal-bench-capture --dataset ... --manifest ... --task <id> --fixture-out <dir> [--corpus-cache ...]`. Requires preflight to pass. Emits fixture + `capture_manifest.json`. Does not run the engine. Prints a one-line "replay with `terminal-bench --mode dry-run --fixture-dir <dir>`" hint.
- Audit:
  - Sibling artifacts only. `manifest.json` unchanged unless we add `preflight_passed: bool | null` (nullable), in which case bump to schema `1.4` with changelog. Default decision: keep `manifest.json` unchanged; preflight status lives in `preflight.json`.
  - Update `RELEASE.md`/`schema_changelog.md` only if schema 1.4 is needed.
- Readiness invariants (extend existing suite):
  - Captured fixtures and capture manifests with `benchmark_protocol="terminal-bench@2.0"` may not set `reproduction_claimed=true`.
  - Preflight artifact must round-trip through a typed loader (mirror `audit.py` discipline).
  - Dry-run canonical hash and toy canonical hash must remain byte-identical.
- Tests:
  - Preflight report shape and status transitions using monkeypatched `shutil.which`/`subprocess.run`.
  - Live mode refuses to start when preflight hard-fails; writes `preflight.json`; exits 2; no `rounds/` dir created.
  - Dry-run mode is unaffected by preflight (regression guard).
  - Capture path produces a fixture that the existing dry-run `_fixture_passed` logic consumes correctly (synthetic capture, no real Harbor).
  - Reproduction-claim invariant trips on a crafted bad capture manifest.
- Docs:
  - `docs/architecture/p7_terminal_bench_preflight_brief.md` stating non-reproduction, machine-checkable preconditions, and the future capture → dry-run replay workflow.
  - README "Experimental Terminal-Bench integration" subsection gains a "Live capture path" paragraph that names the missing-runtime case explicitly and points to `terminal-bench-preflight`.

Acceptance criteria:
- `make check && make readiness` pass with no canonical-hash rotation (toy or TB dry-run).
- On this machine (Docker daemon down, no `harbor`, no `uv`), `self-harness terminal-bench-preflight` produces a structured report whose `passed=false` and whose failing checks include `docker_daemon_reachable`, `harbor_present`. Exits non-zero.
- `self-harness terminal-bench --mode live ...` on this machine exits 2 with `preflight.json` written and no engine round executed.
- `self-harness terminal-bench --mode dry-run ...` still produces the existing schema-1.3 audit byte-for-byte (regression test).
- Capture path is unit-tested with a synthetic Harbor stand-in; no real Harbor required for the gate.
- No file in the repo claims Terminal-Bench reproduction; readiness invariant covers manifests, capture manifests, and captured fixtures.

Out of scope (deferred):
- Real live Harbor capture on a provisioned host (must wait for environment).
- Cross-Python canonical hash matrix — P8.
- Corpus signing — P8.
- Major-version audit migration shims — when needed.
- Non-subprocess verifier adapters beyond TB2 needs — P8.
- Full published TB2 task-set reproduction — never implied by this line of work.

## Remaining Open Questions

None blocking. The Harbor CLI stdout/stderr contract for a real capture is unknown, but P7 deliberately does not require a real capture to pass the gate: it requires that the preflight report correctly reflect the missing runtime and that the capture path be exercisable via a synthetic stand-in. The first real capture on a provisioned host will refine the fixture writer; that is execution work, not a planning gap.

[usage] {"completion_tokens": 2849, "completion_tokens_details": {"reasoning_tokens": 491}, "prompt_tokens": 21206, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 24055}
