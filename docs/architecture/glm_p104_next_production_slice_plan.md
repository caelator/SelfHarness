CONVERGED: YES

## Verdict

P104 — Previous-attempted-edits distinctness within a proposer-context round is the next precise, locally implementable paper-fidelity slice after P103. The paper Section 3.3 constrains the proposer's bounded context to "summaries of previously attempted edits," and Section 3.3's broader principle is that context ingredients be minimal and non-redundant. P101 made failure signatures pairwise distinct, P102 enforced support ordering, and P103 enforced task-id disjointness, but no invariant prevents a `previous_attempted_edits` block within one round from carrying two summary rows that reference the same `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` triple. Such duplication is structurally redundant and cannot arise from a faithful proposer context: one prior attempted edit produces at most one summary entry.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_previous_attempted_edits_block` validates per-edit shape and audit-decision closure but never checks pairwise distinctness of `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` tuples within a single `edits` list.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposer_previous_edits_binding` iterates edits and verifies each binds to a real prior proposer/context round with matching mechanism, causal status, and editable surface, but it does not detect duplicate references within the same `previous_attempted_edits` block.
- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary` (and the broader diff helpers) extract per-cluster metadata for failure patterns but do not summarize previous-attempted-edit signatures, so plan-vs-realized drift cannot observe duplicate-edit drift today.
- The class-shaped fixtures in `tests/test_reproduction_readiness.py::_proposer_context_rounds` and `_previous_attempted_edits` always emit zero or one edit per round, so the invariant is satisfied vacuously and no canonical or operator-evidence fixture hash will rotate.

Inference (architecture decisions, labeled as inference):
- **Enforcement layer:** Distinctness belongs in both `_previous_attempted_edits_block` (shape validation) and `_cross_artifact_proposer_previous_edits_binding` (cross-artifact binding) so that a shape-valid artifact with intra-block duplicates is still rejected when prior rounds are not yet consulted, and so that the cross-artifact layer records the structural violation if shape validation is bypassed in any future ingestion path.
- **Signature choice:** `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` mirrors the P101 failure-signature pattern and matches the fields P82 already binds to prior proposer/context evidence. Including `audit_decision` would be incorrect because the same prior attempted edit should not be summarized twice even with the same decision.
- **Error surface:** Report the first duplicate triple with the edit indexes involved, mirroring P101/P103 diagnostics.
- **Hash rotation:** None. Existing fixtures emit at most one edit per round, so no canonical readiness hash, reproduction-readiness fixture hash, or release-candidate evidence hash rotates.

## Required Changes

None blocking. Decisions resolved:
1. In `_previous_attempted_edits_block`, after the existing per-edit validation, accumulate `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` tuples across `edits` and reject the first duplicate with a descriptive error referencing the two edit indexes.
2. In `_cross_artifact_proposer_previous_edits_binding`, after the per-edit binding loop, record `previous_edit_duplicate_violations` metadata and fail the check when the same `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` triple appears more than once within one round's `previous_attempted_edits`.
3. In `capture_manifest_diff.py`, extend `_proposer_context_failure_category_summary` (or add a sibling helper) to capture `previous_attempted_edit_signature_duplicate_count` per round so plan-vs-realized rehearsals surface duplicate-edit drift before bundle verification.

## Revised Plan

**P104 — Previous-attempted-edits distinctness within a proposer-context round (paper Section 3.3)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):

- `src/self_harness/_artifact_shapes.py::_previous_attempted_edits_block`:
  - After per-edit field validation, build an ordered seen-set of `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)`.
  - On the first duplicate, return a descriptive error: `f"{label}.edits duplicate previous-attempted-edit signature: edit {dup_index} repeats ({proposal_round_index}, {targeted_mechanism_sha256}, {edited_surface_sha256})"`.
  - Comment references Section 3.3's bounded/minimal proposer context: one prior attempted edit yields at most one summary row.

- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposer_previous_edits_binding`:
  - For each round, while iterating edits, track seen `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` triples.
  - When a triple repeats, append a `previous_edit_duplicate_violations` entry capturing `round_index`, `edit_index`, `proposal_round_index`, `targeted_mechanism_sha256`, `edited_surface_sha256`, and `first_seen_edit_index`.
  - Append a corresponding failure message to `failures` and include the violation list in `metadata`.

- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary`:
  - Extend the per-round summary with `previous_attempted_edit_signature_duplicate_count`, computed as `total_edit_signatures - distinct_edit_signatures` using the same triple.
  - The existing `_proposer_context_pattern_hash_drifts` helper (or a sibling) then compares the count between planned and realized context so rehearsal catches duplicate-edit drift.

Tests:

- `tests/test_reproduction_readiness.py`:
  - Add `test_reproduction_bundle_rejects_duplicate_previous_attempted_edit_signature`: load the class-shaped `proposer_context_manifest` artifact, append a second edit in `rounds[1].previous_attempted_edits.edits` with the same `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` as the existing edit but a different `audit_decision_reason`, and assert `verify_reproduction_bundle` fails at `artifact_proposer_context_manifest` with "duplicate previous-attempted-edit signature" in the detail.
  - Add `test_reproduction_bundle_records_duplicate_previous_attempted_edit_signature_cross_artifact`: construct a shape-valid context where two edits share the same triple (requires monkeypatching `artifact_shape_error` like the existing P103 cross-artifact test), then assert the cross-artifact check records `previous_edit_duplicate_violations` with both edit indexes.
  - Add `test_reproduction_bundle_accepts_distinct_previous_attempted_edit_signatures`: extend the fixture with a second edit in `rounds[2].previous_attempted_edits.edits` referencing a distinct prior proposal triple and confirm bundle verification still passes.

- `tests/test_capture_manifest.py`:
  - Add a test that modifies the planned capture manifest's proposer-context artifact to include duplicate-edit signatures within a round and asserts the diff report records `previous_attempted_edit_signature_duplicate_count` drift (planned 0 → realized 1, or vice versa).

Docs:

- `docs/architecture/schema_changelog.md`: add P104 entry under proposer-context manifest notes, stating this is a behavioral tightening of `_previous_attempted_edits_block` and `_cross_artifact_proposer_previous_edits_binding`, not a schema bump; reference Section 3.3 bounded/minimal proposer context.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposer_context_ingredients.notes` to mention that previous-attempted-edit summaries must be pairwise distinct by `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` within each round.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposer_context_manifest` row description to note the previous-edit distinctness invariant.
- `docs/architecture/productionization_brief.md`: append P104 entry in established style.

Non-goals:
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No enforcement that every prior-round attempted proposal must appear in subsequent previous-attempted-edits blocks; the paper's "summaries" language permits filtering, so completeness is out of scope.
- No enforcement of distinctness across rounds (an edit summary legitimately persists across iterations as the proposer accumulates context).
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged.
- Schema changelog entry present and references Section 3.3 bounded/minimal proposer context.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether `previous_attempted_edits` should be required to enumerate all committed proposals from prior rounds (completeness rather than distinctness) — defer; the paper's "summaries of previously attempted edits" wording is permissive, and operators may reasonably filter to salient edits.
2. Whether the same distinctness rule should apply to `passing_behavior_summaries` keyed by `task_id_set_sha256` — defer; P103's open question #2 already flagged this as low value, and the current single-summary fixtures make it academic until operators bundle richer evidence.
3. Whether inter-round stability of previous-attempted-edit summaries should be tracked (an edit summary present in round t should persist in round t+1) — defer; the paper does not require monotonic accumulation, and enforcing it would couple the invariant to proposer-implementation choices.
