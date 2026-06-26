# Round 1

CONVERGED: NO

## Verdict

Initial candidate plan (P108: Proposal-validation harness-state hash continuity binding) is a legitimate, locally implementable, machine-checkable evidence-contract gap that improves paper Algorithm 1 fidelity. However, as the architect reviewer I cannot converge in round 1 because (a) no candidate plan was supplied to critique, so this round must establish the plan, and (b) one scoping decision is genuinely open: whether harness-hash binding belongs inside `proposal_validation_manifest` (derived artifact) or as a new cross-artifact invariant over a bundled `lineage.json`-shaped artifact. I therefore return a revised plan for round 2 review rather than marking the loop converged.

## Critique

Evidence (from repo):
- `productionization_brief.md` P107 currently checks proposal-validation baselines follow prior committed *split-outcome* state, but does not bind the *harness state hashes* (`harness_before_hash`, `harness_after_hash` in `lineage.json`) into reproduction-bundle evidence.
- Paper Algorithm 1 line 21 makes the harness the explicit object of the state transition; Section 5 states "harness improvement should be treated as an empirical state transition" and "a useful harness edit must specify ... the evaluation result that justifies promotion."
- `_artifact_shapes.py` `proposal_validation_manifest` round fields do not currently include harness hashes.
- `capture_extract.py` derives `proposal_validation_manifest` from audit run directories where `lineage.json` carries harness hashes.

Inference:
- There is a machine-checkable gap: a reproduction bundle can currently satisfy all cross-artifact invariants while the committed-candidate harness state is not provably continuous in hash space across rounds.
- This is paper-faithful (Algorithm 1 `MERGEACCEPTED` produces `h_{t+1}` from `h_t`), locally implementable (offline hash recomputation from existing audit bytes), and non-cosmetic (closes a real evidence hole where split-outcome continuity is a weaker proxy than harness-state continuity).

Risks of the candidate slice:
1. Schema-bump temptation. Adding required harness hashes to `proposal_validation_manifest` would break reduced/non-paper bundles. Must be optional-nullable like P91 traffic hashes.
2. Derived-artifact scope creep. Harness hashes live in `lineage.json`; pulling them into `proposal_validation_manifest` duplicates state. Alternative is a new bundled `live_audit_lineage` artifact class. This is the open scoping decision.
3. Hash-stability dependency. Harness hashes must be deterministic over the audit byte layout; the existing canonical-audit-hash pin already assumes this, so no new risk, but the invariant must reuse the same hashing helper.

## Required Changes

For round 2, the revised plan must resolve:
1. **Scoping decision**: emit `harness_before_sha256` / `harness_after_sha256` as optional nullable fields on `proposal_validation_manifest.rounds[]` (preferred, minimal) versus introducing a new required `live_audit_lineage` artifact class (heavier, cleaner separation). Recommend the optional-fields approach for consistency with P91 precedent and to avoid rotating all fixture hashes.
2. **Lineage continuity invariant definition**: for no-op rounds, `round[t+1].harness_before_sha256 == round[t].harness_before_sha256`; for single-commit rounds, `round[t+1].harness_before_sha256 == round[t].harness_after_sha256`; for multi-commit rounds, record in `lineage_continuity_skipped_rounds` (consistent with P107 split-outcome handling).
3. **Capture extraction wiring**: `capture-extract --audit-run-dir` must stamp the two hashes from `lineage.json` when present; legacy extraction may omit them.
4. **Capture-manifest diff coverage**: `proposal-validation-derivation` must compare planned vs realized harness-hash presence counts so rehearsals catch loss of this evidence.
5. **Non-goals**: no new artifact class, no audit-schema bump, no raw patch inspection, no held-out pass-count comparison with post-commit evaluation, no live service contact, no reproduction-claim change.

## Revised Plan

**P108 — Proposal-validation harness-state hash continuity binding**

Files:
- `src/self_harness/_artifact_shapes.py`: extend `_PROPOSAL_VALIDATION_ROUND_FIELDS` with optional `harness_before_sha256` and `harness_after_sha256`; validate as 64-hex-or-null, present-together-when-declared.
- `src/self_harness/reproduction_bundle.py`: extend `_cross_artifact_proposal_validation_binding` with `cross_artifact_proposal_validation_harness_continuity` metadata; enforce lineage continuity for no-op and single-commit rounds; skip multi-commit rounds with explicit `harness_continuity_skipped_rounds`.
- `src/self_harness/capture_extract.py`: when `--audit-run-dir` is supplied and `lineage.json` carries per-round harness hashes, stamp the two fields on each validation round; fail closed on malformed hashes; legacy total-only audits continue to omit the fields.
- `src/self_harness/capture_manifest_diff.py`: add `harness_hash_presence_count` comparison to `proposal-validation-derivation`.
- `src/self_harness/capture_manifest_build.py`: planned proposal-validation stubs now disclose harness hashes derived deterministically from the planned run id.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposal_validation_records` notes with the new harness-continuity binding language.
- `docs/architecture/schema_changelog.md`: add P108 entry.

Tests:
- Happy path: bundled proposal-validation manifest with harness hashes verifies clean.
- No-op round continuity: round with no committed proposals keeps `harness_before == prior harness_before`.
- Single-commit continuity: `round[t+1].harness_before == round[t].harness_after`.
- Multi-commit skip: two committed candidates in one round produces a skip entry, not a failure.
- Hash drift: mismatched `harness_after_sha256` fails closed.
- Legacy compatibility: manifest omitting harness hashes still verifies (fields optional).
- Capture-extract stamps hashes from `lineage.json`; fails closed on malformed hashes.
- Capture-manifest diff catches planned-vs-realized harness-hash presence drift.

Docs:
- Schema changelog entry under "Proposal Validation Manifest" extension.
- `benchmark_reproduction_requirements.json` `proposal_validation_records` notes updated.
- Productionization brief P108 bullet.

Non-goals:
- No new artifact class.
- No audit/corpus schema version bump.
- No canonical readiness hash rotation (default audit writer unchanged).
- No raw patch or harness snapshot content inspection.
- No comparison of validation pass counts with post-commit two-repeat evaluation.
- No live Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud contact.
- No benchmark reproduction claim.

Stop conditions:
- All listed tests green; `make readiness` and `make release-candidate-evidence` unchanged; paper-fidelity canonical hash unchanged; release/operator fixture hashes rotate only for artifacts that newly carry harness-hash metadata.

## Remaining Open Questions

1. **Scoping confirmation (blocking round 2)**: confirm optional nullable fields on `proposal_validation_manifest.rounds[]` is preferred over a new `live_audit_lineage` artifact class. My recommendation is optional fields; the alternative would force every paper bundle to carry a new artifact and rotate every fixture hash.
2. **Hash source binding strength**: should the invariant recompute harness hashes from bundled `harness_before.json`/`harness_after.json` snapshots when a future artifact class exposes them, or trust the `lineage.json`-stamped hashes forever? Recommend: trust stamped hashes now, leave recomputation as future work tied to a potential raw-harness-snapshot artifact class.
3. **Multi-commit merge semantics**: P107 skips multi-commit split-outcome continuity because the merged harness state is not a single candidate row. The same logic applies to harness hashes. Confirm this skip-and-record behavior is acceptable rather than requiring a synthetic merged-state hash.

[usage] {"completion_tokens": 3788, "completion_tokens_details": {"reasoning_tokens": 2117}, "prompt_tokens": 151589, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 155377}

---

# Round 2

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

[usage] {"completion_tokens": 2171, "completion_tokens_details": {"reasoning_tokens": 601}, "prompt_tokens": 154958, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 157129}
