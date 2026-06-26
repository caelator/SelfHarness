# Round 1

CONVERGED: YES

## Verdict
The next gap is real, bounded, and enforceable purely from existing `proposal_validation_manifest` contents without new artifacts, raw traces, or live reproduction. The paper Section 3.4 acceptance rule (candidate improves at least one split and degrades neither split, using aggregate pass counts across repeats) is a *cross-field invariant within a single proposal-validation round*: it compares each accepted/merged candidate's `split_outcomes` against the round's `baseline_split_outcomes`. No external artifact is required. The plan below is execution-ready and fits the established offline-evidence-binding pattern.

## Critique
- **Evidence (repo):** `_proposal_validation_candidate` in `_artifact_shapes.py` already enforces closed `audit_decision` enum, non-empty `decision_reason`, non-empty `rejection_reason` for rejected/superseded/invalid candidates, and per-split monotonicity (`held_in_passed ≤ held_in_total`).
- **Evidence:** `_cross_artifact_proposal_validation_binding` in `reproduction_bundle.py` already binds round count, candidate count, committed-id set, fixed-protocol hash, split totals, evaluation repeats, and previous-edit hashes. It does *not* evaluate the acceptance rule against baseline pass counts.
- **Inference:** A malicious or buggy `proposal_validation_manifest` could declare a candidate `accepted` while its `held_in_passed` is strictly less than baseline's, and nothing currently flags it. This is a paper-fidelity hole: the Self-Harness protocol only commits candidates that are pareto-improvements over the round baseline.
- **Inference:** Binding candidate pass counts to the post-commit `live_two_repeat_evaluation_report` remains **wrong** (P85 decision): different harness states. The acceptance rule is *baseline-vs-candidate within the same round*, not *candidate-vs-final-evaluation*.
- **Inference:** "Invalid candidates fail due to execution or no changed surface" is partially enforceable: `changed_surfaces` is recorded. The cleanest enforceable form is "invalid candidates must carry a non-empty `rejection_reason`" (already enforced) plus the positive acceptance-rule check for accepted/merged. Semantic parsing of execution-failure text is out of scope.
- **Architecture risk:** Low. Purely additive check, no schema bumps, no readiness hash rotation, no reproduction-claim change. Single new failure bucket inside the existing `cross_artifact_proposal_validation_binding` check.

## Required Changes
1. Extend `_cross_artifact_proposal_validation_binding` to evaluate, for every candidate with `audit_decision in {"accepted", "merged"}`:
   - `candidate.split_outcomes.held_in_passed >= round.baseline_split_outcomes.held_in_passed`
   - `candidate.split_outcomes.held_out_passed >= round.baseline_split_outcomes.held_out_passed`
   - at least one of the above is strict `>` (improves at least one split)
2. Record violations in new metadata fields `acceptance_rule_violations` (per-round, per-candidate, with baseline vs candidate pass counts and which side regressed or failed to improve).
3. Add boundary language: "acceptance rule is evaluated per-round against the round's baseline split outcomes, not against the post-commit two-repeat evaluation; baseline and candidate describe the same pre-commit harness state observed under different edits".
4. Do NOT add any comparison between candidate pass counts and `live_two_repeat_evaluation_report` pass counts.
5. Tests: happy path still passes; accepted candidate regressing held-in fails; accepted candidate regressing held-out fails; accepted candidate with no improvement on either split fails; rejected/invalid/superseded candidates are exempt from the improvement requirement; merge_decision="rejected" rounds with no accepted candidates are exempt.
6. Brief entry in `docs/architecture/productionization_brief.md` describing the invariant and the explicit out-of-scope items.
7. If the release-candidate evidence fixture hash rotates, regenerate and note inline; assert canonical paper-fidelity audit hash does NOT rotate.

## Revised Plan
**P86 — proposal_validation_manifest acceptance-rule binding**

Files:
- `src/self_harness/reproduction_bundle.py` — extend `_cross_artifact_proposal_validation_binding`:
  - For each round, after reading `baseline_split_outcomes`, capture `baseline_held_in_passed` and `baseline_held_out_passed`.
  - For each candidate with `audit_decision in {"accepted", "merged"}`, read `candidate.split_outcomes.held_in_passed` and `.held_out_passed`.
  - Append a violation to `acceptance_rule_violations` when:
    - candidate held_in_passed < baseline held_in_passed ("held_in_regression"), or
    - candidate held_out_passed < baseline held_out_passed ("held_out_regression"), or
    - neither strict improvement ("no_improvement").
  - Add `acceptance_rule_violations` to metadata.
  - On any violation, append failure "accepted or merged candidates must improve at least one split and degrade neither split versus the round baseline" to `failures`.
  - Add one-line docstring noting per-round baseline-vs-candidate semantics.
