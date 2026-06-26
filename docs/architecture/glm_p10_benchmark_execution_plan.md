CONVERGED: YES

## Verdict

The P10 plan is implementation-ready. Round 1 identified the material gaps with evidence (CLI command mismatch, missing output parser, incomplete agent adapter, missing benchmark report layer, provenance gaps), and the revised plan addresses each gap with concrete modules, schemas, tests, and acceptance gates that align with the existing repository structure. All remaining open questions are explicitly non-blocking for P10 implementation—they affect first-real-run correctness, which is out of scope, not the protocol-conformance and reporting slice itself.

The plan is appropriately bounded: it does not redefine success as a toy demo, does not claim reproduction, extends the existing `reproduction_claimed=false` invariants, and remains fully testable under the stated constraints (no Harbor, no Docker) using synthetic fixtures and a fake-harbor script.

## Critique

**Strengths confirmed against repository evidence:**
- Existing `src/self_harness/adapters/terminal_bench/` package structure (`runner.py`, `corpus.py`, `preflight.py`, `capture.py`, `agent_render.py`) provides clean seams for the new modules (`harbor_command.py`, `harbor_output.py`, `agent_adapter.py`).
- Audit schema versioning (1.0-1.3) and `reproduction_claimed=false` invariant already exist in `engine.py:validate_benchmark_claims` and `audit.py`; extending these is mechanical.
- The fake-harbor script pattern is already established in `tests/test_terminal_bench_preflight_capture.py:_write_fake_harbor`, so the orchestrator integration test is feasible.
- The dry-run fixture replay pattern in `HarborRunner._dry_run` already handles `terminal_cause`, `causal_status`, `mechanism` fields—the output parser can reuse this shape.

**Material gaps addressed:**
1. CLI command shape: `build_harbor_run_command` will produce the documented `--dataset ... --agent <name> --model <id> --n-concurrent <n>` form.
2. Output parsing: `HarborRunResult` parser with pluggable `HarborOutputFormat` versioning handles the inferred schema without blocking implementation.
3. Agent adapter: `AgentAdapter` protocol with `DeepAgentAdapter` (paper-faithful) and `ClaudeCodeAgentAdapter` (documented) reference implementations.
4. Benchmark report: `BenchmarkReport` schema 1.0 with multi-model comparison matching paper Figure 4 structure.
5. Provenance: invariant rejects `unknown-live` provenance when `reproduction_claimed=true`.

**Scope discipline confirmed:**
- Concurrency orchestration deferred to P11 (Harbor handles via `--n-concurrent`).
- Live path remains best-effort; preflight gate unchanged.
- No claim of reproduction in any default path.

## Required Changes

None blocking. The round 1 plan is implementation-ready as written.

## Revised Plan

Adopt round 1 plan unchanged:

**Modules:** `harbor_command.py`, `harbor_output.py`, `agent_adapter.py`, `reporting/provenance.py`, `reporting/benchmark_report.py`, CLI `benchmark-report` subcommand, readiness gate extension.

**CLI:**
```bash
self-harness benchmark-report \
  --audit-dir minimax:runs/minimax-final \
  --audit-dir qwen:runs/qwen-final \
  --audit-dir glm:runs/glm-final \
  --out runs/benchmark-report.json
```

**Schemas:**
- `HarborCommandSpec`: `{dataset, agent_name, model, n_concurrent, cache_dir?, cloud_env?, task_ids?}`
- `HarborRunResult`: `{task_id, passed, verifier_output, trace_path?, container_digest?, terminal_cause, mechanism}`
- `BenchmarkReport` 1.0: `{schema_version, provenance_per_model, per_model_summary, per_task_breakdown, split_gains, reproduction_claimed:false}`
- `BenchmarkProvenance`: `{model_id, model_version, decoding_config, harbor_version, dataset_version, corpus_hash, container_image_digest, task_split_assignment}`

**Tests:** command builder matches documented examples; output parser handles pass + 2 failure categories; agent adapter renders both named-agent and config-file forms; orchestrator integration with fake-harbor; benchmark report from dry-run audits; provenance invariant rejects incomplete provenance for reproduction claims.

**Acceptance gates:** `make check` and `make readiness` pass on Python 3.11/3.12/3.13; command builder matches ≥2 documented examples; parser handles 3 synthetic fixtures; report generates valid schema 1.0; provenance invariant active; canonical readiness hash updated.

**Stop conditions:** all gates met; no `reproduction_claimed=true` in default paths; live path structurally correct against documented protocol.

## Remaining Open Questions

All non-blocking for P10 implementation:

1. **Harbor output format:** Exact JSON schema inferred; parser is `HarborOutputFormat`-versioned and pluggable. Blocks first-real-run correctness only.
2. **Agent config for `--agent claude-code`:** Path vs name vs inline JSON inferred; `AgentAdapter` supports both. First real run determines canonical form.
3. **Cloud env option shape:** `cloud_env: dict[str, str]` passthrough included; exact keys documented when Harbor docs available.
4. **Concurrency orchestration:** Deferred to P11; Harbor `--n-concurrent` handles parallelism.

These questions can only be resolved with a real Harbor run, which is explicitly out of scope for P10. They do not block implementation, testing, or readiness-gate extension.
