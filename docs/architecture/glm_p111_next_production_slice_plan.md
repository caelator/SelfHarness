CONVERGED: YES

## Verdict

The P110 blocker is resolved by the new engine evidence. The engine already writes independent `__merge__`/`candidate` evaluation rows when multi-commit merges are composed (`src/self_harness/engine.py` lines ~165-180), giving P111 an independent audit source analogous to P109's `lineage.json` harness hash. The slice is now defensible: `merged_split_outcomes` can be derived from an existing independent verifier observation rather than from next-round baseline tautology.

## Critique

**Evidence (validated against repo):**
- `engine.py`: when `accepted` is non-empty, builds `merge_group`, applies `merged_patch`, evaluates `merged_eval`, and writes `_evaluation_rows("__merge__", baseline, merged_eval, ...)`. These rows are written before the merge decision outcome is applied to `chosen_proposals`.
- For multi-commit rounds (`len(committed_proposal_ids) >= 2`), `merge_decision.accepted` must be True and `len(merge_group) >= 2`, which means `__merge__`/`candidate` rows are guaranteed to exist in the current engine.
- `capture_extract.py::_proposal_validation_round` already extracts `__baseline__`/baseline and per-candidate rows via `_split_outcomes`. Adding a `__merge__`/candidate extraction is a small, parallel addition.
- `reproduction_bundle.py::_cross_artifact_proposal_validation_binding` currently skips multi-commit lineage in split-outcome space (records `lineage_continuity_skipped_rounds`); the P109 harness-hash branch already demonstrates the closed form using `merged_harness_hashes_by_round`.
- `_artifact_shapes.py::_PROPOSAL_VALIDATION_ROUND_FIELDS` controls the closed round schema; adding `merged_split_outcomes` is an additive change that legacy manifests can omit.

**Inference:**
- The `__merge__`/`candidate` rows are a separate verifier observation of the merged harness state within the current round; next-round baseline is a separate observation at the start of next round. Both are valid P109-style "single audit-recorded observation of the merged state" along the split-outcome dimension.
- Toy/deterministic runners will produce identical values to next-round baseline; stochastic runners may diverge. Both are paper-faithful evidence.

**Risks addressed:**
- Tautology risk from P110 is gone: the source is an existing audit row, not next-round baseline stamped backwards.
- Legacy compatibility: shape validator keeps `merged_split_outcomes` optional; bundle verifier only closes the multi-commit skip when the field is present.
- Canonical audit hash risk: none — engine.py is unchanged; only derived artifact shape, extraction, and bundle-verification logic change.
- Reproduction-claim risk: none — all new code keeps `reproduction_claimed=false`.

## Required Changes

1. **Schema (`_artifact_shapes.py`)**:
   - Add `"merged_split_outcomes"` to `_PROPOSAL_VALIDATION_ROUND_FIELDS`.
   - In `_proposal_validation_manifest` round validation: if `merged_split_outcomes` is declared, validate with the same `_PROPOSAL_VALIDATION_SPLIT_OUTCOMES_FIELDS` shape. Allow it only when `harness_before_sha256`/`harness_after_sha256` are also declared and `len(committed_proposal_ids) >= 2`. Reject it on no-op or single-commit rounds. Legacy manifests omitting the field stay valid.

2. **Extraction (`capture_extract.py`)**:
   - In `_proposal_validation_round`: when `lineage_hashes is not None` and `len(committed_proposal_ids) >= 2`, also extract `_split_outcomes(round_, "__merge__", "candidate")` and attach as `merged_split_outcomes`. Fail closed if `__merge__`/`candidate` rows are absent in this case (audit corruption signal).

3. **Bundle verification (`reproduction_bundle.py::_cross_artifact_proposal_validation_binding`)**:
   - Track `merged_split_outcomes_by_round` alongside `merged_harness_hashes_by_round`.
   - In the split-outcome lineage-continuity loop: for multi-commit previous rounds, if `previous.merged_split_outcomes` is present, use it as `expected_outcomes` and compare to current baseline projection (replacing the current skip). Record `lineage_continuity_violations` on mismatch. If `merged_split_outcomes` is absent on a multi-commit previous round, keep the existing skip behavior and add the round to `lineage_continuity_skipped_rounds` with `reason: "missing_merged_split_outcomes"`.
   - Add `merged_split_outcome_lineage_continuity_closed_rounds` to metadata for observability.

