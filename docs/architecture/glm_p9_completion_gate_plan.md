CONVERGED: YES

## Verdict

Round 2 architect review. The Round 1 revised plan is implementation-ready: it targets the correct paper-fidelity gap (unbacked LLM proposer seam + missing trajectory reporting), stays within the implementable-now boundary (no Harbor/Docker), pins the ambiguous design decisions (diversity locus, trajectory schema, evidence-bundle completeness), and hardens the production contract (optional-dependency leakage guard, canonical-hash stability). Evidence from the repository confirms the seam exists (`LLMProposer`, `LLMClient` in stable API; `ProposerContext.held_in_patterns` already carries `FailurePattern` with `support`, `task_ids`, `symptoms`, `verifier_evidence`, `mechanism`) but no reference adapter ships and no invariant tests exercise the LLM path. The plan closes both gaps without overreaching into reproduction claims.

## Critique

The Round 1 plan resolved all five material risks it identified. Two minor implementation details remain that do not block execution:

1. **Exception types**: The plan references `LLMClientError` and `LLMRequestError`. Evidence: `self_harness/exceptions.py` currently exports `PaperFidelityError` and `InvalidPatchError` (visible in test imports). The adapter will need to add these two new exceptions or reuse a generic `LLMClientError` with a `status` field. This is a coding decision, not an architecture decision.

2. **Merge-arm trajectory derivation**: The trajectory schema's `score_held_in_delta`/`score_held_out_delta` per-proposal fields must be derived from each proposal's own candidate evaluation, while the round-level `after_held_in_passed`/`after_held_out_passed` must reflect the committed arm (merged if accepted, else best single, else baseline). The existing `_committed_eval_selector` in `audit.py` already encodes this logic; the CLI should reuse it. Non-blocking.

Both are resolvable during implementation without another round.

## Required Changes

None blocking. The plan from Round 1 is approved as written.

## Revised Plan

No changes from the Round 1 revised plan. Proceed to implementation with the following clarifications (non-blocking):

- Add `LLMClientError` and `LLMRequestError` to `self_harness/exceptions.py` as distinct typed exceptions; export them in the stable API.
- `audit-trajectory` must reuse `audit._committed_eval_selector` (or equivalent logic) to determine which evaluation arm represents the committed round outcome, ensuring per-round `after_*` fields match `audit-summary` output.
- Update `tests/fixtures/canonical_audit_hash.txt` as part of the same PR that adds `trajectory.jsonl` to the run directory; document the fixture rotation in `docs/architecture/schema_changelog.md` under a `trajectory 1.0` entry.
- The `python -c "import self_harness"` CI job must run in an environment where neither `anthropic` nor `provenance` extras are installed, to prove the core package has zero required runtime dependencies beyond the standard library.

## Remaining Open Questions

All five questions from Round 1 remain non-blocking and can be resolved by the implementer:

1. Default location of `trajectory.jsonl`: inside run directory (recommended; becomes part of audit tree hash).
2. `anthropic` SDK pin: `>=0.40,<1.0` minor range.
3. No-deps HTTP fallback: out of scope for P9.
4. `LLMProposer` seed: deferred; paper requires deterministic audit artifacts, not deterministic model output.
5. Trajectory schema versioning: separate `1.0` schema, independently versioned from audit schema.
