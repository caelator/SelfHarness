# Round 1

CONVERGED: YES

## Verdict
P95 is ready to execute: **baseline task-outcome failure-category binding to proposer-context failure patterns**. This closes a genuine paper-fidelity gap — the paper (Section 3.2) defines failure signature ϕ(r) = (c, q, m) and clusters failures by *exact agreement on all three components*, but current verification only binds the mechanism dimension (m, via `mechanism_sha256`). The terminal verifier-cause dimension (c) is captured in audit `failure_category` rows but never propagated to proposal-validation baseline task outcomes or cross-checked against proposer-context failure patterns, so two tasks with different verifier causes could silently share a proposer-context cluster. The slice is bounded, additive, offline-only, and reuses the existing closed `failure_category` enum.

## Critique
- **Evidence (paper, Section 3.2):** "Failures are clustered by exact agreement of this signature: C_ϕ = {r ∈ F_t | ϕ(r) = ϕ}", where ϕ(r) = (c_i, q_i, m_i) and c_i denotes the terminal verifier-level cause. The paper explicitly motivates clustering by verifier cause to avoid conflating failures that share an outcome but need different harness changes.
- **Evidence (repo):** `tests/test_capture_extract.py::_audit_task_outcome_rows` shows audit evaluation rows already carry a closed `failure_category` enum (introduced P4, surfaced as `failure_category` in schema 1.2). `capture_extract._task_outcome_pass` reads only `verifier_pass`, so `failure_category` is dropped during `proposal_validation_manifest` extraction.
- **Evidence (repo):** `proposer_context_manifest` failure patterns expose `mechanism_sha256` (the m component) and `task_ids`, but no cluster-level verifier-cause attestation, and bundle verification never cross-checks task-level causes inside a pattern.
- **Evidence (repo):** `_artifact_shapes._HELD_IN_FAILURE_PATTERN_FIELDS` is closed, so adding a cluster `failure_category` field requires a deliberate schema-adjacent extension; keeping it optional preserves reduced bundles.
- **Inference:** q (causal status of agent behavior) is not deterministically recoverable from current artifacts and is intentionally left opaque, matching how `preserved_behavior_sha256` is treated. Binding only c keeps the slice bounded while closing the most verifiable clustering dimension.

## Required Changes
None beyond the revised plan. The plan already separates additive schema extension, capture-extract propagation, cross-artifact invariant, capture-manifest diff coverage, and explicit non-goals (q-component binding, enforcement of single-surface minimality, semantic parsing of rejection reasons).

## Revised Plan
**P95: Failure-category clustering binding (paper Section 3.2 c-dimension)**

Files to modify:
- `src/self_harness/capture_extract.py` — extend `_split_task_outcomes` / `_audit_task_outcome_rows` reading so baseline `task_outcomes` rows carry optional `failure_category` when the audit row provides it; keep absent for legacy audits.
- `src/self_harness/_artifact_shapes.py` — extend `_PROPOSAL_VALIDATION_TASK_OUTCOME_FIELDS` to include optional `failure_category`; extend `_HELD_IN_FAILURE_PATTERN_FIELDS` with optional cluster-level `failure_category`; validate against the existing closed `failure_category` enum.
- `src/self_harness/reproduction_bundle.py` — in `_cross_artifact_proposer_context_evidence_binding`, when bundled proposer context is present and baseline `task_outcomes` disclose `failure_category`, add a new `failure_pattern_terminal_cause_violations` bucket requiring every task id in a `held_in_failure_patterns.pattern` to share the same baseline `failure_category`; fail closed when a pattern declares a cluster `failure_category` that disagrees with the observed task set, and when tasks within one pattern disagree.
- `src/self_harness/capture_manifest_diff.py` — include optional cluster `failure_category` in the `proposal-validation-derivation` / `proposer-context-evidence-derivation` planned-vs-realized comparison so rehearsal plans catch cause drift.
- `tests/test_reproduction_readiness.py` — add negative test where two held-in baseline failures in the same proposer-context pattern carry different `failure_category` values (bundle fails), plus happy-path unchanged test.
- `tests/test_capture_manifest.py` — add rehearsal drift test where planned cluster `failure_category` differs from realized proposer-context pattern.
- `docs/architecture/productionization_brief.md` — append P95 entry.
- `docs/operations/benchmark_reproduction_readiness.md` — note the new c-dimension clustering invariant and the optional fields.
- `docs/architecture/schema_changelog.md` — append `Proposal Validation Manifest 1.0` / `Proposer Context Manifest` note explaining the additive optional `failure_category` fields and the new `cross_artifact_proposer_context_failure_category_binding` check name.

Verification gates:
- `make check`
- `make readiness`
- `make reproduction-readiness-check` (still reports `reproduction_ready: false`)
- `make capture-manifest-diff-check`
- `tests/fixtures/canonical_audit_hash.txt` unchanged (default writer schema unchanged; new fields are optional and only emitted by `capture-extract` when audit rows carry `failure_category`).

Non-goals:
- Binding the q component (causal status of agent behavior) of failure signatures.
- Enforcing single-surface minimality per candidate (deferred from P94).
- Requiring `failure_category` on every baseline task outcome (legacy audits without it remain valid; the invariant fires only when both proposer context and baseline outcomes disclose causes).
- Changing the default audit writer schema or rotating the canonical readiness hash.

Stop conditions:
- All listed tests pass.
- `make reproduction-readiness-check` still exits `2` with `reproduction_ready: false`.
- No live Harbor/Docker/model/PyPI/Sigstore contact and no benchmark reproduction claim.

## Remaining Open Questions
Both are explicitly non-blocking and correctly deferred:
- Whether to elevate cluster-level `failure_category` from optional to required for paper-faithful bundles once enough operator evidence exists — follow-up policy decision.
- Whether to extend the invariant to the `live_harbor_audit` artifact's per-attempt `terminal_cause` so live evidence can also be clustered by verifier cause — future slice once per-attempt cause disclosure is stabilized.

[usage] {"completion_tokens": 5125, "completion_tokens_details": {"reasoning_tokens": 3776}, "prompt_tokens": 161521, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 166646}
