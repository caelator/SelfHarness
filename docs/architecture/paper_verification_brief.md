# Paper Verification Brief

Source: `docs/source/self_harness_2606_09498.pdf`, extracted to
`docs/source/self_harness_2606_09498.txt`.

## Paper Ground Truth

The paper defines Self-Harness as an iterative loop over a fixed model, fixed
evaluator, initial harness, held-in split, held-out split, proposal width, and
round budget. Each round:

1. evaluates the current harness on held-in and held-out tasks;
2. builds an evidence bundle from held-in verifier-grounded failures;
3. invokes the same fixed model in proposer role to generate bounded candidate
   harness edits;
4. evaluates each candidate on both splits under the same evaluator;
5. accepts only candidates where held-in delta and held-out delta are both
   non-negative and at least one is strictly positive;
6. merges compatible accepted edits, while rejected candidates are logged.

The paper's failure signature is `(terminal verifier-level cause, causal status
of relevant agent behavior, abstract agent mechanism)`. Clustering is exact over
that signature, not semantic similarity. The evidence bundle should include
cluster size, representative task instances, shared trace symptoms, verifier
evidence, and inferred agent mechanism.

The proposer context in the paper includes editable surfaces of the current
harness, verifier-grounded failure patterns, records of passing behaviors that
should be preserved, and summaries of previously attempted edits. Proposals must
be diverse, minimal, grounded in a primary failure mechanism, mapped to a
concrete editable surface, and include an audit record with targeted failure
pattern, edited surface, expected behavioral effect, and regression risks.

The validation protocol evaluates current and candidate harnesses on held-in and
held-out splits. Held-out traces are never exposed to the proposer. The paper
uses two repeated attempts per candidate unless otherwise specified and applies
the same acceptance rule to aggregate pass counts when evaluation is stochastic.
Validation also rejects candidates that do not modify any editable surface or
fail before producing a valid evaluation result. Audit records include changed
surfaces, split-wise outcomes, evaluation repeats, proposal summary, and
accept/reject decision.

The experiment instantiates the loop on Terminal-Bench-2.0 with a minimal
DeepAgent-based harness. The initial harness has declared editable surfaces such
as bootstrap instruction, execution instruction, verification instruction,
failure-recovery instruction, runtime control policy, tools, skills, memory
sources, and subagents. The paper reports retained edits around early artifact
creation, schema/tool content handling, loop breaking, dependency prechecking,
tool-error recovery, environment persistence, and moving from exploration to
implementation/testing.

## Current MVP Alignment

Implemented and aligned:

- fixed harness object with declared editable surfaces in `self_harness/harness.py`;
- held-in and held-out task split model in `self_harness/types.py`;
- verifier-grounded failure signatures and exact clustering in
  `self_harness/mining.py`;
- proposer API that receives held-in patterns only in `self_harness/proposer.py`;
- bounded patch DSL over whitelisted harness surfaces;
- proposal audit metadata for targeted pattern, changed surface, expected effect,
  and regression risks;
- candidate evaluation on both splits and the strict non-regression acceptance
  rule in `self_harness/evaluation.py`;
- merge of compatible accepted edits plus re-validation before commit in
  `self_harness/engine.py`;
- deterministic audit artifacts for manifest, lineage, harness snapshots,
  proposals, and evaluations;
- toy failure modes matching paper examples: missing artifacts, repeated failed
  commands, late verification, environment persistence, and held-out regression.

## Paper Gaps To Address

P0 for a paper-faithful next plan:

- Add evaluation repeat support and aggregate pass-count validation. The paper
  uses two repeated attempts per candidate unless otherwise specified and calls
  out stochastic evaluation handling.
- Extend the proposer context object beyond held-in patterns to include passing
  behavior summaries and previously attempted edits, while still excluding
  held-out traces.
- Record evaluation repeat count and changed surfaces explicitly in proposal and
  evaluation audit rows.

P1 for a more faithful implementation:

- Add richer editable harness surfaces for `tools`, `skills`, `memory_sources`,
  and `subagents`, even if the MVP keeps them inert in the toy runner.
- Add addressability filtering so non-actionable or weakly supported clusters
  can be skipped explicitly instead of merely producing no proposal.
- Add explicit proposal diversity checks, not only deterministic heuristic
  variety.
- Add a real subprocess/Terminal-Bench-style runner seam after the toy runner,
  while keeping the model/evaluator/tool budget fixed per run.

P2 / later:

- Add DeepAgent or A-Evolve adapter experiments only after the core algorithmic
  invariants are represented locally.
- Add branch/lineage visualizations similar to the paper's accepted/rejected
  trajectory plots.

## Convergence Question For GLM

Converge a paper-faithful build plan from this state. Decide whether the current
MVP is acceptable as a toy demonstration, identify must-fix gaps before calling
the implementation paper-faithful, and produce a prioritized next-step plan that
preserves held-out isolation, bounded edits, auditability, and fixed-protocol
validation.

## Fidelity Audit Follow-ups (closed)

A claim-by-claim audit against the paper (Algorithm 1, §3.2–3.4, §4.1, Figure 3)
identified and closed the following divergences/gaps:

- **Fixed-T loop (Algorithm 1 lines 18-23).** The engine no longer breaks early
  when a round accepts nothing; it carries the harness forward (`h_{t+1}=h_t`)
  and continues all `T` rounds, so a later round can still surface an accepted
  edit. Locked by `test_loop_runs_all_rounds_and_does_not_break_after_an_empty_round`.
- **Disjoint split partition (§4.1).** `SelfHarnessEngine` now rejects a task id
  that appears in both held-in and held-out via `validate_split_partition`
  (raising `PaperFidelityError`), enforcing the partition precondition for
  held-out isolation on the path that runs the loop. Locked by
  `test_engine_rejects_overlapping_split_partition`.
- **Figure 3 initial harness.** `initial_harness()` now uses the paper's verbatim
  instruction strings and the `runtime_control_policy` schema
  (`enabled` / `max_recent_tool_errors` / `max_total_tool_messages` /
  `instruction`), so the runtime-policy surface can express the paper's
  MiniMax edit. Canonical audit-hash fixtures regenerated.
- **Proposal diversity (§3.3).** `ProposalPolicy.require_distinct_surfaces`
  defaults to `True`, so the heuristic proposer drops exact-duplicate proposals
  on the default path while still allowing genuinely distinct hypotheses to reach
  the validation gate.
- **Actionability-aware cluster ordering (§3.2).** `cluster_failures` accepts the
  editable surfaces and orders clusters by support and estimated actionability
  (addressable mechanisms first), matching "ordered by their support and estimated
  actionability". Locked by
  `test_actionability_ranks_addressable_mechanism_ahead_of_equal_support`.
