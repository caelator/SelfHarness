# Round 1

CONVERGED: NO

## Verdict

The architecture brief is detailed enough to draft an executable plan in round 1, but three design decisions need to be locked before claiming "ready to execute without missing evidence": (a) patch operation model, (b) merge semantics when multiple candidates pass in one round, and (c) the toy failure-mode catalog must demonstrably exercise the non-regression gate. None of these require external evidence; they require committing to defaults and documenting the tradeoffs. I provide those defaults below as the revised plan and recommend round 2 confirm them rather than relitigate.

Evidence: All claims above derive from the supplied brief. Inference: the specific defaults (DSL ops, disjoint-surface merge, scripted regression pair) are architect recommendations, not facts from the repo.

## Critique

**Module boundaries.** The proposed layout is sound. One ambiguity: `harness.py` currently owns "merge compatibility checks," but compatibility has two layers — *textual/structural commutativity* (a harness concern) and *behavioral non-regression after merge* (an evaluation concern). The brief collapses them. Recommendation: `harness.py` owns structural mergeability only; `evaluation.py` re-runs the acceptance rule on the merged harness before it is committed. This preserves the paper's invariant that *every* accepted transition is verifier-grounded, including merges.

**Safety model for harness edits.** The brief lists editable surfaces but does not state the *patch algebra*. Two viable options:
- **JSON-patch over the harness spec** — general, but allows edits to non-declared fields unless explicitly filtered, and makes reversibility harder to verify.
- **Domain-specific ops against named surfaces** (`ReplaceSurface`, `AppendToSurface`, `SetPolicy`) — bounded by construction, trivially reversible, and auditable at the op level.

I recommend the DSL. The paper's "bounded and auditable" constraint is satisfied structurally rather than by policy, which matters for a self-modifying loop. The cost is less generality; acceptable for MVP.

A second safety property is under-specified: **held-out isolation**. The brief says "held-out traces must not be exposed to the proposer" but does not say how the proposer API enforces it. Recommendation: `Proposer.propose(patterns: List[HeldInPattern], harness: HarnessSpec, budget: ProposalBudget)` — the type signature itself prevents leakage; the engine never constructs a proposer call with held-out data.

**Validation gate.** The paper rule is clear: improve ≥1 split, degrade neither. Open issue: when N candidates pass independently, naive sequential application can produce interactions. The brief's "multiple compatible accepted candidates can be merged" needs a defined compatibility rule. Recommendation: in MVP, two accepted candidates are merge-compatible iff they touch **disjoint surfaces** OR their ops are **textually commutative** (e.g., both append to different surfaces, or both append to the same surface in fixed order). Mergers that touch the same surface via `ReplaceSurface` are never auto-merged. The merged harness is then re-validated; if it regresses, the merger is rejected but the individual accepts stand (apply only one, by proposer priority).

**Audit artifacts.** The brief asks "what is essential?" Minimum viable set:
- `rounds/<n>/harness_before.json`, `harness_after.json` — snapshots
- `rounds/<n>/proposals.jsonl` — every candidate with status `{accepted, rejected, merged, superseded}` and rejection reason
- `rounds/<n>/evaluations.jsonl` — one record per (candidate × split) with verifier pass/fail counts
- `lineage.json` — append-only chain linking round N's `harness_after` to round N+1's `harness_before`, with hashes
- `manifest.json` — model id, decoding budget, protocol hash, seed

Snapshots are technically reconstructible from lineage + ops, but materializing them is cheap and dramatically improves inspectability. Keep them.

**Toy demo sufficiency.** A purely synthetic runner risks trivially passing the gate: if the heuristic proposer knows the scripted bug, every edit "works." The demo must include at least one **regression pair** — a failure pattern whose obvious fix degrades the other split, forcing the acceptance rule to fire. Concrete catalog (inference, not in brief):
1. `missing_artifact` — agent never produces required file; fix = stronger bootstrap instruction.
2. `repeated_failed_command` — agent retries same failing command; fix = failure recovery instruction.
3. `late_verification` — agent verifies too late to recover; fix = verification instruction.
4. `environment_persistence` — agent forgets prior turn state; fix = execution instruction.
5. **Cross-split tension**: the held-out set contains a task where stronger bootstrap instruction causes over-prompting and a different failure. This forces the gate to reject the obvious fix and rewards a more targeted edit.

Without item 5 the demo does not actually demonstrate the paper's value proposition.

**Subprocess runner question.** Decided by the brief itself: MVP is toy, no API keys, deterministic. Out of scope to add a real runner now. The runner abstraction (`Runner.run(task, harness) -> RunRecord`) is the only seam that matters; keeping it a Protocol means a subprocess implementation can drop in later.

## Required Changes

