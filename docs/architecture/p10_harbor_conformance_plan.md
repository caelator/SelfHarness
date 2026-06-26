# P10 Harbor Conformance Plan

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p10_benchmark_execution_plan.md` and
`docs/architecture/glm_p10_benchmark_execution_convergence.md`.

P10 moves the live Terminal-Bench path closer to real Harbor execution without
claiming benchmark reproduction.

## Implemented

- `HarborCommandSpec` and `build_harbor_run_command()` for documented Harbor
  command construction.
- `AgentAdapter` protocol with `ClaudeCodeAgentAdapter` and
  `DeepAgentAdapter`.
- `HarborRunResult` and versioned structured output parsing.
- Live `HarborRunner` path now uses command construction and output parsing
  rather than return-code-only verifier outcomes.
- `benchmark-report` CLI for paper-style multi-model audit summaries.
- `BenchmarkProvenance` and provenance-completeness validation.
- Invariant rejecting incomplete provenance for `reproduction_claimed=true`.

## Remaining Boundary

The exact Harbor structured output schema and custom DeepAgent config channel
must still be validated with a real provisioned Harbor run. P10 keeps those
parts versioned and explicit so the first real run can refine them without
weakening existing audit guarantees.
