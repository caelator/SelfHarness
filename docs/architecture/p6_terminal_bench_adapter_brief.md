# P6 Terminal-Bench Adapter Boundary Brief

## Status

GLM convergence completed in
`docs/architecture/glm_p6_benchmark_boundary_plan.md`. P6 is implemented as an
experimental adapter scaffold, not a benchmark reproduction.

Implemented:

- `self_harness.adapters.terminal_bench` experimental package;
- manifest ingestion into `TaskCorpus` with per-task source hashes;
- pure `render_agent_config(HarnessSpec)` mapping editable harness surfaces to
  an agent configuration;
- `HarborRunner` with deterministic `dry-run` mode and best-effort live mode;
- `self-harness terminal-bench` CLI subcommand;
- schema `1.3` support for optional benchmark provenance fields;
- evaluation-row `task_source_hash` support;
- readiness invariant preventing `terminal-bench@2.0` audits from claiming
  reproduction;
- dry-run determinism invariant under ambient environment changes;
- fixture dataset under `tests/fixtures/terminal_bench`.

## Production Meaning

The project can now run a Terminal-Bench-shaped dry-run through the same
Self-Harness loop used by the toy runner. The dry-run fixture fails a held-in
task until the rendered agent config contains an accepted harness edit, proving
that the adapter boundary closes the improvement loop.

## Remaining Limits

P6 does not execute the full published benchmark, does not integrate DeepAgent
or A-Evolve, and does not validate live Harbor output parsing against a captured
run. Those are future work before any reproduction claim is allowed.
