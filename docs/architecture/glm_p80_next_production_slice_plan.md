CONVERGED: YES

## Verdict

P80 should implement an additive `proposer_context_manifest` reproduction artifact that binds the four paper-required Harness Proposal context ingredients (editable surfaces, verifier-grounded held-in failure patterns, passing-behavior summaries, and previous-attempted-edit summaries) to the already-required `proposer_llm_request_log`. This is higher priority than any other locally implementable slice because P78/P79 bound the proposer schedule but leave the proposer *inputs* unverified — the paper's Section 3.3 explicitly lists these four ingredients as the bounded proposal context, and the current bundle cannot reject a proposer log whose request hash was computed over an empty or divergent context.

The slice is safe under all stated constraints: offline-only, additive artifact-class shape, single new cross-artifact invariant, no canonical audit-hash rotation, `reproduction_ready:false` preserved, no live contact.

## Critique

Initial mental draft considered extending `proposer_llm_request_log` with context fields directly. Rejected: that would churn the P78 schema and force re-rotation of proposer-log fixtures for an input-side concern. Better to introduce a sibling primary-captured artifact that the existing proposer log binds to via a new cross-artifact check, matching the P76/P77 trust-digest pattern.

Considered also whether to model the four ingredients as four separate artifact classes. Rejected: paper presents them as a single bounded proposal context, and splitting them would multiply schema surface without buying independent verifiability — the proposer sees them together and the request hash covers them together.

Risk considered: the four ingredient payloads are potentially large (full editable-surface declarations, full held-in failure-pattern bundle). Mitigation: the manifest stores canonical SHA-256 hashes plus compact structural summaries (counts, ids, surface-kind enums), not raw payloads. This matches the existing `proposer_llm_request_log` request-hash discipline.

## Required Changes

None blocking. The plan below is executable as-is.

## Revised Plan

**Goal:** bind proposer request hashes to the four Section 3.3 context ingredients via one new additive artifact class and one new cross-artifact invariant.

### Files

- `src/self_harness/_artifact_shapes.py`
  - Add `_proposer_context_manifest` validator.
  - Required fields: `schema_version="1.0"`, `ok=true`, `mode="live"`, `capture_run_id`, `round_count` (positive int), `rounds[]` with `round_index` contiguous from 0, and four ingredient blocks per round:
    - `editable_surfaces`: `{surfaces: [{kind, name, sha256}], surface_count}`
    - `held_in_failure_patterns`: `{patterns: [{cluster_id, size, mechanism_sha256}], pattern_count}`
    - `passing_behavior_summaries`: `{summaries: [{task_id_set_sha256, preserved_behavior_sha256}], summary_count}`
    - `previous_attempted_edits`: `{edits: [{round_index, surface, decision}], edit_count}`
  - Each `surfaces[].sha256`, `mechanism_sha256`, `preserved_behavior_sha256` must be 64-char lowercase hex.
  - Closed top-level field set; `reproduction_claimed=false`; shared `CAPTURE_EXTRACT_BOUNDARY`-style boundary string.
  - Register in `REPRODUCTION_ARTIFACT_CLASS_VALIDATORS`.

- `src/self_harness/capture_extract.py`
  - Add `proposer_context_manifest` to `EXTRACTABLE_ARTIFACT_CLASSES`.
  - Add `extract_proposer_context_manifest(envelope, context_rows, *, capture_run_id)` mirroring `extract_proposer_llm_request_log` shape discipline: rejects reproduction claims, unknown fields, non-live modes, hash malformation, round gaps, count drift.
  - Add `--proposer-context-log` and per-round context-row JSONL ingestion in `extract_artifact_from_paths` dispatch.

