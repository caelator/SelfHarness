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
