CONVERGED: YES

## Verdict
P93 (proposal grounding binding to proposer context) is ready to execute. The current bundle verification enforces that *previous* attempted edits bind to prior proposer context ingredients (P82), but it fails to enforce the paper's Section 3.3 requirement that *current* candidate proposals are grounded in the same-round evidence bundle $B_t$. Specifically, it does not verify that a candidate's `targeted_mechanism_sha256` and `edited_surface_sha256` exist in the proposer context manifest for that round, nor does it enforce that the $K$ candidates are materially distinct. Closing this gap ensures that all validated harness modifications are strictly derived from the editable surfaces and failure mechanisms presented to the proposer.

## Critique
- **Evidence (repo):** `_cross_artifact_proposal_validation_binding` in `reproduction_bundle.py` already loads `context_by_round` to verify baseline task outcomes (P92) and previous edits (P82), but it ignores the context ingredients when validating the current round's candidates.
- **Evidence (repo):** The proposal validation manifest records `targeted_mechanism_sha256` and `edited_surface_sha256` for each candidate, and the proposer context manifest records the allowed `mechanism_sha256` and surface `sha256` values. The data required for validation is already present.
- **Inference (paper):** Section 3.3 states: "A proposal must be grounded in a primary failure mechanism and mapped to a concrete editable surface. ... The candidates are required to be materially distinct: they should not merely restate the same cluster, surface, or mechanism with different wording." This implies a strict binding between proposed candidates and the proposer context, as well as a uniqueness constraint among candidates.

## Required Changes
1. The `_cross_artifact_proposal_validation_binding` check must use `context_by_round[round_index]` to fetch the allowed sets of mechanism hashes and editable surface hashes.
2. For each candidate in the validation round, if the proposer context manifest is bundled, the candidate's `targeted_mechanism_sha256` and `edited_surface_sha256` must exist in those allowed sets. Missing values must trigger a fail-closed bundle verification.
3. For each round, the verifier must check that the set of `(targeted_mechanism_sha256, edited_surface_sha256)` tuples across all candidates is equal in length to the number of candidates, ensuring they are materially distinct.
4. The test suite must be updated to simulate candidates that drift from the proposer context or duplicate another candidate's signature.

## Revised Plan
**P93 — Proposal grounding binding to proposer context**

Files:
- `src/self_harness/reproduction_bundle.py`
  - Modify `_cross_artifact_proposal_validation_binding`.
  - For each round where `context_by_round` has an entry:
    - Extract `allowed_mechanisms = _context_failure_mechanism_sha256s(context_round)`.
    - Extract `allowed_surfaces = _context_editable_surface_sha256s(context_round)`.
    - For each candidate, verify `candidate.targeted_mechanism_sha256` is in `allowed_mechanisms`.
    - Verify `candidate.edited_surface_sha256` is in `allowed_surfaces`.
    - Record any mismatches in `candidate_mechanism_violations` and `candidate_surface_violations` metadata, and append to failures.
  - For each round, compute `candidate_signatures` as a list of `(targeted_mechanism_sha256, edited_surface_sha256)` tuples.
  - If `len(set(candidate_signatures)) != len(candidate_signatures)`, record `candidate_distinctness_violations` and append to failures.
- `tests/test_reproduction_readiness.py`
  - Add a test that rewrites a candidate's `targeted_mechanism_sha256` to an unknown value and asserts bundle verification fails.
  - Add a test that rewrites a candidate's `edited_surface_sha256` to an unknown value and asserts bundle verification fails.
  - Add a test that duplicates a candidate's `targeted_mechanism_sha256` and `edited_surface_sha256` and asserts bundle verification fails.
- `docs/architecture/productionization_brief.md`
  - Append P93 entry citing paper Section 3.3.

Non-goals (explicit):
- No schema version bumps for `proposal_validation_manifest`.
- No semantic parsing of free-text proposal rationales.
- No changes to the acceptance rule aggregate pass-count logic (P86).

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`.

Stop conditions:
- All listed tests pass.
- `make reproduction-readiness-check` still reports `reproduction_ready: false`.

## Remaining Open Questions
- Should we enforce that all $K$ candidates target *different* mechanisms, or is the current definition of material distinctness (different mechanism OR different surface) sufficient? **Non-blocking for P93**: The paper allows candidates to target a different mechanism, choose a different surface, or express a different hypothesis. Bounding by the `(mechanism, surface)` tuple safely prevents identical proposals without over-constraining the search space.