4. **Capture-manifest diff (`capture_manifest_diff.py`)**:
   - In `_proposal_validation_round_summary`: record `merged_split_outcomes_present: bool` and a content digest (reuse `_optional_task_outcomes_digest` semantics).
   - In `_proposal_validation_findings`: compare planned vs realized `merged_split_outcomes_present` and digest for multi-commit rounds. Add to the drift failure detail when present.

5. **Docs**:
   - `docs/architecture/schema_changelog.md`: add P111 entry mirroring P109's structure, noting the dependency on existing `__merge__`/`candidate` engine rows.
   - `docs/operations/benchmark_reproduction_requirements.json`: extend the `proposal_validation_records` notes to mention `merged_split_outcomes` closes split-outcome lineage for multi-commit rounds when audit `__merge__` rows exist.

6. **Fixtures**:
   - Rotate capture-manifest build/rehearsal fixture hashes and release-candidate evidence hash because the planned validation shape now carries the new optional field on multi-commit rounds.
   - Do **not** rotate `tests/fixtures/canonical_audit_hash.txt` or `tests/fixtures/canonical_llm_audit_hash.txt` (engine output unchanged).

7. **Tests** (new):
   - Shape: multi-commit round with harness hashes requires `merged_split_outcomes`; no-op/single-commit round rejects it; legacy manifest without the field still validates.
   - Extraction: `_proposal_validation_round` populates `merged_split_outcomes` from `__merge__`/`candidate` audit rows when multi-commit; fails closed on missing rows for multi-commit; leaves field absent for single-commit/no-op.
   - Bundle verification: multi-commit split-outcome lineage continuity now closes when `merged_split_outcomes` is declared; mismatched merged outcomes produce `lineage_continuity_violations`; absence of the field on multi-commit preserves skip behavior.
   - Capture-manifest diff: planned-vs-realized `merged_split_outcomes` drift is detected.
   - Paper-fidelity invariants: canonical audit hash unchanged; no live contact; `reproduction_claimed:false` preserved.

## Revised Plan

**Slice P111 — Multi-commit split-outcome lineage continuity via existing `__merge__` audit evidence**

| Step | File | Change |
|---|---|---|
| Schema | `src/self_harness/_artifact_shapes.py` | Add `merged_split_outcomes` to round fields; shape-validate against split-outcome schema; restrict to multi-commit + harness-hash rounds. |
| Extraction | `src/self_harness/capture_extract.py` | In `_proposal_validation_round`, populate `merged_split_outcomes` from `__merge__`/`candidate` rows when multi-commit + lineage hashes present; fail closed if rows missing. |
| Bundle check | `src/self_harness/reproduction_bundle.py` | Close multi-commit split-outcome lineage continuity in `_cross_artifact_proposal_validation_binding` using declared `merged_split_outcomes`; preserve skip when absent. |
| Capture diff | `src/self_harness/capture_manifest_diff.py` | Compare planned vs realized `merged_split_outcomes` presence + digest for multi-commit rounds. |
| Docs | `docs/architecture/schema_changelog.md`, `docs/operations/benchmark_reproduction_requirements.json` | Add P111 entry; extend `proposal_validation_records` notes. |
| Fixtures | `tests/fixtures/capture_manifest/`, `tests/fixtures/release_candidate/`, capture-rehearsal fixtures | Rotate affected fixture hashes. |
| Tests | `tests/test_proposal_validation_*`, `tests/test_reproduction_bundle_*`, `tests/test_capture_manifest_diff_*`, `tests/test_capture_extract_*` | Cover shape, extraction, lineage closure, diff, invariants. |

**Stop conditions:**
- `make check`, `make readiness`, `make capture-manifest-check`, `make capture-rehearsal`, `make release-candidate-evidence` all pass.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/canonical_llm_audit_hash.txt` unchanged.
- Capture-manifest / rehearsal / release-candidate fixture hashes rotated only where their content changed.
- No new dependencies; no live Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud contact.
- All new code paths keep `reproduction_claimed=false` and the existing offline boundary language.
- New schema field is optional and additive; legacy reduced manifests remain valid.

## Remaining Open Questions

None blocking. The single open question from P110 (semantic/scope decision between internal-consistency-only vs independent cross-artifact binding) is now resolved by the engine evidence: the `__merge__`/`candidate` audit row is a genuine independent verifier observation written before the next round's baseline is computed, satisfying P109's standard of cross-artifact binding rather than tautological self-consistency.
