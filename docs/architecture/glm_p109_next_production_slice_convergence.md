# Round 1

# CONVERGED: YES

## Verdict

P109 should close the explicit gap left by P108: **multi-commit `MERGEACCEPTED` harness-state hash continuity** for the `proposal_validation_manifest` evidence path.

Evidence:
- `docs/architecture/schema_changelog.md` (P108 entry) states: *"multi-commit transitions are recorded in `harness_continuity_skipped_rounds`, matching the P107 split-outcome rule because the merged harness state is not represented by one candidate row."*
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` already skips multi-commit transitions in both lineage and harness-hash continuity checks.
- Paper Algorithm 1 (`MERGEACCEPTED`) and Section 3.4 imply that *every* accepted merge should produce a deterministic next harness state, so the current skip is a real paper-fidelity gap, not just a cosmetic one.
- The fix is purely offline: it derives a merged hash from existing audit `lineage.json` material and adds it to the bundled evidence shape.

## Critique

Strengths of the slice:
- Closes an explicit P108 non-goal without introducing a new artifact class.
- No live-service dependency (Harbor/Docker/model/PyPI/Sigstore all untouched).
- Backward-compatible (optional field; legacy reduced bundles still verify).
- Machine-checkable via existing reproduction-bundle verifier and capture-manifest diff.
- Paper-faithful: makes Algorithm 1 `MERGEACCEPTED` machine-checkable across all round shapes.

Risks to acknowledge:
- The merged hash must be *deterministically derived* from audit lineage, not recomputed from raw harness snapshots. P108 explicitly keeps raw snapshots out of scope; P109 must preserve that boundary.
- Shape validators must reject `harness_after_merged_sha256` on single-commit/no-op rounds to keep field semantics closed.
- The capture-manifest diff fixture hashes will rotate because planned multi-commit stubs will now carry the new field; this is expected and aligned with the canonical hash rotation policy.
- Existing synthetic fixtures must continue to exercise both the multi-commit merged-hash path and the skipped path (for legacy reduced bundles).

## Required Changes

For the revised plan to be execution-ready, it must:

1. Add an optional `harness_after_merged_sha256` field to `proposal_validation_manifest/1.0` rounds. Shape validation:
   - Must be a 64-lowercase-hex digest when present.
   - Must be `null` or absent on no-op and single-commit rounds.
   - Must be non-null *only* when `len(committed_proposal_ids) >= 2`.
   - Must be paired with `harness_before_sha256`/`harness_after_sha256` already present (it is a per-round additional binding, not a replacement).

2. Extend `capture-extract` to stamp `harness_after_merged_sha256` from audit `lineage.json` when the round committed ≥2 candidates. The value must come from `lineage.json` `harness_after_hash` for that round, not from recomputing individual candidate changes.

3. Update `cross_artifact_proposal_validation_binding` harness-hash continuity loop so that:
   - Multi-commit rounds are no longer appended to `harness_continuity_skipped_rounds` when `harness_after_merged_sha256` is declared.
   - When a multi-commit round declares `harness_after_merged_sha256`, the next round's `harness_before_sha256` must equal it.
   - Missing-declared-as-present, mismatched, or extra (declared on single-commit) hashes fail closed.

4. Update `cross_artifact_proposal_validation_binding` split-outcome lineage loop (P107 path) symmetrically: multi-commit rounds with a declared merged hash use it for continuity; multi-commit rounds without it remain skipped for split-outcome continuity (since the merged split outcome is not derivable from a single candidate).

5. Update `_proposal_validation_round_summary` in `capture_manifest_diff.py` to compare planned vs. realized `harness_after_merged_sha256` presence and value for multi-commit rounds; add `multi_commit_merged_hash_violation_count` to diff metadata.

6. Update `capture_manifest_build.py` planned stubs so multi-commit-shaped rounds (need at least one such fixture) carry a deterministic `harness_after_merged_sha256`.

7. Rotate only the paper-faithful reproduction-readiness, capture-manifest, capture-manifest diff, and release-candidate evidence fixture hashes. The canonical audit hash and canonical LLM audit hash must not change because the engine default output path is unchanged.

8. Add tests covering: (a) valid merged-hash continuity across multi-commit rounds; (b) rejection when the next round's `harness_before_sha256` disagrees with the prior merged hash; (c) rejection when `harness_after_merged_sha256` appears on a single-commit round; (d) rejection when a multi-commit round omits it; (e) legacy reduced bundles without the field still verify with the multi-commit round reported as skipped.

9. Update `docs/operations/benchmark_reproduction_readiness.md` so the `proposal_validation_manifest` row notes that Algorithm 1 `MERGEACCEPTED` continuity is now machine-checkable for multi-commit rounds via the optional merged-hash field.

## Revised Plan

**P109 — Multi-commit MERGEACCEPTED harness-state hash continuity**

Files:
- `src/self_harness/_artifact_shapes.py` — add `harness_after_merged_sha256` to `_PROPOSAL_VALIDATION_ROUND_FIELDS`; extend `_proposal_validation_manifest` shape validator with closed-field semantics.
- `src/self_harness/reproduction_bundle.py` — extend `_cross_artifact_proposal_validation_binding` to enforce merged-hash continuity on multi-commit rounds in both harness-hash and split-outcome lineage paths.
- `src/self_harness/capture_manifest_diff.py` — extend `_proposal_validation_round_summary` and `_proposal_validation_findings` with merged-hash presence/value diffing.
- `src/self_harness/capture_extract.py` — derive `harness_after_merged_sha256` from audit `lineage.json` for multi-commit rounds; fail closed on malformed lineage material.
- `src/self_harness/capture_manifest_build.py` — planned stubs for multi-commit rounds now carry the merged-hash field.
- `tests/` — new tests covering continuity pass, four rejection modes, and reduced-bundle compatibility.
- `docs/architecture/schema_changelog.md` — P109 entry under `Proposal Validation Manifest 1.0`.
- `docs/operations/benchmark_reproduction_readiness.md` — update `proposal_validation_manifest` required-shape notes.

Tests:
- New `tests/reproduction/test_proposal_validation_merged_hash.py` (or analogous) with the five required scenarios.
- Extension of `tests/release_candidate/` fixture inputs and expected hashes.
- Extension of `tests/capture/test_capture_manifest_diff_*` to compare planned-vs-realized merged-hash disclosure.

Docs:
- `docs/architecture/schema_changelog.md` — P109 entry.
- `docs/operations/benchmark_reproduction_readiness.md` — reflect closed gap.
- `docs/architecture/productionization_brief.md` — append P109 implementation summary.

Non-goals:
- Recomputing merged hashes from raw harness snapshots or raw patches.
- Storing raw patches in reproduction bundles.
- Per-task candidate-vs-baseline acceptance rule.
- Cross-validation-vs-final-evaluation pass-count binding.
- Bumping the primary audit schema version.
- Contacting Harbor/Docker/model/PyPI/Sigstore/registry/scanner/cloud services.
- Introducing a benchmark reproduction claim.

Stop conditions:
- All required tests pass on Python 3.11/3.12/3.13.
- `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, and `make reproduction-readiness-check` pass with rotated paper-faithful fixture hashes.
- Canonical `tests/fixtures/canonical_audit_hash.txt` and `tests/fixtures/canonical_llm_audit_hash.txt` unchanged.
- `reproduction_claimed=false` preserved everywhere; `live_execution_blocked` unaffected.

## Remaining Open Questions

None blocking. Two advisory notes:

1. **Composition semantics for future raw-patch bundles.** If a later slice stores raw patch material, the merged-hash composition rule may need to be re-derived from patches rather than from `lineage.json`. P109 explicitly keeps that out of scope; the lineage-derived hash is the paper-faithful boundary for now.

2. **Whether P110 should revisit P107 split-outcome continuity for multi-commit rounds.** P109 closes the harness-hash path; the split-outcome path remains skipped because the merged split outcome is not derivable from individual candidate rows without recomputation. That is a separate, larger decision and should not gate P109.

[usage] {"completion_tokens": 3700, "completion_tokens_details": {"reasoning_tokens": 1856}, "prompt_tokens": 123327, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 127027}
