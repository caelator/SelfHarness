CONVERGED: YES

## Verdict
P107 — Proposal-validation lineage continuity (Algorithm 1 `MERGEACCEPTED` state transition) is the next precise, locally implementable paper-fidelity slice. The paper Algorithm 1 specifies that the next harness state `h_{t+1}` is formed by merging accepted candidates from round `t`. This means the baseline evaluation of round `t+1` must be consistent with the state resulting from round `t`'s committed proposals. Specifically, if round `t` accepted exactly one candidate, round `t+1`'s baseline split outcomes must exactly match that candidate's split outcomes. The current verification suite checks intra-round acceptance rules but does not enforce inter-round harness-state continuity, allowing a manifest to claim a lineage where `h_{t+1}` magically reverts or diverges from the accepted `h_t`.

## Critique
Evidence (validated against supplied context):
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` validates candidate acceptance against the round baseline but never compares round `t+1` baseline against round `t` accepted outcomes.
- `tests/test_reproduction_readiness.py::_proposal_validation_rounds` fixture currently re-initializes baseline held-in passing counts to a static value (derived from `_baseline_held_in_failing_ids_for_round`), resulting in round 2 baseline dropping from 32 to 31 despite round 1 accepting a candidate that scored 32. This violates the paper's deterministic state transition.
- P106 ensures `evaluation_repeats` match within a round. Since the fixed protocol fixes repeats across rounds, aggregate pass counts should be stable across identical harness states.

Inference (architecture decisions):
- **Enforcement layer:** The check belongs in `_cross_artifact_proposal_validation_binding` alongside the existing acceptance rule.
- **Merge handling:** If `len(committed_proposal_ids) == 1`, exact match is required. If `len(committed_proposal_ids) == 0`, baseline must match the previous baseline (no-op transition). If `len(committed_proposal_ids) > 1`, exact match is skipped because the merged harness state was not evaluated during validation, though non-regression is implied by the acceptance rule.
- **Fixture rewrite:** The fixture must be rewritten to simulate a valid monotonic lineage (e.g., held-in passes increase until full, then held-out passes increase). This rotates the reproduction-readiness and capture-manifest fixture hashes but leaves the canonical audit hash unchanged.

## Required Changes
None blocking. Decisions resolved:
1. In `_cross_artifact_proposal_validation_binding`, sort validation rounds by index. For `t > 0`, inspect round `t-1`.
2. If `t-1` has 0 committed proposals: require `t` baseline outcomes to equal `t-1` baseline outcomes.
3. If `t-1` has 1 committed proposal: find that candidate in `t-1` and require `t` baseline outcomes to equal the candidate's outcomes.
4. If `t-1` has `>1` committed proposals: skip the exact match check (merge state is unobserved).
5. Rewrite `_proposal_validation_rounds` in `tests/test_reproduction_readiness.py` to maintain a running `baseline_held_in_passed` and `baseline_held_out_passed` across rounds.

## Revised Plan
**P107 — Proposal-validation lineage continuity (Algorithm 1 `MERGEACCEPTED`)**

Code (no schema-version bump, no new artifact class, no canonical audit hash rotation):

- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding`:
  - After processing all rounds, iterate through `sorted(validation_by_round)`.
  - For `round_index > 0`, fetch `previous_round = validation_by_round[round_index - 1]`.
  - Extract `previous_committed = _string_list(previous_round, "committed_proposal_ids", ...)`.
  - Determine `expected_outcomes`:
    - If `len(previous_committed) == 0`: `expected_outcomes = previous_round.baseline_split_outcomes`.
    - If `len(previous_committed) == 1`: find candidate in `previous_round.candidates` matching `previous_committed[0]`. `expected_outcomes = candidate.split_outcomes`.
    - If `len(previous_committed) > 1`: `expected_outcomes = None` (skip).
  - If `expected_outcomes` is not None, compare `current_round.baseline_split_outcomes` against `expected_outcomes` (specifically `held_in_passed`, `held_in_total`, `held_out_passed`, `held_out_total`).
  - On mismatch, append to `lineage_continuity_violations` with `round_index`, `previous_round_index`, `expected`, and `actual`.
  - Append failure message: `"proposal validation round {t} baseline must match round {t-1} committed state"`.

Tests:

- `tests/test_reproduction_readiness.py`:
  - Rewrite `_proposal_validation_rounds` to track state across rounds:
    - Initialize `baseline_held_in_passed = held_in_total - 2` (or similar).
    - Initialize `baseline_held_out_passed = held_out_total - 2`.
    - For each round:
      - Use current `baseline_*` values for the round's `baseline_split_outcomes`.
      - For accepted candidate (candidate 0):
        - If `baseline_held_in_passed < held_in_total`: candidate improves held-in.
        - Elif `baseline_held_out_passed < held_out_total`: candidate improves held-out.
        - Else: candidate cannot be accepted (adjust fixture if rounds exhaust improvements, or mark as rejected).
      - Update `baseline_*` values to the accepted candidate's values for the *next* iteration.
  - Update expected fixture hashes in `tests/fixtures/release_candidate/expected_hash.txt` if necessary (reproduction bundle reports will change due to manifest content change, but canonical audit hash should remain stable as the audit writer logic is untouched).

Docs:

- `docs/architecture/schema_changelog.md`: Add P107 entry under proposal-validation manifest notes.
- `docs/operations/benchmark_reproduction_requirements.json`: Update `proposal_validation_records.notes` to mention lineage continuity binding.
- `docs/architecture/productionization_brief.md`: Append P107 entry.

Non-goals:
- No new artifact class.
- No live infrastructure contact.
- No enforcement of exact pass counts if `committed_proposal_ids > 1` (merge evaluation is deferred to the next baseline).

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` updated if release evidence fixture hash rotates.

## Remaining Open Questions
None blocking.
