# Round 1

CONVERGED: NO

## Verdict
Initial architecture plan drafted. Material design decisions are resolved at the abstract level and validated against repo evidence, but one essential shape-change decision (whether `live_harbor_audit` must carry two-attempt records at the per-class level) needs confirmation, and fixture migration scope must be enumerated precisely before execution. Round 2 critique should pressure-test the shape-change rationale and the single-seam invariant placement.

## Critique
- Evidence: `_live_harbor_audit` in `_artifact_shapes.py` currently only checks trial_artifacts non-empty, `captured=true`, non-empty `task_id`, and non-empty `verifier_outcome`. It does not constrain task count, task-id coverage, or attempts.
- Evidence: `extract_live_harbor_audit` in `capture_extract.py` already groups trials by task and emits an `attempts` list (attempt_index, pass, terminal_cause) per task, but the count is data-driven and not enforced.
- Evidence: `_cross_artifact_invariants` in `reproduction_bundle.py` currently only reconciles `live_terminal_bench_split_manifest` with `live_two_repeat_evaluation_report`. It does not consult `live_harbor_audit`, so the audit can drift from the fixed 64-task split without failing bundle verification.
- Evidence: `_class_shaped_payloads` in `tests/test_reproduction_readiness.py` ships a `live_harbor_audit` fixture with a single trial artifact (`tb-held-in-00`), which would fail any new coverage invariant. Fixtures must be migrated before tests pass.
- Evidence: `_planned_artifact_stub` in `capture_manifest_build.py` ships a one-row planned `live_harbor_audit` stub; strict shape validation would fail under the new shape.
- Inference: The Self-Harness paper (Section 4.1 Metrics, Algorithm 1, Appendix A.1) requires two repeated attempts per task under a fixed candidate harness, and Section 3.4 states verifier outcomes are recorded over the final container state per trial. Therefore the live Harbor audit must demonstrate both coverage of the fixed 64-task split and two attempts per task to substantiate the live execution requirement listed in `benchmark_reproduction_requirements.json` (`live_artifact_ingest`).
- Risk: Extending `_live_harbor_audit` to require an `attempts` field is a per-class artifact-shape change. The task says to preserve per-class validators unless essential; I conclude it is essential because without it, the bundle verification seam cannot distinguish "audit covered split with two attempts" from "audit covered one task once."
- Risk: If the cross-artifact invariant lives only in `reproduction_bundle.py`, capture admission (`capture_admit.py`) inherits it through the single shared seam, satisfying the "single shared bundle verification seam" constraint. This is the correct placement; do not duplicate logic in capture_extract.

## Required Changes
1. **Per-class shape change (essential).** In `_artifact_shapes.py::_live_harbor_audit`:
   - Require `trial_artifacts[*].attempts` to be a list of exactly 2 objects.
   - Each attempt object must contain boolean `pass` and integer `attempt_index` (0 or 1, distinct within a task).
   - Reject duplicate `task_id` entries within a single audit document.
   - Keep `verifier_outcome` semantics but additionally require: if both attempts have `pass=true`, outcome must be `pass`; otherwise `fail`.
   - Do not hard-code 64 at shape level (split knowledge belongs to cross-artifact invariant).
2. **Cross-artifact invariant extension.** In `reproduction_bundle.py::_cross_artifact_invariants`:
   - Add a `cross_artifact_audit_split_coverage` check that requires `live_harbor_audit` to be present whenever both `live_terminal_bench_split_manifest` and `live_two_repeat_evaluation_report` are present (it is a required class per requirements JSON).
   - Assert `live_harbor_audit.trial_artifacts[*].task_id` set equals the union of split manifest `held_in_task_ids` ∪ `held_out_task_ids` (set equality; report `missing` and `extra`).
   - Assert every `trial_artifacts[*].attempts` has length 2.
   - Assert `live_harbor_audit` task-id set equals `live_two_repeat_evaluation_report.per_task_attempts[*].task_id` set.
3. **Extractor alignment.** In `capture_extract.py::extract_live_harbor_audit`:
   - Reject input trial sets where any task has fewer or more than 2 captured trials. (Offline validation of operator-supplied capture; does not contact Harbor.)
   - Continue to emit the existing `attempts` payload so the new shape validator passes.
4. **Planned-stub migration.** In `capture_manifest_build.py::_planned_artifact_stub`:
   - Replace the single-row `live_harbor_audit` stub with one entry per planned split task id (32 held-in + 32 held-out), each with two attempt rows `{attempt_index: 0, pass: true}` and `{attempt_index: 1, pass: true}`.
5. **Test fixture migration.** In `tests/test_reproduction_readiness.py::_class_shaped_payloads`:
   - Replace `live_harbor_audit` payload with full 64-task coverage and two attempts per task.
