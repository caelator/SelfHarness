# Round 1

CONVERGED: YES

## Verdict
The two named gaps (thin `previous_attempted_edits` block, missing proposer-context-evidence-derivation plan-vs-realized diff) are both locally implementable, offline-only, paper-fidelity improvements that do not require any live Harbor/Docker/model/PyPI/Sigstore contact and do not rotate the canonical core audit/corpus hashes. The plan below is tight enough to execute after one internal critique-revise pass: schema additions are scoped to the already-versioned reproduction-only `proposer_context_manifest` artifact, the new cross-artifact check reuses existing helpers, and the new diff finding reuses the existing `_split_task_ids` / `read_artifact_payload` plumbing.

## Critique
- **Initial draft weakness:** I first considered adding the binding only at bundle-verification time. That is insufficient because (i) the proposer-context artifact must itself carry the richer fields so capture extraction has somewhere to put them, and (ii) plan-vs-realized diffing is a distinct operator surface (`capture_manifest_diff`) that currently has no finding category for proposer evidence derivation.
- **Backward-compat risk:** P80/P81 already shipped `previous_attempted_edits` with `{round_index, surface, decision}`. The paper-fidelity-correct move is to make the new binding fields **required** (not optional) and rotate the reproduction-only fixture hashes, matching the project's established pattern (P78/P79/P80/P81 all rotated paper-faithful fixture hashes while leaving `tests/fixtures/canonical_audit_hash.txt` and `canonical_llm_audit_hash.txt` unchanged).
- **Skipping semantics:** The new cross-artifact invariant must be skipped entirely when either `proposer_context_manifest` or `proposer_llm_request_log` is absent, preserving the P80/P81 contract that reduced non-paper bundles can omit proposer evidence.
- **Round-zero edge case:** Round 0 legitimately has no previous edits; the binding must only enforce the proposal/mechanism/surface links for `round_index > 0`.
- **Plan-vs-realized asymmetry:** A capture manifest's planned `proposer_context_manifest` is a deterministic stub (per P59) and cannot know which tasks will fail/pass. The diff finding therefore cannot compare planned failure-pattern contents; it can only verify the realized context references the planned fixed split's held-in task set.

## Required Changes
1. **Schema (required, not optional):** Widen `_PREVIOUS_ATTEMPTED_EDIT_FIELDS` from `{round_index, surface, decision}` to also require `proposal_round_index`, `targeted_mechanism_sha256`, `edited_surface_sha256`, `audit_decision` (closed enum: `accepted|rejected|invalid`), and `audit_decision_reason` (required and non-empty unless `audit_decision == "accepted"`).
2. **Capture extraction contract:** Extend `_PROPOSER_CONTEXT_LOG_ROW_FIELDS` and `extract_proposer_context_manifest` to read the richer raw log fields, normalize them, and pass them through to the validated payload.
3. **New bundle invariant:** Add `cross_artifact_proposer_previous_edits_binding` to `reproduction_bundle.py`, gated on presence of both `proposer_context_manifest` and `proposer_llm_request_log`, enforcing:
   - Every `previous_attempted_edits[].proposal_round_index` is `< round_index` and corresponds to a real prior round.
   - Every `targeted_mechanism_sha256` exists in some `held_in_failure_patterns.patterns[].mechanism_sha256` of the referenced prior round.
   - Every `edited_surface_sha256` exists in the referenced prior round's `editable_surfaces.surfaces[].sha256`.
   - `audit_decision` is in the closed enum; `audit_decision_reason` non-empty when decision ≠ `accepted`.
4. **New diff finding:** Add `proposer-context-evidence-derivation` to `capture_manifest_diff.py`, gated on presence of planned `live_terminal_bench_split_manifest` and realized `proposer_context_manifest`, enforcing that the union of realized failure-pattern `task_ids` and passing-summary `task_ids` equals the planned `held_in_task_ids` set exactly.
5. **Fixture rotation:** Rotate `tests/fixtures/release_candidate/*` and any `tests/fixtures/capture_manifest/*` and `tests/fixtures/reproduction_readiness*` hashes that include the new context fields; leave `tests/fixtures/canonical_audit_hash.txt` and `tests/fixtures/canonical_llm_audit_hash.txt` unchanged.
6. **Requirement catalog note:** Append a `cross_artifact_proposer_previous_edits_binding` and `proposer-context-evidence-derivation` mention to the `proposer_context_ingredients` row of `docs/operations/benchmark_reproduction_requirements.json` so the requirements doc stays synchronized with the verifier surface.

