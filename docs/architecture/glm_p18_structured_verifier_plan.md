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
