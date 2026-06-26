# P3 LLM Proposer Brief

## Status

GLM convergence completed in `docs/architecture/glm_p3_llm_proposer_plan.md`.
The provider-neutral LLM proposer seam is implemented:

- Added `LLMClient` protocol and `LLMProposer`.
- Builds prompts only from held-in failure patterns, held-in passing summaries,
  prior attempted edits, editable surfaces, and current harness.
- Parses strict JSON proposal batches into validated `Proposal` objects.
- Uses existing patch validation, budget filtering, and proposal policy.
- Drops malformed JSON and invalid proposals safely instead of crashing the
  engine.
- Added fake-client tests for valid output, malformed output, invalid ops,
  budget/diversity, and prompt isolation.

## Current Verified State

The project now has:

- production package foundation and CI checks;
- paper-faithful core protocol;
- richer paper-aligned harness surfaces;
- proposal addressability/diversity policy;
- audit readback APIs;
- local subprocess verifier-backed task adapter.

## Remaining Gap

The paper's Harness Proposal stage invokes the same fixed model in a proposer
role. The current package only includes `HeuristicProposer`, so production users
cannot plug in a model proposer without implementing the whole `Proposer`
protocol themselves.

## Proposed P3 Slice

Add a provider-neutral LLM proposer seam:

1. `LLMClient` protocol:
   - `complete(system_prompt: str, user_prompt: str) -> str`
   - no provider SDK dependency in core package.
2. `LLMProposer`:
   - consumes existing `ProposerContext`;
   - builds a bounded prompt from held-in failure patterns, held-in passing
     summaries, attempted edits, editable surfaces, and current harness;
   - explicitly excludes held-out traces;
   - asks for strict JSON output;
   - parses candidate proposals into existing `Proposal`, `HarnessPatch`, and
     `HarnessOp` dataclasses;
   - validates op names/surfaces with `harness.validate_op`;
   - enforces proposal budget and payload byte budget;
   - applies existing `ProposalPolicy` addressability/diversity filters.
3. JSON response schema:
   - top-level object with `proposals: [...]`;
   - each proposal has `id_suffix`, `pattern_id`, `priority`, `ops`,
     `rationale`, `expected_effect`, `regression_risks`;
   - each op has `op`, `surface`, `payload`.
4. Tests:
   - fake client returns valid JSON and produces proposals;
   - invalid JSON yields no proposals or typed exception? Proposed: no proposals
     with deterministic parser failure metadata is hard because `Proposer`
     returns only proposals. Use `InvalidProposalError` for invalid model output.
   - held-out patterns/summaries are asserted out;
   - unsupported op/surface is rejected;
   - budget filtering works;
   - prompt does not include held-out labels/traces.

## Constraints

- Do not add a real provider SDK in this slice.
- Do not change engine acceptance/evaluation semantics.
- Keep `HeuristicProposer` as default CLI/demo proposer.
- Do not log full prompt/model output into audit artifacts yet; avoid accidental
  leakage and nondeterministic artifacts.

## GLM Question

Converge whether this is the right next step toward production readiness and
paper fidelity. Confirm failure behavior for invalid LLM output and the exact
minimal stable API.
