# Paper-Faithful Build Plan

Status: P0 implementation complete after GLM convergence and direct verification
against `Self-Harness: Harnesses That Improve Themselves` (arXiv:2606.09498).

## Verdict

The current implementation is now a paper-faithful toy implementation of the
Self-Harness algorithmic protocol. It captures fixed-harness lineage, held-in
weakness mining, bounded harness edits, held-out promotion gates, repeated
evaluation aggregation, enriched proposer context, invalid/rejected proposal
handling, and deterministic audit artifacts. It is not a Terminal-Bench-2.0
reproduction until a DeepAgent or Terminal-Bench-compatible runner is added and
evaluated under a fixed protocol.

## P0 Implementation Status

Implemented:

- `RunRecord.attempt_index`, `EvaluationResult.evaluation_repeats`, and engine
  default `evaluation_repeats=2`.
- Aggregate pass-count validation over repeated attempts.
- `PassingSummary`, `AttemptedEdit`, and `ProposerContext`.
- Proposer context construction from held-in failures, held-in passing records,
  prior attempted edits, editable surfaces, current harness, round, and budget.
- Runtime held-out isolation assertions for patterns and passing summaries.
- Proposal audit rows with `changed_surfaces`, aggregate pass counts,
  `evaluation_repeats`, `decision_reason`, and invalid/rejected reasons.
- Evaluation audit rows with per-attempt `attempt_index` records plus split
  totals.
- Invalid proposal handling for empty patches, patch failures, and evaluation
  failures.
- Tests for repeat aggregation, enriched audit fields, deterministic artifacts,
  and proposer held-out isolation.

## Already Faithful

- A fixed harness object is the object of improvement.
- Tasks are partitioned into held-in and held-out splits.
- Weakness mining uses exact verifier-grounded signatures:
  terminal cause, causal status, and reusable agent mechanism.
- Held-out traces are not passed to the proposer.
- Candidate edits are bounded to declared editable surfaces.
- Proposal metadata records targeted failure pattern, surface, expected effect,
  and regression risks.
- Validation evaluates current and candidate harnesses on both splits.
- Acceptance requires non-negative deltas on both splits and strict improvement
  on at least one split.
- Compatible accepted edits are merged and re-validated before commit.
- Rejected proposals are logged without changing the active harness.
- Audit files are deterministic and preserve harness lineage.

## Implemented P0 Paper-Faithful Contracts

### P0. Evaluation Repeats And Aggregate Validation

Paper requirement: candidate validation repeats evaluation when stochastic and
uses aggregate pass counts. The experiment reports two repeated attempts per
harness candidate unless otherwise specified.

Implemented behavior:

- Added `attempt_index` to `RunRecord`.
- Added `evaluation_repeats` to engine configuration and manifest.
- Updated `evaluate(runner, harness, tasks, repeats=2)` to run each task
  `repeats` times.
- Updated `SplitResult.passed` and `SplitResult.failed` to count attempts, not
  only tasks.
- Kept the acceptance rule unchanged in shape, but compute deltas over aggregate
  pass counts.
- For deterministic toy runs, repeated attempts may return identical results;
  the protocol still exercises the paper contract.

### P0. Proposer Context Enrichment

Paper requirement: proposer receives editable surfaces, verifier-grounded
failure patterns, passing behaviors to preserve, and summaries of previous
attempted edits. Held-out traces must still be excluded.

Implemented behavior:

- Added `PassingSummary` and `AttemptedEdit` dataclasses.
- Added `ProposerContext` with:
  `held_in_patterns`, `passing_summaries`, `attempted_edits`,
  `editable_surfaces`, `harness`, `round_index`, and `budget`.
- Replaced `Proposer.propose(held_in_patterns, harness, budget, round_index)` with
  `Proposer.propose(context)`.
- Built passing summaries only from held-in successful records.
- Built attempted-edit history from proposal audit rows across previous rounds.
- Runtime asserts that all patterns and passing summaries in proposer context are
  held-in only.

### P0. Audit Schema Completion

Paper requirement: audit records include changed surfaces, split-wise outcomes,
evaluation repeats, proposal summary, accept/reject decision, and failed/invalid
candidate reasons.

Implemented behavior:

- Added `changed_surfaces` to proposal JSONL rows as the sorted set of all patched
  surfaces, not only the primary op.
- Added `evaluation_repeats` and `attempt_index` to evaluation JSONL rows.
- Added explicit `decision_reason` and kept `rejection_reason` with values for
  rejected and superseded proposals.
- Added invalid candidate status for empty patches, unsupported surfaces, patch
  validation failures, and evaluation failures before valid results exist.
- Recorded merge validation rows with proposal id `__merge__` and the same repeat
  metadata.

## Should Change Next For Better Fidelity

### P1. Editable Surface Parity

Add inert-but-declared surfaces for `tools`, `skills`, `memory_sources`, and
`subagents` so the harness shape more closely matches the paper's DeepAgent
interface. The toy runner does not need to exercise these yet.

### P1. Addressability And Diversity

Add explicit addressability filtering for failure patterns and proposal diversity
checks so the system can distinguish "no proposal because non-actionable" from
"no proposal because the heuristic proposer lacks coverage".

### P1. Real Runner Seam

Add a subprocess/Terminal-Bench-style runner interface after the P0 contracts are
implemented. The next real runner should preserve the paper's controls: fixed
model, fixed evaluator, fixed tool budget, fixed task split, and fresh task
environment per attempt.

## Acceptable Toy Simplifications

- The MVP may keep a deterministic `ToyRunner`; repeated attempts can be
  identical as long as the aggregation machinery exists.
- The MVP may keep `HeuristicProposer` instead of a same-model LLM proposer,
  provided the `ProposerContext` API exactly models the paper boundary and leaves
  an LLM proposer extension point.
- The MVP may use a small synthetic task set, provided it demonstrates both
  accepted and rejected proposals under the held-out regression gate.
- The MVP may represent harness edits as a domain-specific patch DSL rather than
  raw source-code diffs, because this strengthens bounded edit enforcement.

## Completed Implementation Order

1. Updated dataclasses and JSON serialization for repeats, passing summaries,
   attempted edits, and proposer context.
2. Updated evaluation repeat aggregation and tests for aggregate acceptance.
3. Updated engine proposer-context construction, attempted-edit history, invalid
   proposal handling, and audit rows.
4. Updated heuristic proposer to consume `ProposerContext`.
5. Updated demo tests to assert byte-stable repeated evaluation artifacts,
   changed surfaces, repeat counts, held-in-only proposer context, at least one
   accepted proposal, and at least one rejected proposal.
6. Updated README to call the result a paper-faithful toy implementation with
   explicit limitations.

## Stop Condition

P0 changes have landed and tests pass, so the project can claim a paper-faithful
toy implementation of the Self-Harness algorithmic protocol. It should not claim
to reproduce the Terminal-Bench-2.0 experiments until a DeepAgent or
Terminal-Bench-compatible runner exists and is evaluated under fixed protocol.
