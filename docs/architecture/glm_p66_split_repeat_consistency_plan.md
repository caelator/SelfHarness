CONVERGED: YES

## Verdict
Plan is ready to execute. Round 2 evidence resolves the blocking questions from Round 1: admission delegates to bundle verification (a bundle is built and verified in the admission path), exit-code conventions are consistent (2 = not ready), and the divergent-fixture scope is bounded. The cross-artifact invariant can land solely in `verify_reproduction_bundle` and admission inherits it. Remaining items are implementation details, not blocking decisions.

## Critique
Evidence (validated against repo):
- `src/self_harness/reproduction_bundle.py::_entry_checks` performs class-coverage, per-entry integrity (sha256/byte_size), and per-class shape validation via `artifact_shape_error`. It does **not** compare fields across artifacts. This is the correct and only seam for the new invariant.
- `src/self_harness/_artifact_shapes.py`:
  - `_live_terminal_bench_split_manifest` enforces `total_cases==64`, `held_in_count==len(held_in_task_ids)`, `held_out_count==len(held_out_task_ids)`, sums to 64, and disjointness.
  - `_live_two_repeat_evaluation_report` enforces `attempts_per_task==2`, exactly 2 attempts per task, internal reconciliation of `task_count`/`attempt_count`/`pass_count`/`fail_count`, and rejects duplicate `task_id`s — but **never** compares its task ids against the split manifest.
- `tests/test_reproduction_readiness.py::_class_shaped_payloads` confirms the latent bug: the split manifest has 64 ids (`tb-held-in-{00..31}` ∪ `tb-held-out-{00..31}`), while the evaluation report has only 2 entries (`tb-held-in-00`, `tb-held-out-00`). These are independent sets; today's bundle verifier accepts this.
- `tests/test_capture_admit.py` proves admission delegates to bundle verification: it asserts `payload["bundle_verification"]["ok"] is True` and that `(tmp_path / "admission" / "bundle.json").exists()`. Therefore, adding the invariant inside `verify_reproduction_bundle` automatically covers admission; no second mirror is needed.
- Exit-code convention is uniform: bundle verify and admission both return 2 when `ok=false` (negative tests in both files assert `returncode == 2`).
- `src/self_harness/capture_manifest_build.py::_planned_artifact_stub` produces a similar divergence at the *planned-artifact* layer (planned manifest has 64 ids; planned eval has only 2). This is out of scope for the slice (those are operator planning stubs, not evidence), but should be flagged as follow-up to avoid fixture drift once the invariant is enforced on evidence.

Inference:
- Because admission builds and verifies a bundle, a single invariant function called from `verify_reproduction_bundle` after `_entry_checks` is sufficient; adding it again in admission would duplicate logic and risk drift.
- The invariant should be skipped (not failed) when either artifact class is absent, because `_entry_checks` already emits a `class_coverage` failure for missing required classes. The cross-artifact check should only run when both files are resolvable.

## Required Changes
1. `src/self_harness/reproduction_bundle.py`:
   - Add `_cross_artifact_invariants(bundle, requirements) -> list[ReproductionBundleCheck]`.
   - Resolve the `live_terminal_bench_split_manifest` and `live_two_repeat_evaluation_report` entries via `reproduction_bundle_artifact_index` (or by scanning `bundle.entries`).
   - If either is missing, return `[]` (class_coverage already covers this).
   - Otherwise load both JSON payloads and assert:
     - INV-A: `eval["task_count"] == 64`.
     - INV-B: `eval["attempt_count"] == 128`.
     - INV-C: `set(task["task_id"] for task in eval["per_task_attempts"]) == set(manifest["held_in_task_ids"]) | set(manifest["held_out_task_ids"])`.
   - Emit a single check named `cross_artifact_split_evaluation_coverage` with `status="pass"` or `"fail"`. On failure, include metadata: `{"missing": [...], "extra": [...], "eval_task_count": N, "manifest_total": 64}`.
   - Call from `verify_reproduction_bundle` immediately after `checks.extend(_entry_checks(bundle, requirements))`.