6. **New invariant tests.** Add tests in `tests/test_reproduction_readiness.py` and/or a dedicated `test_reproduction_bundle_audit_coverage.py`:
   - Audit missing one split task id → bundle fails with `cross_artifact_audit_split_coverage` and metadata `missing=[...]`.
   - Audit with extra foreign task id → fails with `extra=[...]`.
   - Audit row with one or three attempts → fails at shape level and bundle level.
   - Audit task-id set disjoint from two-repeat report task-id set → fails.
7. **Capture-admit regression coverage.** Extend `tests/test_capture_admit.py` with a case where `live_harbor_audit` input lacks one task; expect admission to fail at the bundle verification seam (not at the extractor seam alone), proving the single shared seam inherits the new invariant.

## Revised Plan
**Invariants (exact):**
- INV-AUDIT-1 (per-class): For each `trial_artifacts[*]`, `attempts` is a list of exactly 2 objects; each object has boolean `pass` and integer `attempt_index ∈ {0,1}`; the two `attempt_index` values within a task are distinct.
- INV-AUDIT-2 (per-class): `verifier_outcome == "pass"` iff both attempts have `pass == true`.
- INV-AUDIT-3 (per-class): `task_id` values are unique within the audit document.
- INV-XAUDIT-1 (cross-artifact): `set(live_harbor_audit.task_id) == set(split.held_in ∪ split.held_out)`.
- INV-XAUDIT-2 (cross-artifact): `set(live_harbor_audit.task_id) == set(two_repeat.per_task_attempts.task_id)`.
- INV-XAUDIT-3 (cross-artifact): all `trial_artifacts[*].attempts` have length 2 (defensive; also enforced per-class).

**Files to touch:**
- `src/self_harness/_artifact_shapes.py` — extend `_live_harbor_audit`.
- `src/self_harness/reproduction_bundle.py` — extend `_cross_artifact_invariants`.
- `src/self_harness/capture_extract.py` — enforce 2-trials-per-task at extraction time.
- `src/self_harness/capture_manifest_build.py` — migrate planned stub.
- `tests/test_reproduction_readiness.py` — migrate `_class_shaped_payloads` for `live_harbor_audit`; add invariant failure tests.
- `tests/test_capture_admit.py` — add audit-coverage drift regression through the shared seam.

**Fixture migration details:**
- `live_harbor_audit` fixture becomes:
  ```
  {
    "schema_version": "1.0", "ok": true, "mode": "live",
    "trial_artifacts": [
      {"task_id": <id>, "captured": true,
       "verifier_outcome": "pass",
       "attempts": [{"attempt_index": 0, "pass": true, "terminal_cause": null},
                    {"attempt_index": 1, "pass": true, "terminal_cause": null}]}
      for <id> in (held_in_00..31 + held_out_00..31)
    ],
    "reproduction_claimed": false
  }
  ```
- The planned stub mirrors the same shape using `planned-held-in-NN` / `planned-held-out-NN` ids (already used elsewhere in `_planned_artifact_stub`).

**Stop conditions:**
- All existing reproduction-readiness, bundle, capture-extract, and capture-admit tests pass.
- New invariant tests for audit-coverage drift fail without the change and pass with it.
- No readiness matrix dependencies added or removed.
- `reproduction_claimed` remains false everywhere.
- No new network/Harbor/Docker/PyPI/Sigstore/cloud contact points; the changes are pure offline validation, fixture, and stub data.
- `cross_artifact_split_evaluation_coverage` (existing) and `cross_artifact_audit_split_coverage` (new) both run inside `_cross_artifact_invariants`, preserving the single shared bundle verification seam.

**Out of scope (explicit):**
- Adding `attempts_per_task` or `task_count` aggregate fields to `live_harbor_audit` (not required by paper; per-task rows already encode it).
- Changing readiness requirements JSON.
- Live Harbor trial capture itself (offline-only constraint).

## Remaining Open Questions
1. **Confirm the essential-shape-change rationale.** Is requiring `attempts` (exactly 2) on every `live_harbor_audit.trial_artifacts[*]` acceptable, given the task's "preserve per-class validators unless essential" clause? My judgment: yes, because otherwise the audit cannot evidence the two-repeat live execution the paper mandates. Needs round-2 confirmation.
2. **Verifier-outcome tie-break semantics.** When one attempt passes and one fails, `verifier_outcome == "fail"` is consistent with the existing extractor. Confirm we should also enforce this at shape level (INV-AUDIT-2) rather than only at extraction. Default in revised plan: enforce at both.
3. **Empty `terminal_cause`.** The extractor currently emits `terminal_cause` from `record.terminal_cause`. Should shape validator constrain it (e.g., must be a string when present, or must be null/absent when both attempts pass)? Default in revised plan: leave unconstrained to avoid over-coupling to Harbor's failure-reason taxonomy; flag for round-2 review.

[usage] {"completion_tokens": 3948, "completion_tokens_details": {"reasoning_tokens": 1634}, "prompt_tokens": 46503, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 50451}

---

