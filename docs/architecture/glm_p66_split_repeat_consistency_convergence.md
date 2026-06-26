# Round 1

CONVERGED: NO

## Verdict
Initial architecture plan drafted. The core cross-artifact invariant is well-defined and implementable within existing offline bundle/admission verification surfaces. However, one piece of repository evidence is required before execution can begin: the actual `capture_admit.py` / `scripts/capture_admit.py` and `scripts/reproduction_bundle_verify.py` implementations, to confirm where admission-side cross-artifact checks should land and whether the bundle verifier exposes a hook for cross-artifact invariants. Without that, the plan risks duplicating or bypassing existing admission composition logic.

## Critique
Evidence (from repo):
- `src/self_harness/_artifact_shapes.py` validates each artifact class independently. `_live_terminal_bench_split_manifest` enforces 64 total cases, disjoint held-in/held-out, and count consistency. `_live_two_repeat_evaluation_report` enforces `attempts_per_task==2`, internally reconciles `task_count`, `attempt_count`, `pass_count`, `fail_count`, and rejects duplicate `task_id`s.
- `src/self_harness/reproduction_bundle.py::_entry_checks` performs class-coverage and per-entry integrity + per-class shape checks, but does NOT compare fields across artifacts.
- `tests/test_reproduction_readiness.py::_class_shaped_payloads` shows the two artifacts use independently-generated placeholder IDs (`tb-held-in-*` vs `tb-held-out-*` in the report vs manifest), which is itself a latent example of the divergence the task targets.
- `benchmark_reproduction_requirements.json` treats these as distinct requirements (`terminal_bench_fixed_split`, `two_repeated_attempts`).

Inference:
- The gap is real: a `live_two_repeat_evaluation_report` whose `task_count < 64` and whose `per_task_attempts[*].task_id` set is a strict subset of (or entirely disjoint from) the split manifest's task id union will currently pass both class-shape validation and bundle integrity verification.
- The task constraints explicitly place cross-artifact invariants in bundle/admission verification, not in `_artifact_shapes.py`. This is consistent with the current architecture, where `_artifact_shapes.py` is per-class and `reproduction_bundle.py` is the natural home for cross-artifact invariants.

## Required Changes
1. Add a cross-artifact invariant function in `reproduction_bundle.py` (e.g. `_cross_artifact_invariants`) that, given resolved artifact paths, loads `live_terminal_bench_split_manifest.json` and `live_two_repeat_evaluation_report.json` and asserts:
   - Evaluation `task_count == 64`.
   - The set of `per_task_attempts[*].task_id` equals the union of `held_in_task_ids` and `held_out_task_ids` from the split manifest (set equality, not subset).
   - Each task has exactly 2 attempts (already class-enforced, but re-stated as a cross-artifact invariant for robustness).
   - Optional tighter check: evaluation `attempt_count == 128` (= 64 × 2).
2. Wire the invariant into `verify_reproduction_bundle` after per-entry checks succeed, emitting a `cross_artifact_split_evaluation_coverage` check with `pass`/`fail`. Include metadata listing missing, extra, and duplicate task ids on failure.
3. Mirror the same invariant in admission verification (`capture_admit`) so an operator cannot bypass the bundle by supplying artifacts directly to admission. Confirm exact admission composition in the unseen `scripts/capture_admit.py` / `src/self_harness/...` admission module before finalizing.
4. Migrate `tests/test_reproduction_readiness.py::_class_shaped_payloads` so the evaluation report's `task_id`s match the split manifest's union. Currently they diverge (`tb-held-in-00` vs `tb-held-out-00` only in the report; manifest has 32+32 ids). Without this, existing tests will start failing once the invariant lands — which is desirable, but the fixture migration must be part of the slice.
5. Add unit tests:
   - Bundle verifies when evaluation task set equals manifest union.
   - Bundle fails when evaluation covers a strict subset (e.g. 63 tasks).
   - Bundle fails when evaluation ids are disjoint from manifest (e.g. different prefix).
   - Bundle fails when evaluation `task_count==64` but ids don't match manifest union.
   - Admission reproduces the same coverage when supplied directly.