2. `tests/test_reproduction_readiness.py::_class_shaped_payloads`:
   - Migrate `live_two_repeat_evaluation_report.per_task_attempts` to use the **union** of the manifest's `held_in_task_ids` and `held_out_task_ids` (all 64 ids), each with 2 attempts. Recompute `task_count=64`, `attempt_count=128`, and reconcile `pass_count`/`fail_count` accordingly. This unblocks existing positive tests once the invariant lands.
3. New unit tests (in `test_reproduction_readiness.py` or a dedicated `test_reproduction_bundle_cross_artifact.py`):
   - Positive: bundle verifies with full 64-task alignment.
   - Negative A: evaluation `task_count==63` (subset).
   - Negative B: evaluation ids use a different prefix (disjoint).
   - Negative C: evaluation `task_count==64` but ids ≠ manifest union.
   - Negative D: evaluation `task_count==64` and ids match but `attempt_count != 128` (defense in depth, should already be caught by class validator).
4. `tests/test_capture_admit.py`:
   - Add one negative admission test: supply a raw/supplied evaluation report covering only a subset of the manifest union; assert `completed.returncode == 2`, `payload["ok"] is False`, and that a `cross_artifact_split_evaluation_coverage` failure appears in `bundle_verification.checks`. This proves admission inherits the invariant via bundle verification.
5. `docs/operations/benchmark_reproduction_requirements.json`:
   - Extend the `notes` field of `terminal_bench_fixed_split` and `two_repeated_attempts` to reference the new `cross_artifact_split_evaluation_coverage` check and cite paper Section 4.1 (Splits and protocol; Metrics). No schema change.
6. Out of scope but tracked as follow-up: align `capture_manifest_build.py::_planned_artifact_stub` planned-evaluation stub to the planned-manifest id union, so the planning layer does not exhibit the same divergence. File a follow-up issue; do **not** block this slice on it.
7. Preserve all task constraints: no `reproduction_claimed=true`, no external contact, no readiness dependency edits, no cross-artifact logic added to `_artifact_shapes.py`.

## Revised Plan
Slice: "Cross-artifact Terminal-Bench split ↔ two-repeat evaluation coverage invariant (single-seam implementation)."

Files to touch:
- `src/self_harness/reproduction_bundle.py` — add `_cross_artifact_invariants` + wire into `verify_reproduction_bundle`.
- `tests/test_reproduction_readiness.py` — migrate `_class_shaped_payloads["live_two_repeat_evaluation_report"]`; add 4 negative bundle tests.
- `tests/test_capture_admit.py` — add 1 negative admission test that exercises the inherited invariant.
- `docs/operations/benchmark_reproduction_requirements.json` — extend `notes` for the two affected requirements.

Exact invariants (final):
- INV-A: `eval["task_count"] == 64`.
- INV-B: `eval["attempt_count"] == 128`.
- INV-C: `set(t["task_id"] for t in eval["per_task_attempts"]) == set(manifest["held_in_task_ids"]) | set(manifest["held_out_task_ids"])`.

Single check name: `cross_artifact_split_evaluation_coverage`.

Stop conditions:
- New check present in `verify_reproduction_bundle`; positive + 4 negative bundle tests + 1 negative admission test green.
- Fixtures reconciled; docs updated; CI green.
- No external contact, no `reproduction_claimed=true`, no readiness dependency changes, `_artifact_shapes.py` per-class validators untouched for cross-artifact logic.

## Remaining Open Questions
1. ~~Admission delegation~~ — Resolved: admission builds and verifies a bundle (`tests/test_capture_admit.py` asserts `bundle_verification.ok` and `bundle.json` existence). Single-seam implementation confirmed.
2. ~~Exit-code convention~~ — Resolved: 2 = not ready, consistent across bundle verify and admission.
3. `live_harbor_audit.trial_artifacts[*].task_id` binding to the manifest union — Explicitly out of scope per task statement; tracked as future work.
4. `capture_manifest_build.py::_planned_artifact_stub` divergence — Out of scope for this slice (planning stubs, not evidence); tracked as follow-up to avoid fixture drift.
5. Repo-wide fixture grep for divergent ids — Implementation-time housekeeping; the invariant's negative tests will surface any other latent drift. Non-blocking.

CONVERGED: YES