1. **Adopt DSL patch ops**, not JSON-patch. Enumerate `ReplaceSurface`, `AppendToSurface`, `SetPolicy`, each with `surface`, `payload`, and a generated `reverse()` op.
2. **Split merge logic across modules**: structural commutativity in `harness.py`; post-merge re-validation in `evaluation.py`; commit decision in `engine.py`.
3. **Encode held-out isolation in the proposer type signature**, not just in prose.
4. **Add the regression-pair task** to the demo catalog so the acceptance rule is exercised both ways (accept and reject).
5. **Pin the audit artifact schema** to the five-file minimum above before writing code, so tests can pin exact shapes.
6. **Define the demo stopping rule**: fixed 3 rounds OR first round with zero accepted proposals, whichever comes first. Seeded RNG.

## Revised Plan

**Modules**
- `self_harness/types.py` — `Task`, `RunRecord`, `Trace`, `VerifierOutcome`, `FailureSignature`, `FailurePattern`, `Proposal`, `HarnessSpec`, `HarnessPatch` (DSL ops), `EvaluationResult`, `LineageRecord`, `Split` enum (`HELD_IN`, `HELD_OUT`).
- `self_harness/harness.py` — `HarnessSpec` with declared editable surfaces; `apply_patch(spec, patch) -> (new_spec, reverse_patch)`; `structurally_mergeable(p1, p2) -> bool`; `merge_patches(ps) -> patch`.
- `self_harness/mining.py` — `signature_of(run) -> FailureSignature` over the three paper axes; `cluster(runs) -> List[FailurePattern]`; deterministic ranking by frequency then signature order.
- `self_harness/proposer.py` — `Proposer` Protocol with `propose(held_in_patterns, harness, budget) -> List[Proposal]`; `HeuristicProposer` mapping each known pattern to a candidate DSL op; budget caps proposal count.
- `self_harness/evaluation.py` — `Evaluator` Protocol with `evaluate(harness, split) -> SplitResult`; `acceptance_rule(cand, baseline) -> AcceptDecision`; `validate_merge(merged_spec, baseline) -> AcceptDecision`.
- `self_harness/engine.py` — `run_round(state) -> RoundRecord`; orchestrates mine → propose → evaluate → accept → merge → re-validate → commit → audit.
- `self_harness/demo.py` — `ToyTask` catalog (5 items incl. regression pair); `ToyRunner` implementing `Runner` Protocol; deterministic behavior parameterized by harness instructions.
- `self_harness/cli.py` — `self-harness demo --seed N --rounds 3 --out dir/`; prints compact summary table.
- `tests/` — mining signature stability, patch reversal round-trip, acceptance rule accept/reject/inconclusive, merge structural check, merge re-validation gate, demo end-to-end snapshot, held-out isolation invariant (proposer never receives held-out patterns).

**Safety invariants, enforceable by tests**
- Every committed `harness_after` has a chain of reverse patches back to the initial spec.
- Every accepted transition (single or merged) has a passing acceptance record on both splits.
- Proposer call sites receive only `HELD_IN` patterns (statically checkable via type signature; runtime-asserted in engine).
- Patches touching non-declared surfaces are rejected at construction.
- Per-round proposal budget and per-patch byte budget are enforced.

**Demo acceptance criteria**
- At least one round produces an accepted proposal that improves held-in without regressing held-out.
- At least one candidate is rejected by the acceptance rule (the regression pair).
- Re-running with the same seed produces byte-identical audit artifacts.

## Remaining Open Questions

These are non-blocking for execution but worth a confirmation pass in round 2:

1. **Pattern → surface mapping table.** The `HeuristicProposer` needs a concrete mapping from each failure signature to a candidate edit. I have inferred one above; round 2 should ratify it so the test fixtures can be written against a stable table.
2. **Acceptance under tied scores.** When candidate and baseline tie on both splits, the paper rule says reject (no strict improvement). Confirm this is the intended MVP semantics, vs. "accept ties to encourage exploration." Default: reject.
3. **Merge tie-breaking.** If two accepted candidates target the same non-commutative surface, which one is applied? Default: higher proposer-priority score, then lexicographic by surface name. Confirm.
4. **Audit artifact retention policy for rejected proposals.** Default: keep forever in `proposals.jsonl` with `status: rejected` and a `rejection_reason` field. Confirm this is sufficient vs. needing a separate rejection log.

None of the above requires repository evidence or an experiment. Round 2 can converge on YES by ratifying the defaults or substituting alternatives.

[usage] {"completion_tokens": 4717, "completion_tokens_details": {"reasoning_tokens": 2548}, "prompt_tokens": 1172, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 5889}

---

# Round 2

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

[usage] {"completion_tokens": 4877, "completion_tokens_details": {"reasoning_tokens": 3568}, "prompt_tokens": 5537, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 10414}
