CONVERGED: YES

## Verdict

P106 — Baseline and candidate `evaluation_repeats` consistency within a proposal-validation round is the next precise, locally implementable paper-fidelity slice after P105. The paper Section 3.4 states: "we repeat candidate evaluation and apply the same rule to aggregate pass counts across repeats," implying that baseline and candidate harness evaluations within the same round use the same repeat count. Without this invariant, the P86 acceptance-rule comparison (`candidate_held_in_passed` vs `baseline_held_in_passed`) could compare a 2-repeat aggregate against a 1-repeat aggregate, accepting or rejecting candidates under inconsistent measurement protocols.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_proposal_validation_split_outcomes` validates `evaluation_repeats` as a positive integer for both baseline and candidate split outcomes but never compares the two within a round.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` extracts `baseline_held_in_passed`, `baseline_held_in_total`, etc. from `baseline_split_outcomes` but never reads `baseline_split_outcomes.evaluation_repeats`.
- Candidate `evaluation_repeats` is compared against the two-repeat evaluation report's `attempts_per_task` only when `evaluation_entry is not None`; when the two-repeat evaluation artifact is absent from a reduced bundle, candidate and baseline repeats are never cross-checked.
- The acceptance-rule enforcement at lines recording `acceptance_rule_violations` compares raw aggregate counts without confirming they share the same repeat denominator.
- The class-shaped fixtures in `tests/test_reproduction_readiness.py::_proposal_validation_split_outcomes` always emit `evaluation_repeats: 2` for both baseline and candidates, so no canonical readiness hash, reproduction-readiness fixture hash, or release-candidate evidence hash will rotate.

Inference (architecture decisions, labeled as inference):
- **Enforcement layer:** Consistency belongs in `_proposal_validation_manifest` shape validation (within-round cross-row check) so a malformed standalone artifact is rejected before bundle verification, and in `_cross_artifact_proposal_validation_binding` (defense in depth) so the structural violation is still caught if shape validation is bypassed.
- **Scope:** Within-round only. Cross-round repeat consistency is not paper-required (different rounds may theoretically use different repeat counts if the protocol evolves, though the fixed protocol pins `attempts_per_task: 2`).
- **Error surface:** Report first mismatched candidate with round index, proposal id, baseline repeats, and candidate repeats.
- **Hash rotation:** None. All existing fixtures use uniform `evaluation_repeats: 2`.

## Required Changes

None blocking. Decisions resolved:
1. In `_proposal_validation_manifest` shape validation, after parsing each round's baseline and candidate split outcomes, verify every candidate's `evaluation_repeats` equals the round's `baseline_split_outcomes.evaluation_repeats`. Reject the first mismatch with a descriptive error.
2. In `_cross_artifact_proposal_validation_binding`, extract `baseline_evaluation_repeats` from each round's baseline and compare against each candidate's `evaluation_repeats`. Record `evaluation_repeats_mismatch_violations` metadata and fail the check on mismatch.
3. No `capture_manifest_diff.py` change is required because this is a within-artifact structural invariant, not a plan-vs-realized drift signal. The existing `evaluation_repeat_drift` metadata (which compares candidate repeats to the two-repeat evaluation report) remains unchanged.

## Revised Plan

**P106 — Baseline and candidate evaluation_repeats consistency within a proposal-validation round (paper Section 3.4)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):

- `src/self_harness/_artifact_shapes.py::_proposal_validation_manifest`:
  - In the per-round validation loop, after extracting `baseline_split_outcomes` and validating each candidate, read `baseline_split_outcomes.evaluation_repeats`.
  - For each candidate, compare `candidate.split_outcomes.evaluation_repeats` to the baseline value.
  - On first mismatch, return: `f"{row_label}.candidates[{candidate_index}].split_outcomes.evaluation_repeats must match baseline_split_outcomes.evaluation_repeats ({baseline_repeats})"`.
  - Comment references Section 3.4's aggregate pass-count comparison requirement.

- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding`:
  - For each round, extract `baseline_evaluation_repeats` from `baseline_split_outcomes`.
  - For each candidate, extract `candidate_evaluation_repeats` from `split_outcomes`.
  - When they differ, append to `evaluation_repeats_mismatch_violations`: `{"round_index": ..., "proposal_id": ..., "baseline_evaluation_repeats": ..., "candidate_evaluation_repeats": ...}`.
  - Add `evaluation_repeats_mismatch_violations` to `metadata` and add a failure message when non-empty.
  - Add check: `if evaluation_repeats_mismatch_violations: failures.append("proposal validation candidate evaluation_repeats must match baseline evaluation_repeats within each round")`.

Tests:

- `tests/test_reproduction_readiness.py`:
  - Add `test_reproduction_bundle_rejects_candidate_evaluation_repeats_mismatch`: load the class-shaped `proposal_validation_manifest`, set `rounds[0].candidates[0].split_outcomes.evaluation_repeats` to `1` (baseline remains `2`), and assert `verify_reproduction_bundle` fails at `artifact_proposal_validation_manifest` with "evaluation_repeats must match baseline" in the detail.
  - Add `test_reproduction_bundle_records_candidate_evaluation_repeats_mismatch_cross_artifact`: construct a shape-valid manifest where candidate repeats differ from baseline (monkeypatch `artifact_shape_error` to bypass shape validation for this artifact class), then assert `cross_artifact_proposal_validation_binding` records `evaluation_repeats_mismatch_violations` with both repeat values.
  - Add `test_reproduction_bundle_accepts_uniform_evaluation_repeats`: extend a candidate with distinct split counts but same `evaluation_repeats: 2` and confirm bundle verification still passes.

Docs:

- `docs/architecture/schema_changelog.md`: add P106 entry under proposal-validation manifest notes, stating this is a behavioral tightening of `_proposal_validation_manifest` and `_cross_artifact_proposal_validation_binding`, not a schema bump; reference Section 3.4 aggregate pass-count validation.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposal_validation_records.notes` to mention that candidate `evaluation_repeats` must match baseline within each round.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposal_validation_manifest` row description to note the evaluation-repeats consistency invariant.
- `docs/architecture/productionization_brief.md`: append P106 entry in established style.

Non-goals:
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No cross-round evaluation_repeats consistency enforcement (the fixed protocol pins repeats at the protocol level; per-round variation within a single bundle is a shape concern only within each round).
- No enforcement that `evaluation_repeats` equals the fixed protocol's value (that is already covered by the existing `evaluation_repeat_drift` check against the two-repeat evaluation report when present).
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged.
- Schema changelog entry present and references Section 3.4 aggregate pass-count validation.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether `evaluation_repeats` should be hoisted from per-split-outcome to per-round or per-manifest level to reduce redundancy — defer; the current per-split-outcome placement is compatible with the existing P85/P91 two-repeat binding and avoids a schema bump.
2. Whether the existing `evaluation_repeat_drift` check (candidate vs two-repeat evaluation report) should also cover the baseline — defer; the baseline is not a candidate and does not have a direct counterpart in the two-repeat evaluation report, so adding it would require defining new semantics.
3. Whether stochastic evaluation repeat counts should be disclosed per-task rather than as a uniform round-level value — defer; the paper assumes uniform repeats and the fixed protocol encodes a single `attempts_per_task` value.
