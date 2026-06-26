CONVERGED: YES

## Verdict
The suspected gap is real and bounded: `_cross_artifact_proposal_validation_binding` in `src/self_harness/reproduction_bundle.py` already enforces `evaluation_repeats` against the two-repeat evaluation report and validates internal monotonicity (`held_in_passed ≤ held_in_total`), but it does NOT bind `held_in_total`/`held_out_total` to the canonical live split manifest. The defensible invariant is totals-only binding; binding pass counts to the live two-repeat evaluation would be **wrong** because baseline and per-candidate split outcomes describe different harness states (pre-edit and single-candidate-trial) than the cumulative post-commit state captured by the live evaluation report. The plan below is execution-ready and fits the existing offline-evidence-binding pattern without claiming reproduction.

## Critique
- **Evidence (from repo):** `_proposal_validation_split_outcomes` carries `held_in_passed`, `held_in_total`, `held_out_passed`, `held_out_total`, `evaluation_repeats`. The fixture in `tests/test_reproduction_readiness.py::_class_shaped_payloads` uses `held_in_total=32`, `held_out_total=32`, matching the split manifest's `held_in_count`/`held_out_count`, but this equivalence is only implicit.
- **Evidence:** `_cross_artifact_proposal_validation_binding` currently reads `split_entry` indirectly via `evaluation_entry` for `attempts_per_task` only; it does not consult the split manifest at all.
- **Inference:** A malicious or buggy proposal_validation_manifest could declare `held_in_total=10` while bundling a 32/32 split manifest and a 64-task two-repeat evaluation report, and the current verifier would not flag it. This is a paper-fidelity hole because the Self-Harness protocol always evaluates candidates against the same fixed split.
- **Inference:** Binding pass counts (e.g., `baseline_split_outcomes.held_in_passed == two_repeat.held_in_passed`) is **indefensible**. The two-repeat evaluation report captures the post-commit cumulative harness, while `baseline_split_outcomes` captures the pre-round-0 harness and per-candidate `split_outcomes` captures a single-candidate-trial harness. Equating them would fabricate equivalence that the paper does not claim.
- **Inference:** Binding candidate outcomes to raw per-candidate traces would require a new artifact class (e.g., `live_per_candidate_traces`) and a paper section justifying it. That is out of scope for this slice.
- **Architecture risk:** Adding split-total binding is purely additive and offline; no schema bumps, no readiness hash rotation, no reproduction-claim change. Risk is low.
- **Architecture risk:** If future P86+ wants per-candidate trace binding, this slice's "totals-only" decision must be documented as deliberate, not as an oversight, to prevent drift.

