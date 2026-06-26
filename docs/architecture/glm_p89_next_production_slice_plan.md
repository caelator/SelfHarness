CONVERGED: YES

## Verdict

P89 (per-candidate task outcome disclosure) is ready to execute. The slice is well-scoped, additive without schema bump, closes a genuine paper-fidelity auditability gap (Section 3.4 "split-wise outcomes" recorded per candidate), and avoids overreach into per-task acceptance rules or raw trace binding. All material risks identified in round 1 are addressed in the revised plan.

## Critique

- **Evidence (repo):** `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS` in `src/self_harness/_artifact_shapes.py` lacks any per-task surface; `_split_outcomes` only records aggregate counts. The bundle check `_cross_artifact_proposal_validation_binding` compares aggregate counts only.
- **Evidence (repo):** `_audit_split_total_rows` in `tests/test_capture_extract.py` only emits `__split_total__` rows; per-task verifier rows are not authored anywhere in fixtures.
- **Inference (paper):** Section 3.4 says "split-wise outcomes" are recorded per candidate and each transition must be auditable; aggregate counts are a lossy compression that prevents verifying which tasks actually flipped.
- **Architecture risk (low):** Optional additive `task_outcomes` field with closed shape `{task_id, split, pass}` mirrors P87's `validation_failure_category` pattern. No schema bump, no new artifact class, no readiness hash rotation on the canonical audit path. Capture-rehearsal fixture hash and any downstream release-candidate evidence fixture hash will rotate because the fixture gains the new field; this is expected and contained.
- **Bundle check scope (correct):** Baseline-superset-of-proposer-failures is the right conservative check. Candidate-vs-baseline per-task comparison is deliberately deferred as a larger semantic change.

## Required Changes

All round-1 required changes are incorporated and remain valid:
1. Optional additive `task_outcomes` field, no schema bump — confirmed.
2. Bundle check scoped to baseline-vs-proposer-context superset only — confirmed.
3. `capture_extract` reads optional per-task verifier rows, omits field when absent — confirmed.
4. Toy audit fixture extended with per-task candidate rows — confirmed.
5. `capture_manifest_diff` compares presence counts only, not contents — confirmed.
6. Explicit non-goals documented (no semantic rejection parsing, no raw trace binding, no baseline artifact class) — confirmed.

## Revised Plan

Plan from round 1 is final. No further revisions needed. Key points:

- **Files:** `_artifact_shapes.py` (add field + validation), `capture_extract.py` (populate from optional rows), `reproduction_bundle.py` (new `baseline_task_outcome_violations` check), `capture_manifest_diff.py` (presence-count drift), 3 test files, 2 docs files.
- **Non-goals:** No schema bump, no per-task acceptance rule, no raw trace binding, no new artifact class, no live service contact, no reproduction claim change.
- **Verification gates:** `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.

## Remaining Open Questions

All three open questions from round 1 are confirmed non-blocking:
- `attempt_index` support — deferred, list field allows future addition without bump.
- Candidate-vs-baseline per-task check — deferred to follow-up, risks false positives.
- `capture_manifest_diff` content comparison — deferred to bundle verification where cross-artifact context exists.

No blocking decisions remain. The plan is executable.
