# Round 1

CONVERGED: YES

## Verdict
The observed gap is real, bounded, and consistent with the P73–P77 lineage of additive `capture_manifest_diff` findings. P87 introduced a machine-checkable invalid-candidate category into `proposal_validation_manifest`, but the plan-vs-realized rehearsal in `capture_manifest_diff._proposal_validation_round_summary()` collapses every candidate to only `candidate_count`, `committed_count`, and `decision_counts`. A rehearsal could therefore pass while the realized bundle drifts on `validation_failure_category` distribution or on the empty-vs-non-empty `changed_surfaces` shape that P87 made semantically meaningful. This is the right P88 slice: it is purely additive, it closes the P87 loop without introducing a new artifact class or schema bump, and it requires no live evidence.

## Critique
- **Evidence (repo):** `_proposal_validation_round_summary()` in `src/self_harness/capture_manifest_diff.py` only reads `audit_decision` per candidate; it ignores `validation_failure_category` and `changed_surfaces`. `_proposal_validation_findings()` only emits drift on `candidate_count`, `committed_count`, and `decision_counts`.
- **Evidence:** P87's `_planned_artifact_stub()` in `capture_manifest_build.py` emits one `no_editable_surface` invalid candidate per round with `changed_surfaces: []`. The bundle-side fixture in `tests/test_reproduction_readiness.py::_proposal_validation_candidate()` emits an invalid `no_editable_surface` candidate only in round 1. So planned-vs-realized already differs in invalid-candidate placement, but the current diff cannot detect that — exactly the drift the rehearsal should catch.
- **Evidence:** `_proposal_validation_candidate()` in `_artifact_shapes.py` already enforces the closed category enum and the empty-`changed_surfaces`-for-`no_editable_surface` rule, so the diff extension only needs to *compare* per-round aggregates, not re-validate them.
- **Inference:** The P88 extension should mirror the P73/P74/P83 style: extend `_proposal_validation_round_summary()` with two new aggregates and let `_proposal_validation_findings()` emit drift when they differ. No new finding *category* is required; the existing `proposal-validation-derivation` finding already encompasses "realized validation structure" drift.
- **Inference:** The two new aggregates are (a) `validation_failure_category_counts` keyed by the closed enum (`no_editable_surface`, `execution_failure`, plus `none` for non-invalid candidates to keep counts total-equal), and (b) `changed_surfaces_empty_count` (candidates with `changed_surfaces == []`). Both are deterministic, paper-derived, and trivially recomputable from the existing per-candidate fields.
- **Architecture risk:** Low. No schema bump, no readiness hash rotation, no new artifact class, no new live dependency. Capture-rehearsal fixture hash and any release-candidate-evidence fixture hash that consumes rehearsal output may rotate; the canonical paper-fidelity audit hash is untouched because engine default audit output is unchanged.

## Required Changes
1. Extend `_proposal_validation_round_summary()` in `src/self_harness/capture_manifest_diff.py` to additionally compute, per round:
   - `validation_failure_category_counts`: dict mapping each of `{"no_editable_surface", "execution_failure", "none"}` to the number of candidates with that category (where `none` counts candidates with `validation_failure_category is None`).
   - `changed_surfaces_empty_count`: number of candidates whose `changed_surfaces` list is empty.
2. Extend `_proposal_validation_findings()` so that, for each round present on both sides, the drift record also reports differences in `validation_failure_category_counts` and `changed_surfaces_empty_count`, and so that any such difference contributes to the `round_violations` failure set with the same "realized proposal validation candidate and decision counts must match planned shape" message (broadened to "...candidate, decision, and validation-failure-category counts...").
3. Update the planned stub in `capture_manifest_build._planned_artifact_stub()` is *not* required to change; the existing P87 invalid-candidate stub is already sufficient planned evidence.
4. Update `tests/test_reproduction_readiness.py::_proposal_validation_rounds()` so the fixture realizes an invalid `no_editable_surface` candidate in **every** round (not just round 1), matching the planned stub's per-round placement. This keeps the rehearsal happy path green after P88 tightens the diff.
5. Add `tests/test_capture_manifest.py` cases that:
   - Mutate the realized bundle's round-0 invalid candidate from `no_editable_surface` to `execution_failure` (with non-empty `changed_surfaces`) and assert `proposal-validation-derivation` fails with `validation_failure_category_counts` drift in metadata.
   - Mutate the realized bundle's invalid candidate to keep its category but add a non-empty `changed_surfaces` and assert the diff fails on `changed_surfaces_empty_count` drift. (Note: shape validation in `_artifact_shapes.py` already rejects `no_editable_surface` with non-empty surfaces, so this test should instead mutate the planned side, or use a realized candidate with category `none` and empty surfaces — but that is also shape-invalid. The clean shape-preserving mutation is: change a realized *accepted* candidate's `changed_surfaces` to a different non-empty value; that does not affect counts. Therefore the `changed_surfaces_empty_count` drift is only observable by mutating the planned stub. Add a test that builds a planned manifest with an extra empty-surface invalid candidate and asserts the realized bundle diff reports `changed_surfaces_empty_count` drift.)
