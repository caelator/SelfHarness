CONVERGED: YES

## Verdict

P101 — Failure-pattern signature distinctness (paper Section 3.2: "Failures are clustered by exact agreement of this signature... two failed cases are grouped together only when they agree on what the verifier ultimately rejected, how the agent behavior contributed to that rejection, and which reusable behavioral mechanism was involved") is a precise, locally implementable paper-fidelity slice. The paper strictly defines clusters by the failure signature `phi(r) = (c, q, m)`. The current `proposer_context_manifest` artifact allows multiple patterns per round but does not enforce that their `(c, q, m)` signatures are unique. Enforcing this in the artifact shape validator guarantees that the evaluation system's exact-match clustering contract is respected. Existing test fixtures and builders only produce a single pattern per round, so no canonical hashes will rotate, no new artifact classes are required, and no live infrastructure contact is needed.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_held_in_failure_patterns_block` iterates over the `patterns` list to validate individual fields like `mechanism_sha256`, `failure_category`, `causal_status_sha256`, and `presentation_order`.
- It does not check cross-pattern uniqueness for the failure signature `(failure_category, causal_status_sha256, mechanism_sha256)`.
- Paper p.5 Section 3.2 defines clustering as exact agreement on `phi(r) = (c, q, m)`. Distinct clusters must therefore have distinct signatures.
- `src/self_harness/capture_manifest_build.py::_planned_artifact_stub` and `tests/test_reproduction_readiness.py::_class_shaped_payloads` both generate `proposer_context_manifest` fixtures with exactly one failure pattern per round. 

Inference (architecture decisions, labeled as inference):
- **Enforcement layer:** The artifact shape validator (`_held_in_failure_patterns_block`) is the authoritative enforcement point. If patterns with duplicate signatures are present, the artifact violates the structural definition of a cluster.
- **Signature composition:** The distinctness check applies to the tuple `(pattern.get("failure_category"), pattern.get("causal_status_sha256"), pattern.get("mechanism_sha256"))`. `None` values are valid parts of the signature tuple, ensuring that `(None, None, m)` is treated as distinct from `(c, q, m)`.
- **Hash rotation:** None. The canonical audit hash and reproduction bundle fixture hash remain unchanged because all existing fixtures produce valid (uniquely-signed or single-pattern) manifests.

## Required Changes

None blocking. Decisions resolved:
1. Add a `seen_signatures: set[tuple]` tracker in `_held_in_failure_patterns_block`.
2. For each pattern, compute the `(failure_category, causal_status_sha256, mechanism_sha256)` tuple.
3. If a signature is repeated, return an error: `f"{label}.patterns[{index}] duplicate failure signature (category, causal_status, mechanism)"`.

## Revised Plan

**P101 — Failure-pattern signature distinctness (paper Section 3.2)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):
- `src/self_harness/_artifact_shapes.py::_held_in_failure_patterns_block`:
  - Initialize `seen_signatures: set[tuple[str | None, str | None, str]] = set()` before the loop over patterns.
  - After validating the individual `mechanism_sha256`, `failure_category`, and `causal_status_sha256` fields for a pattern, construct the tuple: `signature = (failure_category, causal_status_sha256, mechanism_sha256)`.
  - Check if the signature is in `seen_signatures`. If so, return an error indicating a duplicate failure signature.
  - Otherwise, add the signature to the set.

Tests:
- `tests/test_reproduction_readiness.py`:
  - Add a test that loads the class-shaped `proposer_context_manifest` artifact, appends a second pattern to `rounds[0].held_in_failure_patterns.patterns` that duplicates the `(failure_category, causal_status_sha256, mechanism_sha256)` of the first pattern, adjusts `pattern_count`, and asserts `verify_reproduction_bundle` fails at the `artifact_proposer_context_manifest` check with "duplicate failure signature" in the detail.
  - Add a test confirming that adding a second pattern with a different `mechanism_sha256` passes validation.

Docs:
- `docs/architecture/schema_changelog.md`: add P101 entry under proposer-context manifest notes, explicitly stating this is a behavioral tightening of `_held_in_failure_patterns_block`, not a schema bump; reference Section 3.2 exact-match clustering.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposer_context_ingredients.notes` to mention that distinct failure patterns must carry distinct `(failure_category, causal_status_sha256, mechanism_sha256)` signatures.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposer_context_manifest` row description to note the signature distinctness invariant.
- `docs/architecture/productionization_brief.md`: append P101 entry in established style.

Non-goals:
- No enforcement of distinct `shared_symptoms_sha256` or `verifier_evidence_sha256` across clusters (different clusters might theoretically share symptoms).
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged.
- Schema changelog entry present and references Section 3.2 clustering.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether a future slice should enforce that `cluster_id` strings exactly match some deterministic derivation of the signature components — defer, as `cluster_id` is treated as an opaque unique string by current tooling.
2. Whether `presentation_order` should strictly follow the sorting order of `support_rank` (derived from `size` descending) — defer, as paper clustering groups by exact signature match, but presentation order by support is a separate heuristic ordering step that may allow ties.
