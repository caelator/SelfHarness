CONVERGED: YES

## Verdict

P103 — Failure-pattern task-id disjointness within a proposer-context round is the next precise, locally implementable paper-fidelity slice after P102. The paper Section 3.2 defines deterministic exact-match clustering: `C_ϕ = {r_i ∈ F_t | ϕ(r_i) = ϕ}`. Since each failed task `r_i` has exactly one failure signature `ϕ(r_i)`, it can belong to exactly one cluster. P101 made distinct failure signatures machine-checkable, but no invariant enforces that patterns within a round have pairwise-disjoint `task_ids`. Without this check, a reproduction bundle could carry two patterns with distinct `(failure_category, causal_status_sha256, mechanism_sha256)` signatures that both claim the same task — a structural impossibility under exact-match clustering.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_held_in_failure_patterns_block` validates signature distinctness (P101) and support-rank ordering (P102) but never checks `task_ids` overlap across patterns.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposer_context_evidence_binding` computes `failure_union` as a set union and compares it to `baseline_failing`, but never verifies that the union size equals the sum of individual `task_ids` lengths (the algebraic test for disjointness).
- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary` extracts per-pattern metadata for plan-vs-realized diffing but does not compute or compare task-id overlap counts.
- The class-shaped fixtures in `tests/test_reproduction_readiness.py::_proposer_context_rounds` always use exactly one pattern per round, so the invariant is satisfied vacuously; no hash rotation will occur.

Inference (architecture decisions, labeled as inference):
- **Enforcement layer:** Both `_held_in_failure_patterns_block` (shape validation) and `_cross_artifact_proposer_context_evidence_binding` (cross-artifact binding) need the check, because a shape-valid artifact with intra-round overlap would still pass the cross-artifact binding unless the union-size test is explicit.
- **Error surface:** Report the first overlapping task id and the two clusters that share it, with enough metadata to triage without dumping full traces.
- **Hash rotation:** None. All existing fixtures use single-pattern rounds. No canonical audit hash, reproduction-readiness fixture hash, or release-candidate evidence hash rotates.

## Required Changes

None blocking. Decisions resolved:
1. In `_held_in_failure_patterns_block`, after signature distinctness and support-rank ordering checks, accumulate all `task_ids` across patterns and verify the total count equals the set-union count. Report the first duplicate task id with the two conflicting cluster ids.
2. In `_cross_artifact_proposer_context_evidence_binding`, record `failure_pattern_task_overlap_violations` metadata and fail the check when any task id appears in more than one same-round pattern.
3. In `capture_manifest_diff.py::_proposer_context_failure_category_summary`, include per-round task-id overlap counts so planned-vs-realized rehearsals catch drift before bundle verification.

## Revised Plan

**P103 — Failure-pattern task-id disjointness within a proposer-context round (paper Section 3.2)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):

- `src/self_harness/_artifact_shapes.py::_held_in_failure_patterns_block`:
  - After the P102 support-rank ordering check, build a `Counter` over all pattern `task_ids`.
  - If any task id has count > 1, return a descriptive error: `f"{label}.patterns task-id overlap violation: task {task_id} appears in clusters {cluster_a} and {cluster_b}"` (first violation only).
  - Comment references Section 3.2 exact-match clustering contract: one task → one signature → one cluster.

- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposer_context_evidence_binding`:
  - For each round, after computing `failure_union`, compute `total_task_refs = sum(len(task_ids) for pattern in failure_patterns)`.
  - If `total_task_refs != len(failure_union)`, record `failure_pattern_task_overlap_violations` with the overlapping task ids and their cluster memberships.
  - Add the violation list to `metadata` and append a failure message to `failures`.

- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary`:
  - Add `failure_pattern_task_overlap_count` to each round summary by computing `sum(len(task_ids)) - len(set(union))`.
  - The existing `_proposer_context_pattern_hash_drifts`-style comparison will then surface overlap drift between planned and realized context.

Tests:

- `tests/test_reproduction_readiness.py`:
  - Add `test_reproduction_bundle_rejects_failure_pattern_task_overlap`: load the class-shaped `proposer_context_manifest` artifact, append a second pattern in `rounds[0]` that shares one task id with the existing pattern but has a distinct mechanism hash and presentation order adjusted to remain a valid permutation, and assert `verify_reproduction_bundle` fails at `artifact_proposer_context_manifest` with "task-id overlap violation" in the detail.
  - Add `test_reproduction_bundle_rejects_failure_pattern_task_overlap_cross_artifact`: construct a shape-valid context where patterns have distinct signatures but overlapping task ids (requires relaxing the shape validator only for this test via a local fixture that bypasses `_held_in_failure_patterns_block`), then assert the cross-artifact check records `failure_pattern_task_overlap_violations`.
  - Add `test_reproduction_bundle_accepts_disjoint_failure_patterns`: extend the fixture with a second pattern using a disjoint task-id subset and distinct mechanism hash, confirm bundle verification still passes.

- `tests/test_capture_manifest.py`:
  - Add a test that modifies the planned capture manifest's proposer-context artifact to include overlapping task ids across two patterns and asserts the diff report records `failure_pattern_task_overlap_count` drift.

Docs:

- `docs/architecture/schema_changelog.md`: add P103 entry under proposer-context manifest notes, stating this is a behavioral tightening of `_held_in_failure_patterns_block` and `_cross_artifact_proposer_context_evidence_binding`, not a schema bump; reference Section 3.2 exact-match clustering.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposer_context_ingredients.notes` to mention that failure-pattern `task_ids` must be pairwise disjoint within a round.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposer_context_manifest` row description to note the task-id disjointness invariant.
- `docs/architecture/productionization_brief.md`: append P103 entry in established style.

Non-goals:
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No enforcement of inter-round task-id relationships (a task may move between clusters across rounds as the harness changes).
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged.
- Schema changelog entry present and references Section 3.2 exact-match clustering.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether inter-round task-id stability should be tracked (a task in cluster A at round t may move to cluster B at round t+1 as the harness changes) — defer; the paper does not require inter-round cluster stability.
2. Whether the same disjointness invariant should apply to `passing_behavior_summaries` — defer; the paper does not constrain passing-behavior summaries to disjoint task sets, and the current single-summary fixtures make this low-value.
