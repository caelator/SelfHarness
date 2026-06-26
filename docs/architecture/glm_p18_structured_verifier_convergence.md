# Round 1

CONVERGED: NO

## Verdict
The slice is well-bounded and the codebase has the right seams (`Runner`, `TaskAdapter`, `FailureCategory`, `VerifierOutcome`) already in place (evidence: `evaluation.py`, `adapters/base.py`, `adapters/local_subprocess.py`, `types.py`). An in-process trusted Python adapter can be added as a sibling adapter with no protocol change and no audit schema change. Round 1 produces a complete initial plan; it needs one revision pass on trust semantics and structured-outcome mapping before execution.

## Critique
Design strengths (inference from evidence):
- `Runner` and `TaskAdapter` are pure protocols (`evaluation.py`, `adapters/base.py`), so a new `InProcessPythonTaskAdapter` + `InProcessPythonRunner` fits the existing boundary with no change to `SelfHarnessEngine`.
- `VerifierOutcome` is a closed frozen dataclass with four required fields (`passed`, `terminal_cause`, `causal_status`, `mechanism`, `message`); the local subprocess adapter already maps `FailureCategory` enum values into `terminal_cause` via `.value`, so an in-process adapter can reuse the exact mapping convention without audit schema change.
- `Task.metadata` is a dict, so per-task verifier selectors can ride there; but the trust boundary MUST NOT be a string read directly from corpus JSON and executed as code. The corpus can only carry a stable *selector*; the operator supplies the trusted module path on the CLI / config.
- `LocalSubprocessRunner` already demonstrates the per-attempt `mkdtemp` workdir + cleanup pattern and the in-memory-only `trace` rule; the new adapter should copy that lifecycle for determinism.

Risks / gaps requiring decisions before execution:
1. **Trust source.** Loading a Python module by dotted path or filesystem path is arbitrary code execution. The CLI must require an explicit `--trust-verifier-module PATH` (mutually exclusive with reading any `verifier_module` key from the corpus). Corpus JSON may carry only an opaque `verifier_selector` string for the trusted module to interpret; it MUST NOT name a callable to import.
2. **Verifier contract.** A typed Protocol is needed so the trusted module's surface is stable. Returning a raw bool is insufficient because mining groups on `terminal_cause|causal_status|mechanism`. The module should return a structured object the harness maps to `VerifierOutcome` with explicit category validation against `FailureCategory`.
3. **Unknown category handling.** If a trusted verifier emits a category not in the closed `FailureCategory` enum (e.g. `partial-pass`), the adapter must fail closed into a generic `VerifierOutcome(passed=False, ... VERIFIER_FAIL ...)` or raise `SelfHarnessError` — not silently leak arbitrary strings into audit (which would change audit semantics without schema bump).
4. **Setup hook ordering and isolation.** An operator-provided setup hook must run in the fresh workdir before the verifier and must not outlive the attempt; no module-level mutable state may carry between attempts (import once, call per attempt).
5. **Determinism of trace metadata.** Workdir path, tmp paths, and wallclock must not enter audit; they live only on `RunRecord.trace` exactly as in `LocalSubprocessRunner`.
6. **No regression of `local-demo`.** The new adapter ships under a separate CLI entrypoint (e.g. `python-demo`) and a separate `TaskAdapter` implementation; `local-demo` behavior stays byte-stable.
7. **Aggregate validation.** `evaluate()` already iterates `attempt_index`, so the new runner must accept and echo `attempt_index` and produce a fresh workdir per attempt — verifiable by reusing the existing `evaluate(..., repeats=N)` shape.
8. **Demo fixture parity.** There must be a deterministic in-process verifier fixture module shipped under `tests/fixtures/` that the engine determinism test exercises; otherwise we are shipping an adapter with no canonical hash anchor.