## Revised Plan

**Scope: P82 proposer previous-edits binding + P83 capture-manifest-diff proposer-context-evidence-derivation finding.**

### Files to modify
- `src/self_harness/_artifact_shapes.py`
  - Widen `_PREVIOUS_ATTEMPTED_EDIT_FIELDS`.
  - Extend `_previous_attempted_edits_block` validator: closed-enum check on `audit_decision`; conditional non-empty check on `audit_decision_reason`; SHA-256 grammar on `targeted_mechanism_sha256` and `edited_surface_sha256`; non-negative int with `proposal_round_index` field.
- `src/self_harness/capture_extract.py`
  - Extend `_PROPOSER_CONTEXT_LOG_ROW_FIELDS`.
  - Update `extract_proposer_context_manifest` to forward the new sub-fields verbatim through `_context_block` (already generic) once shape validation accepts them.
- `src/self_harness/reproduction_bundle.py`
  - Add `_cross_artifact_proposer_previous_edits_binding(bundle, context_entry, proposer_entry)` returning a `ReproductionBundleCheck | None`, skipped unless both entries exist.
  - Wire into `_cross_artifact_invariants` after `_cross_artifact_proposer_context_binding`.
- `src/self_harness/capture_manifest_diff.py`
  - Add `_proposer_context_evidence_findings(manifest, bundle)` returning `list[CaptureManifestDiffFinding]`, called from `diff_capture_manifest_to_bundle` after `_fixed_protocol_findings`.
  - Skipped unless both planned `live_terminal_bench_split_manifest` and realized `proposer_context_manifest` are present.
- `docs/operations/benchmark_reproduction_requirements.json`
  - Extend the `proposer_context_ingredients` row's `notes` with the two new binding names.
- `docs/architecture/schema_changelog.md`
  - Add P82 and P83 sections describing the schema widening and the new diff category, with explicit "no canonical hash rotation, no live contact, no reproduction claim" boundary language.
- `docs/architecture/productionization_brief.md`
  - Append P82 and P83 status blocks following the existing P80/P81 template.
- `tests/test_reproduction_readiness.py`
  - Extend `_proposer_context_previous_edits` helper to populate the new required fields.
  - Add tests: prior-round binding pass; mismatched `proposal_round_index` fail; unknown `targeted_mechanism_sha256` fail; unknown `edited_surface_sha256` fail; bad `audit_decision` enum fail; missing `audit_decision_reason` on rejected fail; round-zero empty previous-edits still passes.
- `tests/test_capture_manifest.py`
  - Extend `_proposer_context_rounds` (or local equivalent) to populate the new fields.
  - Add tests: diff passes when realized context covers planned held-in set; diff fails when a realized failure-pattern task id is outside the planned held-in set; diff fails when the realized context omits a planned held-in task id; diff is skipped when proposer context artifact absent.

### Invariants
- `reproduction_claimed == false` everywhere; never rotate `canonical_audit_hash.txt` or `canonical_llm_audit_hash.txt`.
- New invariant is skipped entirely when proposer evidence is absent (preserves reduced non-paper bundle path).
- Round-zero previous-edits block remains `{edit_count: 0, edits: []}`.
- No new artifact class; no new readiness dependency; no audit/corpus/manifest schema bump.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/scanner-db/cloud contact.

### Fixture rotations (expected)
- `tests/fixtures/release_candidate/reproduction_readiness_result.json` hash (because requirement-catalog notes change).
- Any `tests/fixtures/capture_manifest/*` and `tests/fixtures/release_candidate/*` hashes that embed the richer proposer context payload.
- `tests/fixtures/release_candidate/expected_hash.txt` if release-candidate evidence embeds the new reproduction-readiness gate metadata.

### Stop conditions
- All new and existing reproduction-bundle and capture-manifest-diff tests pass under `make check`, `make readiness`, and `make capture-manifest-check`.
- `reproduction_ready` remains `false` in every committed fixture.
- Canonical audit hash and canonical LLM audit hash unchanged.
- Schema changelog, brief, and requirements catalog all updated in the same change.

## Remaining Open Questions
- None blocking. The only design choice that could be revisited in a later round is whether `audit_decision_reason` should also be required for `accepted` decisions to improve proposer-side signal richness; the paper does not mandate this and the current plan keeps it optional for `accepted`, matching Algorithm 1's silent-accept path.

[usage] {"completion_tokens": 4134, "completion_tokens_details": {"reasoning_tokens": 2150}, "prompt_tokens": 105010, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 109144}