## Required Changes
1. Extend `_cross_artifact_proposal_validation_binding` to require the `live_terminal_bench_split_manifest` entry be present whenever `proposal_validation_manifest` is present (it already is in paper bundles, but make the dependency explicit).
2. Add a sub-check `cross_artifact_proposal_validation_split_totals` (or extend the existing check's failure list and metadata) that, for every `round.baseline_split_outcomes` and every `round.candidates[*].split_outcomes`:
   - requires `held_in_total == split_manifest.held_in_count`
   - requires `held_out_total == split_manifest.held_out_count`
   - records offending rounds/candidates in metadata
3. Do NOT add any pass-count comparison against `live_two_repeat_evaluation_report`.
4. Add explicit boundary language in the check detail and any new metadata field: "binds proposal validation split totals to the canonical live split only; baseline and per-candidate pass counts are independent harness-state observations and are not equivalent to the post-commit two-repeat evaluation."
5. Add tests for: (a) happy path still passes, (b) baseline `held_in_total` drift fails, (c) candidate `held_out_total` drift fails, (d) missing split manifest fails closed, (e) partial coverage when only some candidates drift.
6. Update `docs/architecture/productionization_brief.md` with a P85 entry describing the totals-only invariant and the explicit out-of-scope items.
7. Regenerate the release-candidate evidence fixture hash if it rotates; explicitly assert in CI that the canonical paper-fidelity audit hash does NOT rotate.

## Revised Plan
**P85 — proposal_validation_manifest split-total binding**

Files:
- `src/self_harness/reproduction_bundle.py` — extend `_cross_artifact_proposal_validation_binding`:
  - Require `split_entry` is present when `validation_entry` is present; fail with `cross_artifact_proposal_validation_binding` and reason "live Terminal-Bench split manifest artifact is missing" if absent.
  - After loading `split`, extract `held_in_count` and `held_out_count`.
  - For each `round` in `validation.rounds`, compare `round.baseline_split_outcomes.held_in_total` and `.held_out_total` against the split counts; collect violations.
  - For each `candidate` in `round.candidates`, compare `candidate.split_outcomes.held_in_total` and `.held_out_total` against the split counts; collect violations.
  - Emit metadata `split_manifest_held_in_count`, `split_manifest_held_out_count`, `baseline_total_violations`, `candidate_total_violations`.
  - On any violation, append the failure "proposal validation split totals must match the canonical live split manifest" to `failures`.
  - Add a one-line invariant docstring noting totals-only semantics.
- `src/self_harness/_artifact_shapes.py` — no change; totals are already validated as non-negative integers and `held_in_passed ≤ held_in_total` is already enforced.
- `tests/test_reproduction_readiness.py` (or a dedicated `tests/test_reproduction_bundle_proposal_validation.py` if preferred):
  - `test_reproduction_bundle_binds_proposal_validation_split_totals_to_canonical_split` — happy path, asserts check status `pass` and metadata fields.
  - `test_reproduction_bundle_rejects_baseline_held_in_total_drift` — rewrite `baseline_split_outcomes.held_in_total` to 10; assert check fails with split-totals failure and metadata `baseline_total_violations` non-empty.
  - `test_reproduction_bundle_rejects_candidate_held_out_total_drift` — rewrite one candidate's `held_out_total`; assert check fails with `candidate_total_violations` non-empty.
  - `test_reproduction_bundle_rejects_proposal_validation_when_split_manifest_missing` — exclude split manifest; assert fail-closed.
  - `test_reproduction_bundle_does_not_bind_proposal_validation_pass_counts_to_two_repeat_evaluation` — explicit negative test: set `baseline_split_outcomes.held_in_passed` to a value that differs from the two-repeat evaluation's held-in pass count and assert the check STILL passes (guards against future over-binding regressions).
- `docs/architecture/productionization_brief.md` — append P85 section using the same template as P84, listing:
  - Invariant: proposal validation baseline and per-candidate split totals bind to the canonical live split manifest's held-in/held-out counts.
  - Non-invariant (deliberate): pass counts are NOT bound to the post-commit two-repeat evaluation because they describe different harness states.
  - Out-of-scope: per-candidate raw-trace binding (would require a new artifact class and paper justification); baseline evaluation report as a separate artifact.
- `Makefile` / CI — no new target needed; existing `make readiness`, `make release-candidate-evidence`, and bundle-verify tests cover the change. If the release-candidate fixture hash rotates, update `tests/fixtures/release_candidate/release_candidate_evidence_result.json` and document the rotation in the P85 entry.

Invariants:
- `proposal_validation_manifest.rounds[*].baseline_split_outcomes.held_in_total == live_terminal_bench_split_manifest.held_in_count`
- `proposal_validation_manifest.rounds[*].baseline_split_outcomes.held_out_total == live_terminal_bench_split_manifest.held_out_count`
- `proposal_validation_manifest.rounds[*].candidates[*].split_outcomes.held_in_total == live_terminal_bench_split_manifest.held_in_count`
- `proposal_validation_manifest.rounds[*].candidates[*].split_outcomes.held_out_total == live_terminal_bench_split_manifest.held_out_count`
- `evaluation_repeats == live_two_repeat_evaluation_report.attempts_per_task == 2` (already enforced)

Explicitly out-of-scope:
- Binding `held_in_passed` / `held_out_passed` in proposal_validation_manifest to any live artifact (different harness states).
- Adding a `live_baseline_two_repeat_evaluation_report` artifact class.
- Adding a `live_per_candidate_traces` artifact class.
- Per-task candidate outcome disclosure.
- Schema version bumps for `proposal_validation_manifest`, split manifest, or two-repeat evaluation report.
- Rotating the canonical paper-fidelity audit hash.
- Any live Harbor/Docker/model/PyPI/Sigstore contact.
- Any change to `reproduction_claimed` semantics.

## Remaining Open Questions
- Should P86 introduce a separate `live_baseline_two_repeat_evaluation_report` artifact so that `baseline_split_outcomes.held_in_passed` can be bound to a captured pre-edit harness evaluation? **Non-blocking for P85**; flagged as a candidate future slice. Decide after reviewing whether operators can cheaply capture a baseline evaluation before the first proposer round in the live capture pipeline.
- Should per-candidate trace binding be paper-justified under Section 4.1 or deferred indefinitely? **Non-blocking**; requires reading the paper's algorithm box for Self-Harness candidate validation, which is not in the provided repo context. P85's totals-only decision is correct regardless.
- If the release-candidate evidence fixture hash rotates, is an additional changelog note required beyond the P85 brief entry? **Non-blocking**; follow the existing P46/P57 precedent of noting rotations inline.
