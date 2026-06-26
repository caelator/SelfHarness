# Round 1

CONVERGED: BLOCKED

## Verdict

Closing the multi-commit split-outcome lineage skip via an optional `merged_split_outcomes` field on `proposal_validation_manifest` rounds is **not defensible** under the stated constraints without either a user decision or new evidence. The blocker is epistemic, not implementational: unlike P109's harness-hash path, there is no independent audit source from which to derive or cross-check a merged split outcome.

## Critique

**Evidence (from repo):**
- `docs/architecture/schema_changelog.md` P107 entry: multi-commit split-outcome continuity is skipped because *"the merged harness state is not represented by a single candidate row."*
- `docs/architecture/schema_changelog.md` P109 entry: closes the harness-hash analog by deriving `harness_after_merged_sha256` from audit `lineage.json` `harness_after_hash` — an **independent audit artifact** with a separate write path from `proposal_validation_manifest`.
- `src/self_harness/capture_extract.py::_proposal_validation_round` derives baseline/candidate split outcomes from `audit.evaluations` rows keyed by `proposal_id`/`arm`. There is no `__merged__` arm or equivalent aggregate evaluation record.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` currently skips multi-commit rounds in both lineage (P107) and harness-hash (P108→P109) loops; only the harness-hash loop was closed in P109.

**Inference (architecture):**
- The merged harness state `h_{t+1}` produced by `MERGEACCEPTED` is evaluated at the start of the next round (paper Algorithm 1, line 2). Its split outcome **is** the next round's `baseline_split_outcomes`, by construction.
- Therefore a `merged_split_outcomes` field on round `t`, if stamped by `capture-extract`, could only be sourced from round `t+1`'s baseline. The resulting verifier check (`prev.merged == next.baseline`) would be **circular/tautological**: both sides come from the same audit evaluation rows.
- This is categorically weaker than P109. P109 cross-checks two *different* audit artifacts (`proposal_validation_manifest` vs `lineage.json`). A split-outcome field would cross-check a field against itself-by-proxy.
- It would not distinguish verifier evidence from raw harness recomputation, because no recomputation occurs and no independent verifier record exists for the merged state.

**Risks if implemented anyway:**
- Misleading evidence shape: operators may read the field as independent merged-state verification when it is only internal manifest consistency.
- Future drift: if the engine ever records a true merged evaluation artifact, the semantics of this field would need to change, creating a schema confusion.
- Does not advance paper fidelity in a machine-checkable way beyond what "next round baseline" already provides.

## Required Changes

None can be specified until the blocker resolves. Two paths exist, each requiring a decision:

1. **User decision (reject the slice):** Accept that split-outcome multi-commit lineage is fundamentally un-closeable without a new aggregate evaluation artifact, and leave P107's skip as the documented, honest boundary. Update only docs to make the asymmetry with P109 explicit. *Recommended.*
2. **User decision (engine change):** Add a `__merged__` evaluation arm to the audit writer so the engine records the merged harness state's split outcome as an independent row, then add the field with a true cross-artifact check. *Out of scope for a "local slice"; would rotate canonical audit hash.*

## Revised Plan

No revised candidate plan. The slice as framed cannot satisfy "defensible without live infrastructure" on its own terms, because the defensible derivation source does not exist in the current audit schema, and the only available source (next-round baseline) yields tautological verification.

## Remaining Open Questions

The single deciding question, which requires user input:

> Is the project willing to accept a `merged_split_outcomes` field whose verification semantics reduce to "this field equals the next round's baseline" (internal manifest consistency only, no independent cross-artifact binding), OR must any such field wait until the engine writes an independent merged-evaluation audit record?

- If **internal-consistency-only is acceptable**: round 2 can converge with a minimal additive field + shape validator + capture-extract forward-stamp + tautology-check binding. Expect fixture-hash rotation for capture-manifest/rehearsal only.
- If **independent cross-artifact binding is required** (consistent with P109's standard): this slice is permanently blocked for the current audit schema and should be removed from the convergence queue in favor of an engine-side aggregate evaluation artifact proposal.

No additional reviewer is needed to resolve this; it is a semantic/scope decision, not an evidence gap.

[usage] {"completion_tokens": 3500, "completion_tokens_details": {"reasoning_tokens": 2486}, "prompt_tokens": 134799, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 138299}
