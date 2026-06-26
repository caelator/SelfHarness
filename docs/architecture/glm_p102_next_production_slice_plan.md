CONVERGED: YES

## Verdict

P102 — Support-rank ordering validation for failure patterns (paper Section 3.2: "Clusters are then ordered by their support and estimated actionability, so that the proposer is exposed first to recurring mechanisms that are more likely to map to a high-value harness modification") is the next precise, locally implementable paper-fidelity slice after P101. The artifact shape validator already ensures `presentation_order` is a contiguous permutation from zero (P98), but it does not enforce that the ordering respects support (cluster `size`) when sizes differ. The paper explicitly states that higher-support clusters are presented first; enforcing this as a partial-order invariant — larger `size` must yield lower `presentation_order`, with ties left unconstrained to permit actionability-driven ordering — closes a machine-checkable gap without over-constraining operator evidence.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_held_in_failure_patterns_block` validates that `presentation_order`, when declared by any pattern, forms a contiguous permutation from zero across all patterns in the block. It does not validate ordering semantics against `size`.
- The paper (p.6, Section 3.2) states clusters "are then ordered by their support and estimated actionability". `support_rank` is documented as "derived from `size` descending, then `cluster_id` ascending" in `_cross_artifact_proposer_context_evidence_binding` metadata, but this derivation is never enforced as an ordering invariant on `presentation_order`.
- `src/self_harness/capture_manifest_build.py::_planned_artifact_stub` produces fixtures with exactly one pattern per round, so any single-pattern block trivially satisfies any ordering invariant.
- `tests/test_reproduction_readiness.py::_proposer_context_rounds` also produces single-pattern rounds.
- The P101 convergence note explicitly deferred this question: "Whether `presentation_order` should strictly follow the sorting order of `support_rank` ... defer, as paper clustering groups by exact signature match, but presentation order by support is a separate heuristic ordering step that may allow ties."

Inference (architecture decisions, labeled as inference):
- **Enforcement scope:** Validate only the partial order induced by distinct `size` values. When two patterns have different `size` values, the pattern with the larger `size` must carry the smaller `presentation_order`. When `size` values are equal, no ordering constraint is applied, preserving the paper's "and estimated actionability" degrees of freedom.
- **Enforcement layer:** `_held_in_failure_patterns_block` is the authoritative structural validator; adding the partial-order check there keeps all presentation-order validation in one place and feeds both bundle verification and capture-manifest diff paths.
- **Hash rotation:** None. All existing fixtures use single-pattern rounds, so the new invariant is satisfied vacuously. No canonical audit hash, reproduction-readiness fixture hash, or release-candidate evidence hash rotates.

## Required Changes

None blocking. Decisions resolved:
1. In `_held_in_failure_patterns_block`, after the existing contiguous-permutation check, collect `(size, presentation_order)` pairs for patterns that declared `presentation_order`.
2. For every pair of patterns `(i, j)` with `size_i > size_j`, require `presentation_order_i < presentation_order_j`. Report the first violation as `f"{label}.patterns support-rank ordering violation: cluster {cluster_id_i} (size={size_i}) must precede cluster {cluster_id_j} (size={size_j})"`.
3. Do not enforce ordering among equal-`size` patterns; document this boundary in the error message metadata only if a violation is reported.

## Revised Plan

**P102 — Support-rank ordering validation for failure patterns (paper Section 3.2)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):
- `src/self_harness/_artifact_shapes.py::_held_in_failure_patterns_block`:
  - After the existing contiguous-permutation check succeeds, build a list of `(cluster_id, size, presentation_order)` tuples for patterns that declared `presentation_order`.
  - For each ordered pair where `size_a > size_b` but `presentation_order_a > presentation_order_b`, return a descriptive error citing both cluster ids and sizes.
  - Equal-`size` pairs are skipped intentionally; document this in a comment referencing Section 3.2's "and estimated actionability".

Tests:
- `tests/test_reproduction_readiness.py`:
  - Add a test that loads the class-shaped `proposer_context_manifest` artifact, appends a second pattern to `rounds[0].held_in_failure_patterns.patterns` with a smaller `size` but a smaller `presentation_order` than the existing pattern, adjusts `pattern_count` and `presentation_order` values to remain a valid permutation, and asserts `verify_reproduction_bundle` fails at the `artifact_proposer_context_manifest` check with "support-rank ordering violation" in the detail.
  - Add a test confirming that two patterns with equal `size` but arbitrary `presentation_order` values pass validation.
  - Add a test confirming that two patterns with distinct sizes correctly ordered by size descending pass validation.

Docs:
- `docs/architecture/schema_changelog.md`: add P102 entry under proposer-context manifest notes, explicitly stating this is a behavioral tightening of `_held_in_failure_patterns_block`, not a schema bump; reference Section 3.2 support-and-actionability ordering and note the tie-tolerance boundary.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposer_context_ingredients.notes` to mention that `presentation_order` must respect support-rank ordering when sizes differ.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposer_context_manifest` row description to note the support-rank ordering invariant.
- `docs/architecture/productionization_brief.md`: append P102 entry in established style.

Non-goals:
- No enforcement of ordering among equal-`size` patterns (actionability may decide).
- No derivation or storage of `support_rank` as a field; it remains a conceptual ordering key.
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged.
- Schema changelog entry present and references Section 3.2 support-and-actionability ordering.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether a future slice should enforce that `actionability_hint_sha256` values are pairwise distinct across clusters with equal `size` — defer, as the paper does not require actionability hints to be unique, only that they inform ordering.
2. Whether `cluster_id` should participate in ordering validation when `size` values are equal — defer. Enforcing a cluster-id tie-breaker would remove operator freedom to order equal-support clusters by actionability.
