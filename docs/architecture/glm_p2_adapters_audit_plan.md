CONVERGED: YES

## Verdict
The P2 slice is the right next move and is actionable now. The brief already proposes correct module boundaries and a faithful, non-overclaiming scope. The one under-specified fidelity point — how a subprocess verifier exit code maps onto the existing `VerifierOutcome` (which requires `terminal_cause`, `causal_status`, `mechanism`) — can be resolved by design without further evidence, so this round can converge.

## Critique
Strengths of the proposed slice (evidence from brief + code):
- Adds the two missing production seams identified in `paper_faithful_build_plan.md`: real runner seam + audit readback.
- Preserves the paper controls: fixed harness object, fixed task split, fresh environment per attempt, verifier-grounded pass/fail.
- Scope is honestly bounded: no Docker/Terminal-Bench, no LLM proposer, no demo behavior change.
- `Task` is currently a frozen dataclass with no defaulted fields (`src/self_harness/types.py`), so adding `metadata: dict[str, Any] = field(default_factory=dict)` is constructor-compatible.
- Engine writes `evaluation_rows` without trace messages, so subprocess stdout/stderr snippets in `RunRecord.trace` will not break audit determinism (they stay in-memory only). Good.

Risks / gaps requiring decisions:
1. **VerifierOutcome derivation.** `Runner.run` must return a `RunRecord` with a full `VerifierOutcome`. A subprocess runner only observes exit codes and captured output. The plan must define an explicit, stable convention so failure mining keeps working (it groups by `terminal_cause|causal_status|mechanism`).
2. **Workdir lifecycle.** "Fresh temp workdir per attempt" must define cleanup policy, timeout behavior, and whether the workdir path is persisted anywhere outside `RunRecord.trace` (it should not be, to keep audit deterministic).
3. **Harness argument semantics for `LocalSubprocessRunner`.** The `Runner` protocol passes `harness: HarnessSpec`, but local tasks are metadata-driven. The runner should accept but not require harness text surfaces; only `runtime_policy` (e.g. timeouts) and metadata together configure the run.
4. **Audit API strictness.** `load_audit_run` / `summarize_audit_run` must reject unknown `schema_version` values and missing round directories with package exceptions, not bare `KeyError`/`FileNotFoundError`.
5. **CLI scope.** `audit-summary` is small and useful for demos/CI; ship now. `local-demo` is also small if `load_tasks_json` exists; ship it to make the adapter testable end-to-end from the CLI, but keep it out of any "paper-faithful" claim.

No blocking user decisions or experiments are required.

## Required Changes
1. Pin the `VerifierOutcome` convention for the local subprocess adapter in the plan (see Revised Plan §3).
2. Pin audit API failure modes: strict schema-version allowlist (`{"1.0","1.1"}` initially), package-typed exceptions for missing/corrupt artifacts.
3. Pin CLI scope: ship `audit-summary PATH` and `local-demo TASKS_JSON --out PATH` now.
4. Pin metadata key names exactly: `solve_command`, `verify_command`, `workspace_template` (optional, path), `timeout_seconds` (optional, int), `env` (optional, dict[str,str]) for parity with future Docker runner.
5. State explicitly that subprocess traces (stdout/stderr/workdir) live only on in-memory `RunRecord.trace` and never in on-disk audit JSONL, preserving deterministic artifacts.
6. Add test that audit determinism is unchanged when the local subprocess runner is exercised through the engine (compare against a baseline toy run of equivalent shape).

## Revised Plan
**P2 slice (actionable now):**

1. `Task.metadata: dict[str, Any] = field(default_factory=dict)` in `types.py`. Backward compatible.

