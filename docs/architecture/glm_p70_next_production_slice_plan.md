CONVERGED: YES

## Verdict
A single P70 slice is clear and fits the established P65–P69 pattern: add a cross-artifact invariant ensuring the `live_two_repeat_evaluation_report` and `live_harbor_audit` artifacts agree on per-task and per-attempt verifier outcomes. This closes a paper-Section-4.1 fidelity gap where an operator could currently supply contradictory evidence: the two artifacts are each bound to the same split and protocol, and each individually computes verifier outcomes, but no bundle check requires them to agree on outcomes. The work is offline-testable, code-level, narrows a real invariant, and does not claim reproduction.

## Critique
Evidence (from `_artifact_shapes.py`):
- `_live_two_repeat_evaluation_report` validates `per_task_attempts[].attempts[].pass` booleans, two attempts per task, and derives no aggregate beyond `pass_count`/`fail_count`.
- `_live_harbor_audit` validates `trial_artifacts[].attempts[].pass` and enforces `verifier_outcome == "pass" iff all(pass_values)`.

Evidence (from `reproduction_bundle.py::_cross_artifact_invariants`):
- `_cross_artifact_split_evaluation_coverage` binds evaluation task ids to the split.
- `_cross_artifact_audit_split_coverage` binds audit task ids to split and to evaluation ids, and checks each audit task has exactly two attempts.
- It does **not** compare per-task or per-attempt pass values between the two artifacts.

Inference / gap: a bundle could currently pass verification with `live_two_repeat_evaluation_report` showing task `T` as pass/pass while `live_harbor_audit` shows task `T` as fail/fail. This contradicts the paper's single fixed-protocol, two-attempt-per-task evidence contract where the audit's verifier outcome over the final container state *is* the evaluation outcome.

Risks addressed: no schema change, no new artifact class, no default release path change, no readiness hash rotation (bundle report `report_hash` rotates only for bundles that include both artifacts, which fixture reproduction bundles already do — so fixture hashes must rotate once). Non-blocking because rotation is deterministic and localized.

## Required Changes
None beyond the revised plan below. The slice reuses existing validator and report-hash primitives; no new seam or reviewer is needed.

## Revised Plan

**P70 — cross-artifact evaluation/audit verifier outcome agreement**

1. Files to modify
   - `src/self_harness/reproduction_bundle.py`: extend `_cross_artifact_invariants` with `_cross_artifact_evaluation_audit_outcomes`.
   - Tests: add/extend `tests/test_reproduction_bundle_cross_artifact.py` (or equivalent) with agreement, disagreement (per-task), disagreement (per-attempt index 0 vs 1 swap), missing-audit, missing-evaluation, and partial task coverage cases.
   - Fixtures: rotate the committed `tests/fixtures/release_candidate/reproduction_bundle*` and any `make reproduction-bundle-check` fixture output where both `live_two_repeat_evaluation_report` and `live_harbor_audit` are present, because the bundle verifier report hash will change.

2. Invariant specification
   - Trigger only when both `live_two_repeat_evaluation_report` and `live_harbor_audit` are present in the bundle. (Otherwise skip — preserves the skip-when-both-absent and fail-when-exactly-one-present behavior already used by `_cross_artifact_protocol_binding`.)
   - Task-id sets must match exactly (already enforced by P67's audit/evaluation id binding; reuse, do not recompute).
   - For each shared `task_id`:
     - evaluation attempt pass values sorted by attempt order `[a0, a1]` must equal audit attempt pass values keyed by `attempt_index` `{0, 1}`.
     - evaluation's derived per-task pass (all attempts pass) must equal audit's `verifier_outcome == "pass"`.
   - Failure detail must include: mismatched task ids, mismatched per-attempt values with attempt index, and the expected-vs-actual verifier outcome strings.

3. Tests and fixtures
   - Offline only; no Harbor/Docker/model/PyPI/Sigstore/network contact.
   - One passing fixture rotation covering the current happy path so committed bundle report hashes update deterministically.
   - Three negative fixtures: per-task outcome mismatch, per-attempt pass swap (same aggregate, different attempt), and audit missing one task present in evaluation (the last is already caught by P67; assert the new check does not shadow it).
   - One positive fixture: artifacts absent → invariant skipped, report hash unchanged vs pre-P70 for that combination.

4. Stop conditions
   - `make check`, `make reproduction-bundle-check`, `make capture-admit-check`, and `make readiness` pass locally with no live contact.
   - `make release-candidate-evidence` still produces a deterministic non-reproduction decision; only the fixture bundle report hash rotates.
   - No new artifact class, no schema bump, no readiness catalog change, no audit/corpus schema change, no canonical paper-fidelity audit hash rotation.

## Remaining Open Questions
None blocking. One non-blocking confirmation Codex should verify before editing: whether `tests/fixtures/release_candidate/` currently contains a reproduction bundle with both `live_two_repeat_evaluation_report` and `live_harbor_audit`; if it does, the bundle report fixture hash must rotate as part of this slice. If it does not, no fixture rotation is needed and the slice is purely additive. Either way the plan executes the same way.