## Required Changes
1. Define a typed `InProcessVerifier` Protocol in `adapters/in_process_python.py`:
   ```python
   class VerifierResult(TypedDict):
       passed: bool
       failure_category: str | None  # must be a FailureCategory value or None
       mechanism: str
       message: str

   class InProcessVerifier(Protocol):
       def verify(self, task: Task, workdir: Path, attempt_index: int) -> VerifierResult: ...

   class InProcessSetupHook(Protocol):
       def setup(self, task: Task, workdir: Path, attempt_index: int) -> None: ...
   ```
2. Loader: explicit `--trust-verifier-module PATH` (dotted or file). If a corpus carries a key like `verifier_selector`, it is passed through as an opaque string into the verifier call; it MUST NOT be used to choose code to import.
3. Mapping rule:
   - `failure_category in FailureCategory.__members__.values()` → use as-is.
   - `passed=True` → `terminal_cause="verifier-pass"`, `causal_status="confirmed"`, `mechanism="in-process-verifier"`.
   - `passed=False` and `failure_category is None` → `VERIFIER_FAIL` / `rejected` / `in-process-verifier`.
   - Unknown category string → raise `SelfHarnessError("invalid-failure-category")` (fail closed; prevents silent audit drift).
4. Workdir lifecycle: identical to `LocalSubprocessRunner` (mkdtemp per attempt, optional `workspace_template` copy, `keep_workdir` flag, `finally` cleanup).
5. CLI: new subcommand `python-demo CORPUS --trust-verifier-module PATH [--setup-symbol NAME] [--verifier-symbol NAME] [--keep-workdir] [--out PATH]`. Refuses to run if `--trust-verifier-module` is absent. The corpus path still supports the existing signature/keyring gates.
6. Tests:
   - `test_in_process_python.py`: pass/fail/timeout-by-exception/unknown-category/selector-passthrough/setup-hook/fresh-workdir-per-attempt/engine-loop determinism.
   - Add a shipped fixture module `tests/fixtures/in_process_verifier.py` with `verify` and an optional `setup`.
   - Add canonical audit hash fixture for one `python-demo` invocation so readiness gating covers it.
7. Docs: README section "In-process trusted verifier (P18)" with explicit "this is not a benchmark reproduction" and the trust warning. Update `docs/architecture/productionization_brief.md` "Remaining production work" line to remove "richer non-subprocess verifier adapters."
8. Stop condition for P18: one trusted in-process adapter shipped; CLI gates trust via explicit module path; `FailureCategory` mapping is closed; `local-demo` and `demo` hashes unchanged.

## Revised Plan

**P18 slice: trusted in-process Python verifier adapter**

1. **New module** `src/self_harness/adapters/in_process_python.py`:
   - `InProcessVerifier` Protocol (`verify(task, workdir, attempt_index) -> VerifierResult`).
   - `InProcessSetupHook` Protocol (`setup(task, workdir, attempt_index) -> None`).
   - `VerifierResult` TypedDict (`passed`, `failure_category`, `mechanism`, `message`).
   - `load_trusted_module(path_or_dotted: str) -> module` via `importlib.util.spec_from_file_location` for filesystem paths or `importlib.import_module` for dotted paths; rejects loading by corpus-supplied string.
   - `InProcessPythonTaskAdapter(TaskAdapter)`:
     - Constructor: `module_path: str`, `verifier_symbol: str = "verify"`, `setup_symbol: str | None = "setup"`, `keep_workdir: bool = False`.
     - `load(corpus) -> list[Task]` returns `corpus.tasks`.
     - `runner() -> InProcessPythonRunner` resolves symbols once.
   - `InProcessPythonRunner(Runner)`:
     - Per attempt: `mkdtemp`, optional `workspace_template` copy, optional `setup` hook, `verify` call, map to `VerifierOutcome` via the required-changes rules above.
     - Trace events: `workspace`, `setup`, `verify` with deterministic metadata (command/exception type, no wallclock, no absolute paths beyond the in-memory workdir).
     - Exceptions from `setup`/`verify` map to `VerifierOutcome(passed=False, terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value, causal_status="environment", mechanism="verifier-exception", message=exc.__class__.__name__)`.
     - `keep_workdir` behavior mirrors `LocalSubprocessRunner`.

