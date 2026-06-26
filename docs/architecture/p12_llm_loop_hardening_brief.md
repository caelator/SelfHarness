# P12 LLM Loop Hardening Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p12_production_readiness_plan.md`.

## Purpose

P12 closes the gap between a provider-neutral LLM proposer seam and a
production-checkable engine loop. Earlier slices proved `LLMProposer` parsing and
provider adapter contracts in isolation. This slice proves that LLM-shaped
proposals can drive a full `SelfHarnessEngine` run, produce deterministic audit
artifacts, and preserve the paper's held-in-only proposer boundary.

This remains a local protocol hardening slice. It does not validate a live model
provider, does not run Terminal-Bench, and does not make a reproduction claim.

## Implemented

- `self_harness.testing.MockLLMClient`, a deterministic `LLMClient`
  implementation for engine-loop tests and downstream adapter authors.
- Explicit LLM prompt rendering via `render_llm_proposer_prompts`, which filters
  rendered evidence to held-in failure patterns and held-in passing summaries.
- End-to-end engine-loop tests using `LLMProposer(MockLLMClient(...))`.
- A canonical LLM audit hash fixture covering a mock-driven audit plus derived
  trajectory bytes.
- Paper-fidelity invariants proving that:
  - rendered LLM proposer prompts do not include held-out decoys;
  - ungrounded LLM pattern IDs are audited as invalid and never promoted;
  - the LLM proposer path cannot bypass the Terminal-Bench no-reproduction gate.

## Deferred

- Live provider end-to-end validation with a real API key.
- Nondeterministic model-output robustness studies.
- Real Terminal-Bench/Harbor reproduction on a provisioned host.
- Any `reproduction_claimed=true` path.

## Schema

No audit schema bump. P12 reuses audit schema `1.4` and trajectory schema `1.0`.
