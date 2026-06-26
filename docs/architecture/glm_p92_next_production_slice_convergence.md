# Round 1

CONVERGED: NO

## Verdict

The most valuable next locally-implementable slice is **P92: proposer context intermediate-baseline task binding**. P81 currently binds `proposer_context_manifest` failing and passing task sets to the *final* `live_two_repeat_evaluation_report` (harness `h_T`). This violates the paper's Section 3.2 requirement that the evidence bundle `B_t` is derived from failures under the *current* harness `h_t` at round `t`. If the harness actually improves over time, the set of failing tasks shrinks, meaning P81 currently forces the proposer context to use stale or incorrect final outcomes. P92 closes this paper-fidelity gap by binding proposer context round `t` to `proposal_validation_manifest` round `t` `baseline_split_outcomes` `task_outcomes`. The initial plan needs three refinements before execution to ensure strict offline validation and backward compatibility.

## Critique

- **Evidence (repo):** `_cross_artifact_proposer_context_evidence_binding` in `reproduction_bundle.py` currently takes `evaluation_entry` (`live_two_repeat_evaluation_report`) and uses it to determine `eval_failing` and `eval_passing` for all rounds. This assumes the failing task set is static across all proposer rounds, which contradicts Algorithm 1 where `h_t` evolves.
- **Evidence (repo):** `proposal_validation_manifest` (introduced in P84) records `baseline_split_outcomes` for each round. P89 made `task_outcomes` optional on these split outcomes.
- **Inference (paper):** Section 3.2 states: "Weakness Mining... Starting from an initial harness, the agent with a fixed model is run on a set of tasks... The agent then clusters failed traces". The failures must belong to the baseline harness of that specific round.
- **Risk (medium, mitigated):** Promoting `task_outcomes` to required for baselines. If we enforce this strictly in the shape validator, it breaks older `1.0` manifests. We can mitigate this by enforcing the requirement inside the cross-artifact check only when `proposer_context_manifest` is present, or by gating it behind the reproduction bundle verification context.
- **Risk (low):** Updating the local fixtures to simulate iterative improvement. The current test fixtures use the same failing task across all rounds. They must be updated to show harness improvement (e.g., round 0 fails tasks A, B; round 1 fails task A; round 2 fails none).

## Required Changes

1. The `_cross_artifact_proposer_context_evidence_binding` check must drop its dependency on `evaluation_entry` and instead depend on `validation_entry` (`proposal_validation_manifest`).
2. For each `round_index` present in `proposer_context_manifest`, the corresponding round in `proposal_validation_manifest` must have `baseline_split_outcomes.task_outcomes`. If `task_outcomes` is missing, the check must fail closed.
3. The `eval_failing` and `eval_passing` sets must be computed per-round from the `baseline_task_outcomes` of that specific round.
4. The test fixture `_proposal_validation_rounds` must simulate iterative improvement by varying `held_in_failing_ids` across rounds, and `_proposer_context_rounds` must be updated to match these intermediate failing sets exactly.

## Revised Plan

**P92 — proposer context intermediate-baseline task binding**

Files:
- `src/self_harness/reproduction_bundle.py`
  - Modify `_cross_artifact_proposer_context_evidence_binding` signature to accept `validation_entry` instead of `evaluation_entry`.
  - Iterate over `validation_by_round` to extract `baseline_split_outcomes.task_outcomes`.
  - If `task_outcomes` is missing, return a fail check: "proposal validation baselines must disclose task outcomes when proposer context is bundled".
  - Compute `baseline_failing` and `baseline_passing` sets for each round from the `baseline_task_outcomes`.
  - Compare `proposer_context_manifest` failure/passing tasks against these per-round baseline sets.
- `src/self_harness/capture_extract.py`
  - Ensure `extract_proposal_validation_manifest` explicitly preserves `task_outcomes` for baselines (it currently does via `_split_outcomes`, but ensure no validation logic drops it).
- `tests/test_reproduction_readiness.py`
  - Update `_class_shaped_payloads` and helpers to simulate iterative improvement:
    - Round 0: Failing tasks = `[tb-held-in-00, tb-held-in-01]`
    - Round 1: Failing tasks = `[tb-held-in-00]`
    - Round 2: Failing tasks = `[]`
  - Update `_proposer_context_rounds` to dynamically match the failing tasks for the given `round_index`.
  - Update the `_rewrite_proposer_context_failure_pattern` test to ensure it still fails against the *intermediate* baseline for that round.
- `docs/architecture/productionization_brief.md`
  - Append P92 entry citing paper Section 3.2 and Algorithm 1.