2. **CLI** `src/self_harness/cli.py`:
   - New subcommand `python-demo CORPUS --trust-verifier-module PATH [--verifier-symbol NAME] [--setup-symbol NAME] [--out PATH] [--rounds N] [--seed N] [--evaluation-repeats N] [--max-proposals N] [--max-payload-bytes N] [--keep-workdir] [--require-corpus-signature PATH | --require-corpus-keyring PATH]`.
   - Hard error if `--trust-verifier-module` absent.
   - Wires `load_corpus` (with trust gates), `InProcessPythonTaskAdapter`, `HeuristicProposer`, `SelfHarnessEngine`.
   - Output explicitly labeled: `"This is not a benchmark reproduction."`
   - Audit schema version stays `1.2` (no schema change).
   - `model_id="in-process-python-verifier"`.

3. **Tests** `tests/`:
   - Fixture module `tests/fixtures/in_process_verifier.py` with:
     - `def verify(task, workdir, attempt_index)`: inspects `task.metadata["verifier_selector"]` and returns a deterministic `VerifierResult`.
     - `def setup(task, workdir, attempt_index)`: writes a marker file.
   - `tests/test_in_process_python.py`:
     - Pass / fail / unknown-category / selector-passthrough / setup-hook / fresh-workdir-per-attempt / verifier-exception / setup-exception.
     - Engine loop determinism: identical corpus + identical trusted module → byte-identical audit JSONL across two runs.
   - Extend readiness / canonical hash coverage for one `python-demo` invocation if the project gates readiness on a closed set of audit hashes.

4. **Docs**:
   - README: new section "In-process trusted verifier (P18)" with trust warning, example invocation, "not a benchmark reproduction."
   - `productionization_brief.md`: move "richer non-subprocess verifier adapters" out of remaining production work.
   - Architecture note: `verifier_selector` in corpus JSON is opaque to the harness; only the trusted module may interpret it.

5. **Stop conditions**:
   - Trusted in-process adapter shipped as a sibling of `LocalSubprocessRunner`.
   - CLI gates trust via explicit module path; corpus JSON never selects executable code.
   - `FailureCategory` mapping is closed and validated; unknown categories fail closed.
   - `local-demo`, `demo`, Terminal-Bench dry-run canonical hashes unchanged.
   - No audit schema change.

6. **Out of scope**:
   - Network/HTTP verifiers.
   - Container/Docker verifiers (covered by Harbor adapter).
   - KMS/HSM-backed signing key management.
   - Any benchmark reproduction claim.
   - Migration shims for unsupported schema versions.

## Remaining Open Questions
1. (Likely non-blocking) Should the verifier Protocol support async, or stay synchronous for determinism parity with `LocalSubprocessRunner`? Inference: keep synchronous for P18; revisit only if a concrete async verifier requirement appears.
2. (Likely non-blocking) Should `verifier_selector` be reserved metadata validated against a regex/length cap, or fully opaque? Inference: enforce `str`, non-empty, max 256 chars; do not interpret.
3. (Non-blocking) Whether readiness canonical-hash coverage should include a `python-demo` run. Recommendation: include it; can be added in the same slice without protocol change.
4. (Non-blocking, future) Whether to publish a `self_harness.contrib.verifiers` registry package so trusted modules can be discovered without filesystem paths. Out of scope for P18.

[usage] {"completion_tokens": 2915, "completion_tokens_details": {"reasoning_tokens": 303}, "prompt_tokens": 19611, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 22526}

---

# Round 2

CONVERGED: YES

## Verdict
Round 1 produced a complete, executable plan. The P18 slice is well-bounded against the existing seams (`Runner`, `TaskAdapter`, `FailureCategory`, `VerifierOutcome`), preserves the audit schema (`1.2`), keeps `local-demo` byte-stable, and gates trust explicitly via a CLI-supplied module path rather than corpus-supplied callables. All blocking risks identified in Round 1 (trust source, verifier contract, closed failure-category mapping, workdir lifecycle, trace determinism, aggregate validation, fixture parity) have explicit resolutions. Remaining open questions are explicitly labeled non-blocking.

