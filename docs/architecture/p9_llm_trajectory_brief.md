# P9 LLM Proposer And Trajectory Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p9_completion_gate_plan.md` and
`docs/architecture/glm_p9_completion_gate_convergence.md`.

P9 closes an implementable paper-fidelity gap without claiming live
Terminal-Bench reproduction: the repository now has a reference LLM proposer
adapter path, proposer evidence-bundle invariants, and a paper-style audit
trajectory view.

## Implemented

- Optional `AnthropicClaudeClient` adapter behind the `anthropic` extra.
- Typed `LLMClientError` and `LLMRequestError` exceptions.
- LLM proposer evidence rendering for held-in pattern id, support, task ids,
  symptoms, verifier evidence, and inferred mechanism.
- LLM proposer grounding and diversity invalidation with auditable
  `ungrounded_proposal`, `unaddressable_pattern`, and `diversity_collision`
  reasons.
- Engine support for proposer-side invalid reasons.
- `self-harness audit-trajectory` derived JSONL output.
- Trajectory schema `1.0` docs and byte-stability invariant.
- Core-import CI job proving no optional provider/provenance dependency is
  required for `import self_harness`.
- Anthropic adapter mock-only contract tests across supported Python versions.

## Remaining Boundary

P9 makes the Harness Proposal stage usable with a real provider client and makes
paper-style lineage reporting reproducible from audit artifacts. It still does
not run Harbor, execute the full Terminal-Bench-2.0 corpus, or compare the three
paper model backends.