- `src/self_harness/reproduction_bundle.py`
  - Add `proposer_context_manifest` to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES` so `cross_artifact_capture_run_id_binding` enforces it.
  - Add `_cross_artifact_proposer_context_binding(bundle, context_entry, proposer_entry, protocol_entry)`:
    - Skips only when both context and proposer logs absent.
    - Fails closed if exactly one of context/proposer present.
    - Requires `round_count` equality across context manifest, proposer log, and `fixed_protocol_config.self_harness_rounds`.
    - Requires per-round `round_index` alignment between context manifest and proposer log.
    - Requires each round's four ingredient blocks to be non-empty when the proposer round recorded `attempted_proposals > 0`.
  - Wire into `_cross_artifact_invariants`.

- `docs/operations/benchmark_reproduction_requirements.json`
  - Add `proposer_context_ingredients` requirement row referencing paper Section 3.3, `required_artifact_class: proposer_context_manifest`, `required_state: provisioned`, notes describing the four ingredients and the new cross-artifact bindings.

- `src/self_harness/capture_manifest_build.py`
  - Add `_planned_artifact_stub` branch for `proposer_context_manifest` producing a deterministic minimal valid shape with one surface, one pattern, one summary, one edit per round, all hashes `0*64`.
  - Add to fixed-protocol-hash consumer list (context manifest does NOT carry `fixed_protocol_sha256` directly; it binds via proposer log round alignment).

- `tests/test_reproduction_readiness.py`
  - Extend `_class_shaped_payloads()` with `proposer_context_manifest` fixture.
  - Add tests:
    - bundle accepts signed class-shaped artifacts with new class present;
    - bundle rejects context/proposer presence asymmetry;
    - bundle rejects `round_count` drift between context manifest and proposer log;
    - bundle rejects per-round ingredient blocks empty when proposer recorded attempted proposals;
    - bundle rejects malformed ingredient hashes;
    - bundle skips binding cleanly when both context and proposer absent (reduced non-paper bundle);
    - `capture-admit`/`capture-extract` round-trip for the new class against fixture JSONL;
    - `capture_manifest_diff` includes the new primary captured class in run-id binding.

- `scripts/reproduction_bundle_build.py`, `scripts/capture_extract.py`, `scripts/capture_admit.py`
  - No schema change; inherit new class through existing `benchmark_reproduction_requirements.json` derivation. Verify CLI smoke via existing `make` targets.

- `docs/architecture/schema_changelog.md`
  - Add `## P80 Proposer Context Ingredients Binding` section documenting the new artifact class, the new cross-artifact check, fixture-hash rotation scope (release-candidate, capture-manifest, capture-rehearsal, reproduction-readiness; NOT canonical audit hash), and the explicit non-reproduction boundary.

- `docs/operations/release_candidate_evidence.md` (if present) or equivalent operator doc
  - One paragraph noting the new required artifact class for paper-faithful bundles.

### Invariants

1. `proposer_context_manifest.round_count == proposer_llm_request_log.round_count == fixed_protocol_config.self_harness_rounds`.
2. Per-round `round_index` sequences match exactly between context manifest and proposer log.
3. Every proposer round with `attempted_proposals > 0` has non-empty `editable_surfaces`, `held_in_failure_patterns`, `passing_behavior_summaries`, and `previous_attempted_edits` (previous-edits block may be empty only at `round_index == 0`).
4. `proposer_context_manifest.capture_run_id` equals the shared primary capture run id.
5. `reproduction_claimed == false` everywhere; `mode == "live"`; closed top-level field set.

### Test Cases

- Happy path: full class-shaped bundle verifies with new class present and bound.
- Drift: context manifest round_count = 2 while proposer log round_count = 3 → `cross_artifact_proposer_context_binding` fails with metadata showing both values.
- Asymmetry: proposer log present, context manifest absent → fail closed.
- Reduced bundle: both proposer log and context manifest absent → binding skipped, bundle still ok for non-paper reduced requirement set.
- Empty ingredients on attempted round: fail.
- Empty previous-edits at round 0 only: pass; same at round >0: fail.
- Malformed `surfaces[].sha256` (63 hex chars): rejected by shape validator before cross-artifact stage.
- Capture-extract round trip: raw JSONL with unknown field → `CaptureExtractError`.
- Run-id binding: context manifest with different `capture_run_id` → `cross_artifact_capture_run_id_binding` fails listing the new class.

### Fixture Rotations

Expected rotations (all advisory release/operator fixtures; canonical audit/readiness hash unchanged):
- `tests/fixtures/release_candidate/reproduction_readiness_result.json`
- `tests/fixtures/release_candidate/expected_hash.txt`
- `tests/fixtures/release_candidate/release_candidate_evidence.json` (if hash-pinned)
- `tests/fixtures/capture_manifest/*` planned-artifact hashes
- `tests/fixtures/capture_rehearsal/*` report hashes

Explicitly NOT rotated:
- `tests/fixtures/canonical_audit_hash.txt`
- `tests/fixtures/canonical_llm_audit_hash.txt`
- audit schema version (stays at 1.4)
- corpus schema version

### Docs

- `docs/architecture/schema_changelog.md`: new P80 section per template above.
- `docs/operations/benchmark_reproduction_requirements.json`: new requirement row.
- `README.md` or `docs/operations/release_candidate_evidence.md`: one-line note that paper-faithful bundles now require proposer context evidence.

### Stop Conditions

- All listed tests pass on Python 3.11/3.12/3.13.
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-admit-check`, `make release-candidate-evidence` all green.
- `reproduction_ready:false` preserved on every generated report.
- No canonical audit/hash rotation.
- No live Harbor/Docker/model/PyPI/Sigstore contact in any default target.
- `docs/architecture/schema_changelog.md` explains the rotation scope.

## Remaining Open Questions

None blocking. Two advisory items for future slices, out of scope here:

1. Whether `proposer_context_manifest` should eventually carry detached signatures over each ingredient payload (e.g., signed editable-surface declaration) — defer until a real operator capture pipeline exists.
2. Whether `previous_attempted_edits` at round 0 should permit a single "initial harness" sentinel entry instead of an empty list — current plan allows empty at round 0 only; revisit if operators report ambiguity.