6. Update docs (`docs/operations/benchmark_reproduction_requirements.json` notes, and any verification doc) to record the cross-artifact invariant as an explicit check, with paper reference Section 4.1 "Splits and protocol" + Metrics.
7. Preserve all existing constraints: no `reproduction_claimed=true`, no external contact, no readiness dependency change, per-class validators in `_artifact_shapes.py` untouched for cross-artifact logic.

## Revised Plan
Slice: "Cross-artifact Terminal-Bench split ↔ two-repeat evaluation coverage invariant."

Files to touch:
- `src/self_harness/reproduction_bundle.py`: add `_cross_artifact_invariants(bundle, requirements)`; call from `verify_reproduction_bundle` after `_entry_checks`. New check name `cross_artifact_split_evaluation_coverage`.
- `scripts/reproduction_bundle_verify.py` (unseen — confirm): ensure report surfaces the new check; no behavioral change beyond propagation.
- Admission path (unseen — confirm): `src/self_harness/...admission...` and/or `scripts/capture_admit.py`: invoke the same invariant after extraction, before producing the ready report.
- `tests/test_reproduction_readiness.py`: migrate `_class_shaped_payloads` so `live_two_repeat_evaluation_report.per_task_attempts[*].task_id` equals the union of the manifest's `held_in_task_ids` and `held_out_task_ids`. Add the 4 negative tests listed above.
- `tests/test_capture_admit.py`: add a negative admission test where raw/supplied evaluation report covers only a subset of the split manifest.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `notes` for `terminal_bench_fixed_split` and `two_repeated_attempts` to reference the cross-artifact invariant; no schema change.
- Optional: short doc note in `docs/source/...` if a verification doc exists (confirm).

Exact invariants:
- INV-1: `set(eval.per_task_attempts[*].task_id) == set(manifest.held_in_task_ids) | set(manifest.held_out_task_ids)`.
- INV-2: `eval.task_count == 64`.
- INV-3: `eval.attempt_count == 128`.
- INV-4: every `task_id` in eval appears in manifest, and vice versa (set equality; same as INV-1, restated for error messages).
- INV-5: each eval task has exactly 2 attempts (already class-enforced; cross-artifact context adds nothing new, keep as class-level).

Stop conditions:
- New invariant check exists in `verify_reproduction_bundle` and admission, is unit-tested positive and negative, fixtures reconciled, docs updated, and CI green.
- No external contact, no `reproduction_claimed=true`, no readiness dependency edits, per-class validators untouched for cross-artifact logic.

## Remaining Open Questions
1. Exact admission module path and composition: is cross-artifact validation best added in `reproduction_bundle.verify_reproduction_bundle` only, with admission delegating to it, or does admission assemble artifacts in a way that bypasses bundle verification (e.g., `--skip-readiness` still builds a bundle)? Need to inspect `scripts/capture_admit.py` and the admission source.
2. Should the invariant also bind `live_harbor_audit.trial_artifacts[*].task_id` to the same manifest union, or is that out of scope for this slice? The task statement scopes the fix to evaluation report vs split manifest; treat harbor-audit binding as future work unless admission already joins them.
3. Does any existing fixture outside `tests/fixtures/release_candidate` encode divergent ids that would also need migration once the invariant is enforced repo-wide? Need a grep across `tests/fixtures` for `per_task_attempts` and `held_in_task_ids`.
4. Should the cross-artifact invariant failure produce exit code 2 (not ready) or 3 (input error)? Current bundle verifier uses report-level `ok=false`; confirm convention with admission script exit codes.

These are decidable by reading two unseen scripts and one fixture directory; once answered, the plan should converge in round 2.

[usage] {"completion_tokens": 2174, "completion_tokens_details": {"reasoning_tokens": 399}, "prompt_tokens": 43961, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 46135}

---

# Round 2

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

[usage] {"completion_tokens": 2557, "completion_tokens_details": {"reasoning_tokens": 657}, "prompt_tokens": 47537, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 50094}
