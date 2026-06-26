# Round 1

CONVERGED: YES

## Verdict
The P3 slice for a provider-neutral LLM proposer seam is well-scoped, aligns with paper fidelity requirements, and correctly isolates held-out data. The existing `ProposerContext` and `Proposal` data boundaries are sufficient to support this without requiring engine evaluation changes. The architecture is actionable now with minor refinements to error handling and API stability to ensure no regressions in production stability.

## Critique
- **Failure Mode Handling**: The brief suggests raising an `InvalidProposalError` for invalid model output. However, the engine loop likely expects a `Proposer` to return a list of proposals. If the engine is not explicitly updated to catch this exception (which violates the "no engine changes" constraint), an invalid LLM response could crash the entire harness loop. To preserve production API stability, the `LLMProposer` should gracefully handle invalid JSON or schema violations by returning an empty list `[]` and logging a warning, rather than throwing an unhandled exception.
- **Prompt Safety**: While the code asserts `split == Split.HELD_IN` on inputs, the prompt builder must be strictly constructed to omit any held-out task identifiers or metadata from the context.
- **Op Validation**: The brief correctly mandates using `harness.validate_op`. This is excellent for maintaining bounded editable surfaces, but we must also ensure the `LLMProposer` catches `InvalidPatchError` during parsing and drops those specific proposals rather than failing the whole batch, preserving maximal productivity from a single LLM call.

## Required Changes
1. **Graceful Degradation**: Implement the `LLMProposer` to catch JSON parse errors, schema mismatches, and `InvalidPatchError`s on individual proposals. Drop invalid proposals and return the valid subset (or `[]` if all fail), rather than raising an uncaught exception.
2. **JSON Schema Strictness**: Ensure the JSON schema parsing enforces types strictly (e.g., `payload` must match the expected type for the `op` and `surface` as defined in `harness.py`).
3. **Prompt Isolation**: The prompt builder must only project `held_in_patterns` and `passing_summaries` (filtered by `Split.HELD_IN`) into the prompt text.

## Revised Plan
1. **New File**: Create `self_harness/llm_proposer.py`.
2. **Protocol**: Define an `LLMClient` Protocol with `complete(system_prompt: str, user_prompt: str) -> str`.
3. **Implementation**: Implement `LLMProposer`:
   - Accepts an `LLMClient` instance and an optional `ProposalPolicy`.
   - In `propose(self, context: ProposerContext)`:
     - Assert `context.held_in_patterns` and `context.passing_summaries` contain only `Split.HELD_IN`.
     - Construct a strict system prompt detailing the allowed JSON schema and valid ops/surfaces.
     - Construct a user prompt from the `ProposerContext`.
     - Invoke `client.complete()` and parse the JSON.
     - Map valid JSON objects to `Proposal` instances.
     - Use `try/except` around `harness.validate_op` to filter out invalid ops proposed by the model.
     - Apply byte and proposal count budgets from `ProposerContext`.
     - Apply `ensure_diverse` from `ProposalPolicy`.
     - Return the list of valid `Proposal` objects. Return `[]` if the output is entirely unparseable.
4. **Tests**: 
   - Fake `LLMClient` returning a valid JSON batch -> asserts proposals are parsed, validated, and filtered correctly.
   - Fake `LLMClient` returning malformed JSON -> asserts `[]` is returned (no exceptions raised).
   - Fake `LLMClient` returning valid JSON but invalid ops -> asserts invalid ops are dropped, valid ones are kept.
   - Assert that the generated prompt string does not contain held-out task IDs.

## Remaining Open Questions
- None blocking. The failure behavior has been converged to return `[]` to guarantee engine stability without modifying the engine.

[usage] {"completion_tokens": 2232, "completion_tokens_details": {"reasoning_tokens": 1380}, "prompt_tokens": 6941, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 9173}