2. New module `self_harness/audit.py`:
   - `AuditRun` frozen dataclass: `manifest`, `lineage`, `rounds: list[AuditRound]`, `path`.
   - `AuditRound` frozen dataclass: `index`, `proposals`, `evaluations`, `harness_before`, `harness_after`.
   - `AuditSummary` frozen dataclass: `schema_version`, `protocol_version`, `rounds`, `final_held_in_score`, `final_held_out_score`, `accepted_count`, `rejected_count`, `invalid_count`.
   - `load_audit_run(path: Path) -> AuditRun` with strict schema-version check against allowlist `{"1.0","1.1"}`; raise `SelfHarnessError` (or new `AuditCorruptError`) on missing manifest, missing round dirs, or unparseable JSON.
   - `summarize_audit_run(path: Path) -> AuditSummary` built on `load_audit_run`.
   - No mutation of existing engine writes.

3. New module `self_harness/adapters/local_subprocess.py`:
   - `load_tasks_json(path: Path) -> list[Task]` reading `{"tasks":[...]}` with required `id, split, failure_mode, description` and optional `metadata`.
   - `LocalSubprocessRunner` implementing `Runner`:
     - Per-attempt fresh temp workdir (`mkdtemp`), optional copy from `task.metadata["workspace_template"]`.
     - Runs `solve_command` then `verify_command` via `subprocess.run` with optional `timeout_seconds` and optional `env` overlay.
     - Verifier exit code → `RunRecord.passed` and `VerifierOutcome`:
       - exit 0 → `passed=True`, `terminal_cause="verifier-pass"`, `causal_status="confirmed"`, `mechanism="subprocess-exit-zero"`, `message="verifier exited 0"`.
       - exit != 0 → `passed=False`, `terminal_cause="verifier-fail"`, `causal_status="rejected"`, `mechanism="nonzero-exit"`, `message=f"verifier exited {code}"`.
       - timeout → `passed=False`, `terminal_cause="timeout"`, `causal_status="environment"`, `mechanism="solve-or-verify-timeout"`.
     - `RunRecord.trace` records: solve command + exit, verify command + exit, stdout/stderr snippets (truncated, e.g. 4 KiB each), workdir path. These never enter on-disk audit JSONL.
     - Workdir removed in `finally` unless `keep_workdir` flag is set (default off).
   - Honors but does not require `harness.runtime_policy` keys: `solve_timeout_override`, `verify_timeout_override`.

4. CLI (`cli.py`):
   - `self-harness audit-summary PATH` → prints `AuditSummary` as stable JSON.
   - `self-harness local-demo TASKS_JSON --out PATH [--rounds N]` wires `load_tasks_json` + `LocalSubprocessRunner` + `HeuristicProposer` + `SelfHarnessEngine`. Output clearly labeled "local subprocess demo, not a Terminal-Bench reproduction".

5. Tests (`tests/`):
   - `test_audit.py`: load + summarize the existing demo output; assert manifest fields, lineage length, round count. Corrupt manifest, unknown schema_version, missing round dir each raise package exception.
   - `test_local_subprocess.py`: `load_tasks_json` happy + error paths. Runner passes a trivial verifier; fails on nonzero exit; uses fresh workdir per attempt (assert two attempts get different paths); honors `timeout_seconds`.
   - `test_engine_determinism_with_subprocess.py`: run engine twice on identical tasks file, assert byte-stable audit JSONL (traces are in-memory only).
   - Existing demo artifact byte-stability test must remain green.

6. Docs:
   - Update README: P2 ships audit readback + local subprocess adapter; explicitly state this is *not* a Terminal-Bench reproduction.
   - No change to `paper_faithful_build_plan.md` stop condition.

## Remaining Open Questions
None blocking. Items to track for P3 (out of scope here):
- Whether to introduce a richer verifier protocol (structured JSON verifier output) so `terminal_cause`/`mechanism` can be classifier-grounded rather than exit-code-derived.
- Whether `workspace_template` should support archive formats (tar/zip) vs. plain directory copy — current plan assumes directory copy.
- Whether to expose a `keep_workdir_on_failure` CLI flag for debugging; default remains off.
