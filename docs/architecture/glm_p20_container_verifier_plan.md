CONVERGED: YES

## Verdict
The slice is well-scoped and aligns with established P18/P19 verifier adapter seams. It can be converged in round 1 because (a) the repository already contains the trust-boundary pattern from `HttpVerifierTaskAdapter`, (b) Docker preflight machinery already exists in `terminal_bench/preflight.py` and can be generalized or reused, and (c) the "honest boundary" requirement maps cleanly onto a `dry-run` command-construction mode plus operator-supplied image. Remaining questions are non-blocking design choices that can be resolved during implementation without altering the architecture.

## Critique
- **Evidence**: `HttpVerifierTaskAdapter` / `HttpVerifierRunner` establish the exact sibling shape to mirror: operator-only trust argument, closed `FailureCategory` mapping, fresh per-attempt workdir, deterministic request body, strict structured result validation, no audit schema change.
- **Evidence**: `terminal_bench/preflight.py` already implements Docker CLI presence + daemon reachability checks with a typed `PreflightReport`. This is the right machinery for "failures must produce machine-readable preflight evidence"; a shared or parallel container verifier preflight is warranted.
- **Inference**: A `ContainerVerifierTaskAdapter` + `ContainerVerifierRunner` with a `mode: Literal["dry-run", "live"]` (mirroring `HarborRunner`) is the lowest-risk seam. Dry-run builds a deterministic `docker run` command spec and emits structured command evidence without executing; live is guarded behind preflight and is only exercised on a provisioned host.
- **Critique of alternatives**:
  - *Full Docker run now*: Blocked by stated environment constraints and would couple local CI to a daemon. Reject for this slice; the dry-run/live seam defers it correctly.
  - *KMS/HSM key management*: Orthogonal; belongs to a future provenance slice, not verifier execution. Out of scope.
  - *Migration shims*: Premature; no audit schema change is required, so no shim is needed.
  - *More HTTP verifier work*: P19 already covers HTTP; container boundary is a distinct trust surface and should not be folded into HTTP.
- **Risk**: Two preflight implementations could diverge. Recommend extracting the docker executable/daemon checks into a shared helper or reusing the terminal_bench preflight module rather than duplicating.
- **Risk**: Image trust boundary must be explicit. Corpus JSON must not select images; only `--trust-container-image` from the operator can, with optional `--trust-container-image-digest` pinning. Corpus `verifier_selector` is opaque metadata only.
- **Risk**: Dry-run must be distinguishable in audit so it cannot masquerade as live execution. Manifest `model_id` should encode `container-verifier-dry-run` and `benchmark_metadata` (if used) must not set reproduction flags.

## Required Changes
None blocking. The following are binding design constraints for the implementer:
1. Reuse existing `TaskAdapter` / `Runner` protocols; do not introduce a new evaluation contract.
2. Corpus metadata whitelist: allow `verifier_selector` (opaque, length-bounded), `workspace_template`; forbid `image`, `container_image`, `digest`, `entrypoint`, `command`, and any `docker_*` keys unless an explicit operator override flag is present.
3. Operator trust flag is required and non-empty: `--trust-container-image IMAGE[:TAG]` (+ optional `--trust-container-image-digest` for pinning). No default image.
4. Produce machine-readable preflight evidence on failure by extending or delegating to the existing preflight machinery; the report must be written before any engine round and the CLI must exit non-zero without writing partial rounds.
5. No audit schema bump; container command details live only in `RunRecord.trace`, not audit JSONL (consistent with P19).
6. CLI output must include the standard "This is not a benchmark reproduction." disclaimer.

## Revised Plan

### API surface
- `src/self_harness/adapters/container_verifier.py`
  - `@dataclass(frozen=True) ContainerVerifierTaskAdapter(TaskAdapter)`:
    - `image: str`, `image_digest: str | None`, `command: tuple[str, ...]`, `timeout_seconds: float`, `keep_workdir: bool`, `extra_env: tuple[tuple[str,str], ...]`, `workdir_template_key: str | None`
    - `load(corpus)` validates task metadata whitelist and `verifier_selector` shape; rejects corpus-supplied images/commands.
    - `runner()` returns `ContainerVerifierRunner`.
  - `@dataclass(frozen=True) ContainerVerifierRunner(Runner)`:
    - Same construction fields plus `mode: Literal["dry-run","live"] = "dry-run"`.
    - `build_command(task, workdir, attempt_index) -> ContainerCommandSpec`: deterministic `docker run --rm --workdir /work -v <workdir>:/work [-e KEY=VAL...] <image>[@digest] <command...>`; never executes in dry-run.
    - `run(...)`:
      - dry-run: emit trace events for workdir creation, template copy, and the constructed command spec; produce a synthetic `VerifierOutcome` with `mechanism="container-dry-run"` and `terminal_cause` mapped from `verifier_selector` (or `verifier-pass`/`verifier-fail` sentinel). Pass/fail is fixture-derived (mirror Harbor dry-run fixture approach) or always-fail-closed if no fixture.
      - live: run preflight; if preflight fails, write report and return `FailureCategory.ENVIRONMENT_ERROR` outcome (do not raise). If preflight passes, execute via `subprocess.run`, parse structured JSON result from container stdout (same contract as HTTP/Python verifier), map categories closed.