- `src/self_harness/_artifact_shapes.py` — no change; pass-count fields already validated as non-negative integers with `held_in_passed ≤ held_in_total`.
- `tests/test_reproduction_readiness.py` — add cases:
  - `test_reproduction_bundle_binds_proposal_validation_acceptance_rule` — happy path, asserts check passes and `acceptance_rule_violations == []`.
  - `test_reproduction_bundle_rejects_accepted_candidate_held_in_regression` — rewrite an accepted candidate's `held_in_passed` below baseline; assert check fails with `held_in_regression`.
  - `test_reproduction_bundle_rejects_accepted_candidate_held_out_regression` — rewrite accepted candidate's `held_out_passed` below baseline; assert check fails with `held_out_regression`.
  - `test_reproduction_bundle_rejects_accepted_candidate_with_no_improvement` — set accepted candidate pass counts equal to baseline on both splits; assert check fails with `no_improvement`.
  - `test_reproduction_bundle_exempts_rejected_candidates_from_acceptance_rule` — mark both candidates rejected with `merge_decision="rejected"`; assert check still passes.
  - Negative guard: `test_reproduction_bundle_does_not_bind_proposal_validation_acceptance_to_two_repeat_evaluation` — already covered by existing P85 negative test; extend assertion to mention acceptance rule.
- `docs/architecture/productionization_brief.md` — append P86 entry using the P84/P85 template:
  - Invariant: every accepted or merged candidate must improve at least one split and degrade neither split versus its round's baseline split outcomes, using aggregate pass counts across `evaluation_repeats`.
  - Non-invariant (deliberate): candidate pass counts are NOT compared with the post-commit two-repeat evaluation; baseline and candidate are same-round same-harness-state observations under different edits.
  - Out-of-scope: per-task candidate outcome disclosure; semantic parsing of rejection_reason text; per-candidate raw trace binding; baseline evaluation as a separate artifact.
- `Makefile` / CI — no new target; existing `make readiness`, `make release-candidate-evidence`, and bundle-verify tests cover the change. Regenerate `tests/fixtures/release_candidate/release_candidate_evidence_result.json` if its hash rotates.

Invariants:
- For every `round` and every `candidate` with `audit_decision in {"accepted", "merged"}`:
  - `candidate.split_outcomes.held_in_passed >= round.baseline_split_outcomes.held_in_passed`
  - `candidate.split_outcomes.held_out_passed >= round.baseline_split_outcomes.held_out_passed`
  - `candidate.split_outcomes.held_in_passed > round.baseline_split_outcomes.held_in_passed OR candidate.split_outcomes.held_out_passed > round.baseline_split_outcomes.held_out_passed`
- Rejected, superseded, and invalid candidates are exempt.
- Rounds with `merge_decision == "rejected"` (no accepted/merged candidates) are exempt.

Explicitly out-of-scope:
- Comparing candidate pass counts to `live_two_repeat_evaluation_report` (different harness state; P85 decision).
- Requiring invalid candidates to have empty `changed_surfaces` (execution-failure path is already covered by non-empty `rejection_reason` enforced at shape validation).
- Semantic parsing of `decision_reason` / `rejection_reason` text.
- Per-task candidate outcome disclosure.
- New artifact classes (e.g., `live_baseline_two_repeat_evaluation_report`, `live_per_candidate_traces`).
- Schema version bumps for `proposal_validation_manifest`.
- Rotating the canonical paper-fidelity audit hash.
- Any live Harbor/Docker/model/PyPI/Sigstore contact.
- Any change to `reproduction_claimed` semantics.

## Remaining Open Questions
- Should P87 introduce a structured `failure_category` enum on proposal-validation candidates (e.g., `execution_error`, `no_changed_surface`, `acceptance_rule_violation`) so invalid-candidate reasons become machine-checkable instead of free text? **Non-blocking for P86**; flagged as a candidate future slice. Decide after confirming whether the paper's Section 3.4 taxonomy is closed or illustrative.
- Should the acceptance rule also be enforced at `_artifact_shapes.py` validation time (single-artifact check) in addition to the cross-artifact bundle check? **Non-blocking**; current placement in `_cross_artifact_proposal_validation_binding` is consistent with P84/P85 precedent of keeping cross-field invariants in the bundle verifier and structural per-field invariants in the shape validator. Revisit if a future consumer wants single-artifact validation to be self-contained.
- If the release-candidate evidence fixture hash rotates, is an additional changelog note required beyond the P86 brief entry? **Non-blocking**; follow the existing P46/P57/P85 precedent of noting rotations inline.

[usage] {"completion_tokens": 3319, "completion_tokens_details": {"reasoning_tokens": 1245}, "prompt_tokens": 84696, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 88015}
