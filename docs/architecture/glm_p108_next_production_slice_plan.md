CONVERGED: YES

## Verdict

Round 2 can converge P108 (Proposal-validation harness-state hash continuity binding). The three round-1 open questions all have repository-validated resolutions, the plan is locally implementable without new external dependencies, the slice closes a genuine paper-fidelity gap (Algorithm 1 `MERGEACCEPTED` produces `h_{t+1}` from `h_t` in harness hash space), and the proposed optional-nullable approach is the established pattern in this codebase. Plan is ready to execute.

## Critique

Evidence (from repo):
- `tests/test_capture_extract.py` confirms `lineage.json` carries per-round `harness_before_hash`/`harness_after_hash` (e.g., `"harness_before_hash": "0" * 64, "harness_after_hash": "1" * 64`), so the data source exists.
- `src/self_harness/_artifact_shapes.py` already uses optional-nullable paired fields on proposal-validation rounds for P91 (`proposer_round_request_sha256`/`proposer_round_response_sha256` present-together-or-absent pattern), confirming the optional-fields approach is the established pattern rather than a new artifact class.
- `src/self_harness/reproduction_bundle.py` already implements `lineage_continuity_violations` and `lineage_continuity_skipped_rounds` for P107 split-outcome continuity, so the harness-hash continuity invariant can reuse this exact structure with skip-and-record behavior for multi-commit rounds.
- `productionization_brief.md` P107 currently only verifies *split-outcome* continuity, not *harness-state* hash continuity, confirming this is a material evidence-contract gap.
- Paper Algorithm 1 line 21 and Section 5 Conclusion explicitly frame harness improvement as an empirical state transition, justifying the harness-hash binding at the artifact level.

Inference:
- Open question 1 (scoping): repository precedent decisively supports optional-nullable fields on `proposal_validation_manifest.rounds[]`. A new `live_audit_lineage` artifact class would force rotating every fixture hash and is not warranted.
- Open question 2 (hash source binding strength): the invariant should trust the lineage-stamped hashes now, matching the P91 precedent where validation traffic hashes are also stamped rather than recomputed. Future recomputation from raw harness snapshots is a separate, heavier artifact-class work and remains non-blocking.
- Open question 3 (multi-commit semantics): the P107 skip-and-record behavior should apply identically because the merged harness state is not a single candidate row in either the split-outcome or hash view.

Architecture risks:
1. Schema evolution pressure is contained: optional-nullable fields keep reduced/non-paper bundles valid.
2. Capture-extract fail-closed behavior must reject malformed hashes but preserve legacy total-only audits that omit the fields — matches existing capture-extract contract.
3. The capture-manifest diff must include a new `harness_hash_presence_count` comparison so rehearsals catch planned-vs-realized drift; this is an additive metadata field on the existing `proposal-validation-derivation` finding.

## Required Changes

Resolved decisions for execution:
1. **Scoping**: emit optional nullable `harness_before_sha256`/`harness_after_sha256` on `proposal_validation_manifest.rounds[]`. Validate as 64-hex-or-null, present-together-when-declared (mirrors P91 grammar). No new artifact class.
2. **Lineage continuity invariant**: for no-op rounds, `round[t+1].harness_before_sha256 == round[t].harness_before_sha256`; for single-commit rounds, `round[t+1].harness_before_sha256 == round[t].harness_after_sha256`; for multi-commit rounds, record in `harness_continuity_skipped_rounds`. Reuse the P107 structure inside `cross_artifact_proposal_validation_binding`.
3. **Hash source**: trust lineage-stamped hashes; do not recompute from bundled snapshots. Defer raw-snapshot recomputation to a future artifact-class slice.
4. **Capture-extract wiring**: `capture-extract --audit-run-dir` stamps both hashes from `lineage.json` per round when present; fails closed on malformed; legacy total-only audits continue to omit.
5. **Capture-manifest diff**: add `harness_hash_presence_count` to `proposal-validation-derivation` metadata for planned-vs-realized rehearsal coverage.

## Revised Plan

**P108 — Proposal-validation harness-state hash continuity binding**

Files:
- `src/self_harness/_artifact_shapes.py`: extend `_PROPOSAL_VALIDATION_ROUND_FIELDS` with optional paired `harness_before_sha256` and `harness_after_sha256`; validate 64-hex-or-null, present-together-when-declared.
- `src/self_harness/reproduction_bundle.py`: extend `_cross_artifact_proposal_validation_binding` to enforce harness-state continuity for no-op and single-commit rounds; skip multi-commit rounds into `harness_continuity_skipped_rounds`; record `harness_continuity_violations` metadata.
- `src/self_harness/capture_extract.py`: stamp both hashes from `lineage.json` per round when `--audit-run-dir` is supplied and lineage rows carry harness hashes; fail closed on malformed; legacy total-only audits omit the fields.
- `src/self_harness/capture_manifest_diff.py`: add `harness_hash_presence_count` comparison inside `proposal-validation-derivation`.
- `src/self_harness/capture_manifest_build.py`: planned `proposal_validation_manifest` stubs disclose harness hashes derived deterministically from the planned run id.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposal_validation_records` notes with the new harness-continuity binding language.
- `docs/architecture/schema_changelog.md`: add P108 entry under "Proposal Validation Manifest" extension.

Tests:
- Happy path: bundled proposal-validation manifest with harness hashes verifies clean.
- No-op round continuity: `harness_before == prior harness_before`.
- Single-commit continuity: `harness_before == prior harness_after`.
- Multi-commit skip: produces a skip entry, not a failure.
- Hash drift: mismatched `harness_after_sha256` fails closed.
- Legacy compatibility: manifest omitting harness hashes still verifies.
- Capture-extract stamps hashes from `lineage.json`; fails closed on malformed hashes.
- Capture-manifest diff catches planned-vs-realized harness-hash presence drift.

Docs:
- Schema changelog entry under "Proposal Validation Manifest" extension.
- `benchmark_reproduction_requirements.json` `proposal_validation_records` notes updated.
- `productionization_brief.md` P108 bullet.

Non-goals:
- No new artifact class.
- No audit/corpus schema version bump.
- No canonical readiness hash rotation (default audit writer unchanged).
- No raw patch or harness snapshot content inspection.
- No recomputation of harness hashes from raw harness snapshots.
- No comparison of validation pass counts with post-commit two-repeat evaluation.
- No live Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud contact.
- No benchmark reproduction claim.

Stop conditions:
- All listed tests green; `make readiness` and `make release-candidate-evidence` unchanged in default paths; paper-fidelity canonical hash unchanged; release/operator fixture hashes rotate only for artifacts that newly carry harness-hash metadata.

## Remaining Open Questions

None blocking. The recommended resolutions for the round-1 open questions are validated by the repository precedent (optional-nullable P91 fields, P107 skip-and-record structure, stamped-hash trust for derived evidence) and can proceed to execution.