- `ContainerCommandSpec` dataclass with stable JSON serialization (mirror `HarborCommandSpec`).
- `src/self_harness/adapters/container_preflight.py` (or reuse terminal_bench preflight via import):
  - `run_container_preflight(image, *, docker_executable, require_daemon, require_image_present=False) -> PreflightReport`.
  - Adds optional `image_present` check (`docker image inspect`) that is skipped unless `--require-image-present` is supplied; default off to keep tests daemon-free.

### CLI
- `self-harness container-demo CORPUS --trust-container-image IMAGE [--trust-container-image-digest DIGEST] [--mode dry-run|live] [--container-command "verify"] [--timeout-seconds N] [--header-style-env KEY=VAL ...] [--fixture-dir DIR] [--keep-workdir] [--skip-docker-preflight] [--require-corpus-signature PATH | --require-corpus-keyring PATH] ...standard engine flags...`
- Mirrors `http-demo` / `python-demo` ergonomics and trust group.
- Requires `--trust-container-image`; exits 2 with structured JSON error if missing.
- Writes `runs/container-demo/preflight.json` when live preflight fails; exits 2 before engine loop.

### Tests (all daemon-free)
1. `test_container_verifier_metadata_whitelist`: corpus-supplied `image`/`command` rejected via `TaskLoadError`.
2. `test_container_verifier_dry_run_command_spec`: dry-run produces stable `docker run ...` command bytes across runs; assert determinism.
3. `test_container_verifier_dry_run_fixture_replay`: fixture-driven pass/fail mapping via `verifier_selector` and `--fixture-dir` (parallel to Harbor dry-run).
4. `test_container_verifier_preflight_failure_emits_report`: missing docker executable -> report written, exit 2, no rounds directory.
5. `test_container_verifier_live_uses_fake_docker`: fake `docker` shell script prints structured JSON result; assert parsed outcome and that `docker run` argv contains pinned digest when supplied.
6. `test_container_demo_cli_trust_boundary`: missing `--trust-container-image` exits 2; valid dry-run writes manifest `model_id="container-verifier-dry-run"` and emits disclaimer.
7. `test_container_verifier_engine_audit_determinism`: two dry-run engine invocations produce byte-identical audit trees (canonical hash fixture optional).

### Docs
- `docs/architecture/p20_container_verifier_brief.md`: status, purpose, trust boundary, deferred (KMS, mTLS, registry auth, reproduction claims), schema note ("no audit schema change").
- README section "Container verifier (trusted, dry-run default)" with CLI example and explicit non-reproduction disclaimer.
- Update `docs/architecture/productionization_brief.md` "Remaining production work" list to mark container verifier boundary as implemented.

### Stop conditions
- Slice is done when: dry-run path is daemon-free and deterministic; live path is guarded by preflight and parses structured container output; corpus cannot select images or commands; failures produce machine-readable preflight JSON; canonical audit hash fixture (if added) is stable; README + brief document the boundary and limitations.
- Slice is explicitly NOT done if it claims benchmark reproduction, adds an audit schema version bump, or adds a hard `docker` SDK dependency.

### Out of scope
- KMS/HSM key management, registry authentication, mTLS, image vulnerability scanning, async execution, reproduction claims, full Harbor/Docker benchmark reproduction, audit schema migration shims.

## Remaining Open Questions
1. Should container preflight live in a shared module or remain under `terminal_bench/preflight.py` with re-exports? **Non-blocking**: implementer may choose; recommend extracting docker checks to `adapters/container_preflight.py` and having terminal_bench re-export for compatibility.
2. Should dry-run without a fixture directory fail-closed (always `verifier-fail`) or refuse to run? **Non-blocking**: recommend fail-closed with `mechanism="container-dry-run-no-fixture"` and a trace event, mirroring conservative verifier semantics.
3. Should `--container-command` be a single string or a JSON argv? **Non-blocking**: recommend single string split with `shlex.split` for operator ergonomics, validated to be non-empty.
4. Should canonical readiness hash coverage include the container-verifier path now or defer to a follow-up? **Non-blocking**: recommend including now to preserve the existing readiness invariant discipline.