## Critique
- **Against subprocess JSON output (P2 comparison):** the in-process adapter returns a structured `VerifierResult` rather than parsing stdout/exit codes, so it is strictly richer than the subprocess path; the closed `FailureCategory` mapping plus fail-closed-on-unknown-category rule prevents silent audit drift. No conflict.
- **Against live Harbor (P6/P10/P11):** out of scope. Harbor carries its own `1.3`/`1.4` provenance and reproduction-claim invariants; the P18 adapter emits `1.2` with `model_id="in-process-python-verifier"` and no benchmark provenance, so there is no collision and no invariant interaction.
- **Against KMS/HSM (P17 follow-on):** orthogonal. Corpus trust gates (`--require-corpus-signature`/`--require-corpus-keyring`) compose unchanged with the new `--trust-verifier-module` flag because they operate on the corpus, not the verifier module.
- **Against migration shims:** out of scope and explicitly excluded; no schema change is introduced, so no migration surface is created.
- **Determinism:** `RunRecord.trace` keeps the in-memory workdir path exactly like `LocalSubprocessRunner`; no wallclock or absolute paths leak into on-disk audit. The shipped fixture module provides a canonical hash anchor.
- **Trust boundary:** the corpus carries only an opaque `verifier_selector` string; only the operator-supplied module path selects executable code. This is the correct fail-closed design.
- **Regression risk:** the new adapter ships under a separate `python-demo` subcommand and a sibling `TaskAdapter`/`Runner` pair; `local-demo`, `demo`, and Terminal-Bench dry-run paths are untouched.

## Required Changes
No additional changes required beyond the Round 1 revised plan. The plan is internally consistent and ready to execute.

## Revised Plan
(Execute the Round 1 revised plan as written.)

1. `src/self_harness/adapters/in_process_python.py`: `InProcessVerifier`/`InProcessSetupHook` Protocols, `VerifierResult` TypedDict, `load_trusted_module`, `InProcessPythonTaskAdapter`, `InProcessPythonRunner` with per-attempt `mkdtemp`, optional `workspace_template` copy, optional setup hook, closed `FailureCategory` mapping, fail-closed on unknown category, `keep_workdir` mirror of `LocalSubprocessRunner`.
2. `src/self_harness/cli.py`: `python-demo CORPUS --trust-verifier-module PATH` (hard-required) with existing corpus trust gates, `model_id="in-process-python-verifier"`, schema `1.2`, explicit "not a benchmark reproduction" output.
3. Tests: `tests/test_in_process_python.py` (pass/fail/unknown-category/selector-passthrough/setup-hook/fresh-workdir-per-attempt/verifier-exception/setup-exception/engine-loop byte-stability); `tests/fixtures/in_process_verifier.py` fixture; readiness canonical-hash extension for one `python-demo` invocation.
4. Docs: README section "In-process trusted verifier (P18)"; remove "richer non-subprocess verifier adapters" from `productionization_brief.md` remaining work.
5. Stop conditions: adapter shipped; CLI gates trust via explicit module path; `FailureCategory` mapping closed and validated; existing canonical hashes unchanged; no audit schema change.
6. Out of scope: network/container verifiers, KMS/HSM, benchmark reproduction claims, migration shims.

## Remaining Open Questions
1. (Non-blocking) Sync vs async verifier Protocol — keep synchronous for P18 for determinism parity with `LocalSubprocessRunner`.
2. (Non-blocking) `verifier_selector` shape — enforce `str`, non-empty, ≤256 chars, fully opaque.
3. (Non-blocking) Readiness canonical-hash coverage — recommend adding one `python-demo` hash in the same slice.
4. (Future) `self_harness.contrib.verifiers` registry — out of scope for P18.

[usage] {"completion_tokens": 1128, "completion_tokens_details": {"reasoning_tokens": 148}, "prompt_tokens": 24859, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 25987}
