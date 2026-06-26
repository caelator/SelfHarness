CONVERGED: YES

## Verdict

Round 1 produced a fully executable plan. The four open questions it flagged are design choices decidable from the brief alone — none are evidence gaps. I ratify round 1's defaults with one tightening (determinism requirements for byte-identical re-runs) and one confirmation (the cross-split tension task is constructible with a parameterized toy runner). No additional repository evidence is required.

Evidence (from brief): "accept only if the candidate improves at least one split and does not degrade the other" ⇒ strict improvement, ties reject. "Held-out traces must not be exposed to the proposer" ⇒ type-signature enforcement satisfies this. "Multiple compatible accepted candidates can be merged" ⇒ disjoint-surface-or-commutative rule with mandatory re-validation is a sound operationalization.
Inference: pattern→surface table, tie-break order, and audit retention policy are MVP scoping decisions; the defaults below are deterministic and testable.

## Critique

Round 1's plan survives second-pass review on all five brief axes.

- **Module boundaries.** Three-way merge split (structural / re-validation / commit) preserves the verifier-grounded-transition invariant. No change.
- **Safety model.** DSL ops + structural surface validation + type-signature held-out isolation make "bounded, auditable, reversible" structural rather than policy-based. Correct for MVP.
- **Validation gate.** Strict-improvement rule is unambiguous from the brief. Merge re-validation closes the only real loophole. Fallback to single-apply on merge regression preserves accepted work without violating the gate.
- **Audit artifacts.** Five-file minimum is sufficient and testable. Rejected-proposal retention inside `proposals.jsonl` is adequate; a separate rejection log would be redundant.
- **Toy demo.** The regression pair is constructible: a parameterized `ToyRunner` can simulate "stronger bootstrap helps short-context tasks but distracts long-context tasks," yielding held-in improvement and held-out regression from one candidate. This is the load-bearing demo requirement.

One addition: **byte-identical re-runs require pinned serialization**. Without `sort_keys=True`, fixed separators, and a threaded `random.Random`, the determinism test will be flaky. I promote this from implementation detail to required decision.

## Required Changes

1. **Ratify round 1 changes 1–6 unchanged.**
2. **Ratify the four round 1 open questions as locked decisions** (see Revised Plan).
3. **Add determinism requirements**: `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`; explicitly-threaded `random.Random(seed)`; no `time.time()`, `uuid4`, or dict-ordering dependency on any audit path.

## Revised Plan

**Modules**: unchanged from round 1 (`types`, `harness`, `mining`, `proposer`, `evaluation`, `engine`, `demo`, `cli`, `tests/`).

**Locked decisions**:
- DSL ops: `ReplaceSurface`, `AppendToSurface`, `SetPolicy`, each `(surface, payload)` with generated `reverse()`.
- Surface whitelist: `system_prompt`, `bootstrap`, `execution`, `verification`, `failure_recovery`, `runtime_policy`.
- Pattern → surface table:
  - `missing_artifact → AppendToSurface(bootstrap)`
  - `repeated_failed_command → AppendToSurface(failure_recovery)`
  - `late_verification → AppendToSurface(verification)`
  - `environment_persistence → AppendToSurface(execution)`
  - `cross_split_tension → proposer emits both an obvious-but-regressing candidate and a targeted one; gate accepts only the targeted one`
- Acceptance: strict improvement on ≥1 split AND no degradation on the other. Ties on both ⇒ reject.
- Merge: structurally mergeable iff disjoint surfaces OR commutative ops on same surface in fixed order; never auto-merge two `ReplaceSurface` on same surface; merged spec must re-pass acceptance on both splits; on merge regression, fall back to single highest-priority accept.
- Tie-break: proposer-priority desc, then surface name lexicographic asc.
- Stopping: 3 rounds OR first round with zero accepts.
- Held-out isolation: `Proposer.propose(held_in_patterns: List[FailurePattern], harness, budget)`; engine runtime-asserts every pattern has `split == HELD_IN`.

**Audit schema (pinned)**:
- `manifest.json`: `{protocol_hash, model_id, decoding_budget, seed, surface_whitelist, op_whitelist}`
- `lineage.json`: list of `{round, harness_before_hash, harness_after_hash, ops_applied, reverse_ops, accepted_proposal_ids}`
- `rounds/<n>/harness_before.json`, `harness_after.json`: full `HarnessSpec`
- `rounds/<n>/proposals.jsonl`: `{id, round, pattern_id, op, surface, payload, status, score_held_in, score_held_out, baseline_held_in, baseline_held_out, rejection_reason?}`
- `rounds/<n>/evaluations.jsonl`: `{proposal_id, split, arm, verifier_pass, verifier_fail, score}`

**Toy catalog (5 tasks)**: `missing_artifact_short` (held-in, fixable), `repeated_failed_command` (held-in, fixable), `late_verification` (held-in, fixable), `environment_persistence` (held-in, fixable), `long_context_overprompting` (held-out, regresses under the same strong-bootstrap candidate that fixes task 1).

**Tests**: mining signature stability; patch reversal round-trip; acceptance accept/reject/tie-reject; merge structural check (disjoint OK, commutative OK, replace-repair rejected); merge re-validation gate with fallback; demo end-to-end produces ≥1 accept and ≥1 reject and is byte-identical on re-run; held-out isolation assertion fires; determinism diff-stable across all artifacts.

## Remaining Open Questions

None blocking. All four round 1 questions are now decisions. Remaining choices (e.g., exact prose of bootstrap instructions in the toy runner) are implementation detail within the locked surface table and do not require further architect sign-off.