# Round 2

CONVERGED: YES

## Verdict
Round 2 review confirms the candidate plan is execution-ready. All material design decisions are resolved, validated against repo evidence, and consistent with the Self-Harness paper's Terminal-Bench-2.0 requirements. The three remaining open questions have defensible non-blocking defaults that align with the task constraints.

## Critique
- **Evidence validated:** `_live_harbor_audit` in `_artifact_shapes.py` checks only `task_id`, `captured`, and `verifier_outcome` — no task count, coverage, or attempt constraints. Confirms the shape-change gap.
- **Evidence validated:** `extract_live_harbor_audit` in `capture_extract.py` already emits an `attempts` list per task with `{attempt_index, pass, terminal_cause}`. The shape validator lags the extractor output.
- **Evidence validated:** `_cross_artifact_invariants` in `reproduction_bundle.py` reconciles only split manifest with two-repeat report. Harbor audit drift would go undetected.
- **Evidence validated:** `benchmark_reproduction_requirements.json` lists `live_harbor_audit` as a required class under `live_artifact_ingest`, with note "Artifact ingest exists locally, but this row requires artifacts from a live run."
- **Evidence validated:** `_class_shaped_payloads` ships a one-task `live_harbor_audit` fixture; `_planned_artifact_stub` ships a one-row stub. Both would fail any new coverage invariant.
- **Inference:** The paper Section 4.1 explicitly states Pass (%) is "computed over two repeated attempts for each harness candidate unless otherwise specified." Requiring the audit to carry two attempts per task is therefore paper-grounded, not over-engineering.
- **Inference:** The essential-shape-change rationale is sound — the task clause permits changes "when GLM concludes artifact-shape changes are essential," and without per-class attempt enforcement the bundle seam cannot distinguish one-task-once from 64-task-twice.
- **Inference:** Single-seam placement in `reproduction_bundle._cross_artifact_invariants` is correct; capture admission (`capture_admit.py`) already invokes bundle verification, so the new invariant is inherited without duplication.

## Required Changes
No additional changes beyond the round-1 plan. The seven required changes stand as written:
1. Per-class shape change in `_artifact_shapes.py::_live_harbor_audit` (require `attempts` list of exactly 2, distinct `attempt_index ∈ {0,1}`, unique `task_id`, verifier-outcome consistency).
2. Cross-artifact invariant extension in `reproduction_bundle.py::_cross_artifact_invariants` (`cross_artifact_audit_split_coverage` with set-equality to split manifest and to two-repeat report).
3. Extractor alignment in `capture_extract.py::extract_live_harbor_audit` (reject tasks without exactly 2 captured trials; offline validation only).
4. Planned-stub migration in `capture_manifest_build.py::_planned_artifact_stub` (64 entries, 2 attempts each).
5. Test fixture migration in `tests/test_reproduction_readiness.py::_class_shaped_payloads`.
6. New invariant tests for audit-coverage drift.
7. Capture-admit regression coverage proving shared seam inheritance.

## Revised Plan
Unchanged from round 1. Invariants, file touches, fixture migration details, and stop conditions are finalized:

**Invariants (exact):**
- INV-AUDIT-1: `trial_artifacts[*].attempts` is a list of exactly 2 objects; each has boolean `pass` and integer `attempt_index ∈ {0,1}`; the two indices within a task are distinct.
- INV-AUDIT-2: `verifier_outcome == "pass"` iff both attempts have `pass == true`.
- INV-AUDIT-3: `task_id` values are unique within the audit document.
- INV-XAUDIT-1: `set(live_harbor_audit.task_id) == set(split.held_in ∪ split.held_out)`.
- INV-XAUDIT-2: `set(live_harbor_audit.task_id) == set(two_repeat.per_task_attempts.task_id)`.
- INV-XAUDIT-3: all `trial_artifacts[*].attempts` have length 2 (defensive).

**Stop conditions:** All existing tests pass; new invariant tests fail without the change and pass with it; no readiness-matrix dependency churn; `reproduction_claimed` stays false everywhere; no new network/cloud contact points; both cross-artifact checks run inside the single shared seam.

**Out of scope:** Aggregate count fields on audit; readiness JSON edits; live Harbor capture itself.

## Remaining Open Questions
All three are resolved with non-blocking defaults:
1. **Essential-shape-change rationale:** Confirmed acceptable. The paper's Section 4.1 two-repeat metric definition makes per-class attempt enforcement necessary for evidence fidelity.
2. **Verifier-outcome semantics:** Enforce at both shape and extraction layers (defense in depth; extractor already implements the rule).
3. **Empty `terminal_cause`:** Leave unconstrained to avoid coupling to Harbor's failure-reason taxonomy. May revisit if future paper revisions publish a closed vocabulary.

[usage] {"completion_tokens": 2265, "completion_tokens_details": {"reasoning_tokens": 1172}, "prompt_tokens": 51158, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 53423}
