CONVERGED: YES

## Verdict

P105 — Editable-surface distinctness within a proposer-context round is the next precise, locally implementable paper-fidelity slice after P104. The paper Section 3.3 defines the proposer's bounded context as including "the editable surfaces of the current harness" — a set of distinct configuration points the proposer may modify. P101 made failure signatures pairwise distinct, P103 enforced failure-pattern task-id disjointness, and P104 enforced previous-attempted-edit distinctness. No invariant enforces that `editable_surfaces.surfaces[]` within one round are pairwise distinct by `sha256` (equivalently by `name`, since `sha256` is derived from `name` via the capture-extract convention). Duplicate surface declarations are structurally redundant and ambiguous: a candidate binding its `edited_surface_sha256` to a duplicated surface hash cannot be said to target a unique editable surface.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_editable_surfaces_block` validates per-surface `kind`, `name`, and `sha256` shape but never checks pairwise distinctness of `sha256` (or `name`) across `surfaces[]` within the same block.
- `src/self_harness/reproduction_bundle.py::_context_editable_surface_sha256s` and `_context_editable_surface_names` return `frozenset`, so duplicate surfaces are silently collapsed during cross-artifact binding rather than rejected.
- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary` extracts per-pattern metadata for failure patterns but does not summarize editable-surface duplicates, so plan-vs-realized drift cannot observe duplicate-surface drift.
- The class-shaped fixtures in `tests/test_reproduction_readiness.py::_proposer_context_rounds` always declare exactly one surface per round, so the invariant is satisfied vacuously and no canonical audit hash, reproduction-readiness fixture hash, or release-candidate evidence hash will rotate.

Inference (architecture decisions, labeled as inference):
- **Enforcement layer:** Distinctness belongs in both `_editable_surfaces_block` (shape validation) and `_cross_artifact_proposer_context_binding` (cross-artifact binding) so a shape-valid artifact with duplicate surfaces is still rejected when proposer context is bound to proposer LLM and fixed protocol evidence, and so the cross-artifact layer records the structural violation if shape validation is bypassed.
- **Signature choice:** `sha256` is the authoritative key because the capture-extract convention derives it deterministically from `name` (`sha256(stable_json({"changed_surfaces":[name]}) + "\n")`). Two surfaces with the same `sha256` necessarily have the same `name`; two surfaces with the same `name` necessarily have the same `sha256`. Checking either suffices; checking `sha256` is consistent with P101/P103/P104's hash-keyed distinctness pattern.
- **Error surface:** Report the first duplicate `sha256` with the two surface indexes and the surface `name`, mirroring P101/P104 diagnostics.
- **Hash rotation:** None. Existing fixtures declare one surface per round, so no canonical readiness hash, reproduction-readiness fixture hash, or release-candidate evidence hash rotates.

## Required Changes

None blocking. Decisions resolved:
1. In `_editable_surfaces_block`, after per-surface field validation, accumulate `sha256` values across `surfaces[]` and reject the first duplicate with a descriptive error referencing the two surface indexes and the surface `name`.
2. In `_cross_artifact_proposer_context_binding`, when iterating context rounds, record `editable_surface_duplicate_violations` metadata and fail the check when the same `sha256` appears more than once within a round's `editable_surfaces.surfaces[]`.
3. In `capture_manifest_diff.py::_proposer_context_failure_category_summary` (or a sibling helper), include `editable_surface_duplicate_count` per round so plan-vs-realized rehearsals surface duplicate-surface drift before bundle verification.

## Revised Plan

**P105 — Editable-surface distinctness within a proposer-context round (paper Section 3.3)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):

- `src/self_harness/_artifact_shapes.py::_editable_surfaces_block`:
  - After per-surface field validation, build an ordered seen-set of `sha256` values.
  - On the first duplicate, return a descriptive error: `f"{label}.surfaces duplicate editable surface: surface {dup_index} repeats sha256 {sha256_value} (name={name})"`.
  - Comment references Section 3.3's bounded proposer context: each editable surface is a distinct harness configuration point.

- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposer_context_binding`:
  - For each context round, while collecting `editable_surfaces`, track seen `sha256` values.
  - When a `sha256` repeats, append an `editable_surface_duplicate_violations` entry capturing `round_index`, `surface_index`, `sha256`, `name`, and `first_seen_surface_index`.
  - Append a corresponding failure message to `failures` and include the violation list in `metadata`.

- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary`:
  - Extend the per-round summary with `editable_surface_duplicate_count`, computed as `total_surface_sha256s - distinct_surface_sha256s`.
  - The existing `_proposer_context_pattern_hash_drifts` helper then compares the count between planned and realized context so rehearsal catches duplicate-surface drift.

Tests:

- `tests/test_reproduction_readiness.py`:
  - Add `test_reproduction_bundle_rejects_duplicate_editable_surface_sha256`: load the class-shaped `proposer_context_manifest` artifact, append a second surface in `rounds[0].editable_surfaces.surfaces` with the same `sha256` and `name` as the existing surface (but a different `kind` to prove the check is keyed on `sha256`, not `kind`), and assert `verify_reproduction_bundle` fails at `artifact_proposer_context_manifest` with "duplicate editable surface" in the detail.
  - Add `test_reproduction_bundle_records_duplicate_editable_surface_sha256_cross_artifact`: construct a shape-valid context where two surfaces share the same `sha256` (requires monkeypatching `artifact_shape_error` like the existing P103/P104 cross-artifact tests), then assert the cross-artifact check records `editable_surface_duplicate_violations` with both surface indexes.
  - Add `test_reproduction_bundle_accepts_distinct_editable_surface_sha256s`: extend the fixture with a second surface in `rounds[1].editable_surfaces.surfaces` using a distinct `sha256` and `name` and confirm bundle verification still passes.

- `tests/test_capture_manifest.py`:
  - Add a test that modifies the planned capture manifest's proposer-context artifact to include duplicate surface `sha256` values within a round and asserts the diff report records `editable_surface_duplicate_count` drift (planned 0 → realized 1, or vice versa).

Docs:

- `docs/architecture/schema_changelog.md`: add P105 entry under proposer-context manifest notes, stating this is a behavioral tightening of `_editable_surfaces_block` and `_cross_artifact_proposer_context_binding`, not a schema bump; reference Section 3.3 bounded proposer context.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposer_context_ingredients.notes` to mention that editable surfaces must be pairwise distinct by `sha256` within each round.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposer_context_manifest` row description to note the editable-surface distinctness invariant.
- `docs/architecture/productionization_brief.md`: append P105 entry in established style.

Non-goals:
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No enforcement that editable surfaces must match a specific canonical harness definition; the surfaces remain operator-disclosed evidence.
- No enforcement of cross-round surface stability (the set of editable surfaces may change across rounds as the harness evolves).
- No enforcement that every editable surface is targeted by at least one candidate; the paper allows the proposer to choose among surfaces.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged.
- Schema changelog entry present and references Section 3.3 bounded proposer context.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether passing-behavior summaries should be pairwise distinct by `task_id_set_sha256` within each round — defer; P103 open question #2 already flagged this as low value because the paper does not constrain passing-behavior summary task sets and current fixtures use a single summary per round.
2. Whether inter-round editable-surface stability should be tracked (the set of editable surfaces should persist or grow monotonically across Self-Harness rounds) — defer; the paper's Algorithm 1 does not require surface-set stability, and the harness may expose different surfaces as its definition file evolves.
3. Whether the `kind` field on editable surfaces should be closed to an allowlist (e.g., `prompt`, `tool`, `memory`, `policy`) rather than free-form — defer; the paper's DeepAgent-based harness declares multiple surface kinds, but closing the vocabulary without live evidence risks over-constraining operator disclosures.