Non-goals (explicit):
- No schema version bumps for `proposal_validation_manifest`.
- No binding of proposer context to *candidate* task outcomes (only the baseline).
- No changes to `live_two_repeat_evaluation_report` or its cross-artifact checks.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`.
- Capture-rehearsal fixture hash rotates because the synthetic validation and context stubs now simulate iterative improvement.

Stop conditions:
- All listed tests pass.
- `make reproduction-readiness-check` still reports `reproduction_ready: false`.

## Remaining Open Questions

- Should we deprecate the use of `live_two_repeat_evaluation_report` for any proposer context validation entirely? **Yes**, the final evaluation represents `h_T`, which is not the baseline for any intermediate proposer round. The final evaluation should only be used for final pass-rate calculations.
- Should we enforce `task_outcomes` on *candidate* split outcomes as well? **Non-blocking for P92**: The acceptance rule (P86) currently relies on aggregate pass counts. Candidate task-level binding can be deferred to a future slice if needed.

[usage] {"completion_tokens": 7504, "completion_tokens_details": {"reasoning_tokens": 6203}, "prompt_tokens": 150750, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 158254}

---

# Round 2

CONVERGED: YES

## Verdict
P92 (proposer context intermediate-baseline task binding) is ready to execute. The plan correctly identifies a paper-fidelity gap where the proposer context is bound to the *final* evaluation outcomes rather than the *intermediate* baseline outcomes per round. Algorithm 1 specifies that the evidence bundle $B_t$ is derived from failures under the *current* harness $h_t$. The proposed changes to bind proposer context to `proposal_validation_manifest` `baseline_split_outcomes` per round correctly implement this requirement. The required code, test, and doc surfaces are clearly defined, and the backward compatibility risk is mitigated by enforcing the new requirement only when the proposer context artifact is present.

## Critique
- **Evidence (repo):** `_cross_artifact_proposer_context_evidence_binding` in `reproduction_bundle.py` currently takes `evaluation_entry` (`live_two_repeat_evaluation_report`) and uses it to determine `eval_failing` and `eval_passing` for all rounds. This assumes the failing task set is static across all proposer rounds, which contradicts Algorithm 1 where $h_t$ evolves.
- **Evidence (repo):** `proposal_validation_manifest` (introduced in P84) records `baseline_split_outcomes` for each round. P89 made `task_outcomes` optional on these split outcomes.
- **Inference (paper):** Section 3.2 states: "Weakness Mining... Starting from an initial harness, the agent with a fixed model is run on a set of tasks... The agent then clusters failed traces". The failures must belong to the baseline harness of that specific round.
- **Risk (medium, mitigated):** Promoting `task_outcomes` to required for baselines. If we enforce this strictly in the shape validator, it breaks older `1.0` manifests. We can mitigate this by enforcing the requirement inside the cross-artifact check only when `proposer_context_manifest` is present, or by gating it behind the reproduction bundle verification context.
- **Risk (low):** Updating the local fixtures to simulate iterative improvement. The current test fixtures use the same failing task across all rounds. They must be updated to show harness improvement (e.g., round 0 fails tasks A, B; round 1 fails task A; round 2 fails none).

## Required Changes
1. The `_cross_artifact_proposer_context_evidence_binding` check must drop its dependency on `evaluation_entry` and instead depend on `validation_entry` (`proposal_validation_manifest`).
2. For each `round_index` present in `proposer_context_manifest`, the corresponding round in `proposal_validation_manifest` must have `baseline_split_outcomes.task_outcomes`. If `task_outcomes` is missing, the check must fail closed.
3. The `eval_failing` and `eval_passing` sets must be computed per-round from the `baseline_task_outcomes` of that specific round.
4. The test fixture `_proposal_validation_rounds` must simulate iterative improvement by varying `held_in_failing_ids` across rounds, and `_proposer_context_rounds` must be updated to match these intermediate failing sets exactly.

## Revised Plan
**P92 — proposer context intermediate-baseline task binding**

Files:
- `src/self_harness/reproduction_bundle.py`
  - Modify `_cross_artifact_proposer_context_evidence_binding` signature to accept `validation_entry` instead of `evaluation_entry`.
  - Iterate over `validation_by_round` to extract `baseline_split_outcomes.task_outcomes`.
  - If `task_outcomes` is missing, return a fail check: "proposal validation baselines must disclose task outcomes when proposer context is bundled".
  - Compute `baseline_failing` and `baseline_passing` sets for each round from the `baseline_task_outcomes`.
  - Compare `proposer_context_manifest` failure/passing tasks against these per-round baseline sets.
- `src/self_harness/capture_extract.py`
  - Ensure `extract_proposal_validation_manifest` explicitly preserves `task_outcomes` for baselines (it currently does via `_split_outcomes`, but ensure no validation logic drops it).
- `tests/test_reproduction_readiness.py`
  - Update `_class_shaped_payloads` and helpers to simulate iterative improvement:
    - Round 0: Failing tasks = `[tb-held-in-00, tb-held-in-01]`
    - Round 1: Failing tasks = `[tb-held-in-00]`
    - Round 2: Failing tasks = `[]`
  - Update `_proposer_context_rounds` to dynamically match the failing tasks for the given `round_index`.
  - Update the `_rewrite_proposer_context_failure_pattern` test to ensure it still fails against the *intermediate* baseline for that round.
- `docs/architecture/productionization_brief.md`
  - Append P92 entry citing paper Section 3.2 and Algorithm 1.

Non-goals (explicit):
- No schema version bumps for `proposal_validation_manifest`.
- No binding of proposer context to *candidate* task outcomes (only the baseline).
- No changes to `live_two_repeat_evaluation_report` or its cross-artifact checks.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`.
- Capture-rehearsal fixture hash rotates because the synthetic validation and context stubs now simulate iterative improvement.

Stop conditions:
- All listed tests pass.
- `make reproduction-readiness-check` still reports `reproduction_ready: false`.

## Remaining Open Questions
- Should we deprecate the use of `live_two_repeat_evaluation_report` for any proposer context validation entirely? **Yes**, the final evaluation represents $h_T$, which is not the baseline for any intermediate proposer round. The final evaluation should only be used for final pass-rate calculations.
- Should we enforce `task_outcomes` on *candidate* split outcomes as well? **Non-blocking for P92**: The acceptance rule (P86) currently relies on aggregate pass counts. Candidate task-level binding can be deferred to a future slice if needed.

[usage] {"completion_tokens": 3312, "completion_tokens_details": {"reasoning_tokens": 2055}, "prompt_tokens": 153378, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 156690}