6. Documentation: append a P88 entry to `docs/architecture/productionization_brief.md` using the P84–P87 template, citing paper Section 3.4's two invalid-proposal causes and explicitly noting that P88 only tightens *planned-vs-realized* rehearsal coverage; it does not change any schema, validator, or default release path.
7. Stop conditions / explicit non-goals:
   - No new finding category in `capture_manifest_diff`.
   - No `capture_manifest_diff` schema version bump.
   - No new artifact class, no audit schema bump, no `proposal_validation_manifest.schema_version` bump.
   - No semantic parsing of `rejection_reason` beyond P87's deterministic rule.
   - No readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, no reproduction-claim change.

## Revised Plan
**P88 — capture-manifest proposal-validation category and changed-surfaces diffing**

Files:
- `src/self_harness/capture_manifest_diff.py`
  - In `_proposal_validation_round_summary()`: for each candidate read `validation_failure_category` (defaulting to `"none"` when `None`) and the length of `changed_surfaces`; accumulate `validation_failure_category_counts` (initialized to all three keys at 0) and `changed_surfaces_empty_count`; append both to the per-round summary dict.
  - In `_proposal_validation_findings()`: extend the per-round drift dict's compared keys to include `validation_failure_category_counts` and `changed_surfaces_empty_count`; broaden the failure message to "realized proposal validation candidate, decision, and validation-failure-category counts must match planned shape".
- `tests/test_reproduction_readiness.py`
  - In `_proposal_validation_rounds()`: change `second_candidate_decision` so every round emits an invalid `no_editable_surface` candidate for `candidate_index=1`, mirroring the planned stub. Keep acceptance-rule invariants intact (the invalid candidate must not appear in `committed_proposal_ids`).
- `tests/test_capture_manifest.py`
  - Add `test_capture_manifest_diff_reports_validation_failure_category_drift`: realize a bundle where round 0's invalid candidate has `validation_failure_category="execution_failure"` and non-empty `changed_surfaces`; assert `proposal-validation-derivation` fails and that `round_violations[0]` includes `validation_failure_category_counts`.
  - Add `test_capture_manifest_diff_reports_changed_surfaces_empty_count_drift`: author a planned manifest stub that adds a second empty-surface invalid candidate to round 0, diff against the standard realized bundle, and assert `round_violations[0]` includes `changed_surfaces_empty_count`.
- `docs/architecture/productionization_brief.md`
  - Append P88 entry using the P84–P87 template.

Invariants enforced after P88:
- For each planned/realized round pair with matching `round_index`: `validation_failure_category_counts` must be equal and `changed_surfaces_empty_count` must be equal.
- Existing `candidate_count`, `committed_count`, and `decision_counts` comparisons remain unchanged.
- Existing `proposal-validation-derivation` skip rules (planned artifact absent, realized artifact absent) remain unchanged.

## Remaining Open Questions
- Should the diff eventually also compare per-candidate `edited_surface_sha256` / `targeted_mechanism_sha256` aggregates? **Non-blocking**: those are already bound by `cross_artifact_proposal_validation_binding` on the bundle side and by `cross_artifact_proposer_previous_edits_binding`. P88 stays scoped to the closed P87 category surface.
- Should `_proposal_validation_round_summary()` eventually expose a per-round `invalid_count` convenience field in addition to the closed category breakdown? **Non-blocking**: `decision_counts["invalid"]` already provides it and is already compared.
- Should the rehearsal report (`scripts/capture_rehearsal.py`) surface the new aggregates as top-level stage metadata? **Non-blocking**: the diff report already carries them in finding metadata; rehearsal-stage promotion is a presentation-only follow-up.

[usage] {"completion_tokens": 2674, "completion_tokens_details": {"reasoning_tokens": 640}, "prompt_tokens": 119885, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 122559}
