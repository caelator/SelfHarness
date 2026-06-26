# Round 1

CONVERGED: NO

## Verdict

P10 is architecturally tractable as a single coherent slice focused on **Harbor protocol conformance** and **paper-style benchmark reporting**. The current repository has strong scaffolding (P0-P9) but the live Harbor execution path is structurally incomplete: the `HarborRunner._live_run` invocation (`harbor run --agent <config_path> --task <task_id>`) does not match the documented Harbor CLI shape (`harbor run --dataset <dataset> --agent <agent> --model <model> --n-concurrent <n>`), and there is no parser for Harbor's structured verifier output. Without these, the live path cannot produce paper-faithful audit artifacts even when Harbor/Docker become available.

The proposed slice is implementable and fully testable under the stated constraints (no Harbor, no Docker) using synthetic fixtures and a fake-harbor script. It does not claim reproduction and explicitly extends the existing `reproduction_claimed=false` invariants. The plan is implementation-ready after one revision pass on scope boundaries.

## Critique

**Strengths of current state (evidence):**
- P6-P9 established the Terminal-Bench manifest ingestion, dry-run fixture replay, preflight gate, capture scaffolding, and provenance hashing. The readiness gate already enforces `reproduction_claimed=false` for `benchmark_protocol="terminal-bench@2.0"`.
- The audit schema is versioned (1.0-1.3) with stable JSONL output and canonical hash fixtures.
- `EngineConfig`, `TaskAdapter` protocol, and `Runner` protocol provide clean seams for new adapters.

**Material gaps for real Harbor readiness (evidence + inference):**
1. **CLI command mismatch (evidence):** `runner.py:_live_run` builds `harbor run --dataset ... --agent <path> --task <id> --n-concurrent 1`. Official docs (per task statement) use `harbor run --dataset terminal-bench@2.0 --agent claude-code --model anthropic/claude-opus-4-1 --n-concurrent 4`. The `--model`, `--agent <name>` (not path), and cloud env options are absent.
2. **Output parsing absent (inference):** `_live_run` only checks `completed.returncode`. Paper requires verifier-grounded `terminal_cause`, `causal_status`, `mechanism` per task. Harbor emits structured verifier output that is currently discarded.
3. **Agent adapter incomplete (evidence):** `agent_render.py` renders a config hash but there is no `AgentAdapter` protocol translating `HarnessSpec` into a Harbor-recognized agent (`claude-code`, `deepagent`, etc.).
4. **No benchmark report layer (evidence):** `audit-trajectory` produces per-run evolution rows but there is no multi-model comparison report matching paper Figure 4 structure (initial vs final across MiniMax/Qwen/GLM with relative gains).
5. **Provenance gaps (evidence):** `harbor_version` and `container_image_digest` are hardcoded to `"unknown-live"`. Paper-style reporting requires concrete provenance; the schema should reject `unknown-live` when `reproduction_claimed` is ever flipped to true.

**Scope concern:** The initial draft included concurrency orchestration. This is deferred—Harbor handles concurrency via `--n-concurrent`; the orchestrator only needs to pass the flag correctly. This keeps P10 focused on protocol conformance and reporting.

## Required Changes

1. **Add `HarborCommandSpec` and `build_harbor_run_command`** producing the documented CLI shape including `--model`, named `--agent`, `--n-concurrent`, and optional cloud env vars.
2. **Add `HarborRunResult` parser** for Harbor's structured output with failure-category mapping. Design parser as pluggable (`HarborOutputFormat` versioned) since exact output schema is inferred, not documented in provided context.
3. **Add `AgentAdapter` protocol** with `DeepAgentAdapter` (paper-faithful) and `ClaudeCodeAgentAdapter` (documented `--agent claude-code` path) reference implementations.
4. **Refactor `HarborRunner._live_run`** to use `build_harbor_run_command` + `parse_harbor_output` instead of raw subprocess + exit-code-only logic.
5. **Add `BenchmarkReport` schema and generator** supporting multi-model comparison from multiple audit directories, with explicit `reproduction_claimed` provenance validation.
6. **Add `self-harness benchmark-report` CLI** writing versioned report JSON.
7. **Extend readiness gate** with benchmark-report schema stability and provenance-completeness invariants.
8. **Add synthetic Harbor fixtures** (fake-harbor scripts emitting structured output) for integration tests.
9. **Document Harbor protocol conformance** assumptions and the pluggable output-format seam.

## Revised Plan

### P10: Harbor Protocol Conformance & Paper-Style Benchmark Reporting

**Modules to add/modify:**

| Path | Purpose |
|---|---|
| `src/self_harness/adapters/terminal_bench/harbor_command.py` | `HarborCommandSpec`, `build_harbor_run_command()`, `HARBOR_PROTOCOL_VERSION="2.0"` |
| `src/self_harness/adapters/terminal_bench/harbor_output.py` | `HarborRunResult`, `HarborOutputFormat`, `parse_harbor_output()`, `map_harbor_failure_category()` |
| `src/self_harness/adapters/terminal_bench/agent_adapter.py` | `AgentAdapter` protocol, `DeepAgentAdapter`, `ClaudeCodeAgentAdapter` |
| `src/self_harness/adapters/terminal_bench/runner.py` | Refactor `_live_run` to use command builder + output parser |
| `src/self_harness/reporting/__init__.py` | New package |
| `src/self_harness/reporting/provenance.py` | `BenchmarkProvenance`, `provenance_from_manifest()`, `validate_provenance_completeness()` |
| `src/self_harness/reporting/benchmark_report.py` | `BenchmarkReport` (schema `1.0`), `build_benchmark_report()`, `write_benchmark_report()` |
| `src/self_harness/cli.py` | Add `benchmark-report` subcommand |
| `src/self_harness/readiness.py` | Extend canonical hash coverage; add provenance invariant |

**CLI surface:**
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
- `BenchmarkReport` schema `1.0`: `{schema_version, provenance_per_model, per_model_summary, per_task_breakdown, split_gains, reproduction_claimed:false}`
- `BenchmarkProvenance`: `{model_id, model_version, decoding_config, harbor_version, dataset_version, corpus_hash, container_image_digest, task_split_assignment}`

**Tests:**
- `tests/test_harbor_command.py`: assert command equals `["harbor","run","--dataset","terminal-bench@2.0","--agent","claude-code","--model","anthropic/claude-opus-4-1","--n-concurrent","4"]` for documented example
- `tests/test_harbor_output.py`: parse 3 synthetic fixtures (pass, missing-artifact, timeout)
- `tests/test_agent_adapter.py`: render `HarnessSpec` → DeepAgent config; render → claude-code agent name
- `tests/test_orchestrator_integration.py`: fake-harbor script emits structured JSON; orchestrator produces `HarborRunResult` with correct `terminal_cause`
- `tests/test_benchmark_report.py`: build report from 2 dry-run audits; validate schema; assert `reproduction_claimed=false`
- `tests/invariants/test_provenance.py`: reject `harbor_version="unknown-live"` when `reproduction_claimed=true`

**Acceptance gates:**
- `make check` and `make readiness` pass across Python 3.11/3.12/3.13
- Harbor command builder matches ≥2 documented CLI examples exactly
- Harbor output parser handles pass + 2 failure categories from synthetic fixtures
- Benchmark report generates valid schema `1.0` from dry-run audits
- Provenance invariant rejects incomplete provenance for reproduction claims
- Canonical readiness hash updated and stable

**Stop conditions:**
- All acceptance gates met
- No `reproduction_claimed=true` in any default code path or fixture
- Harbor live path remains best-effort (preflight gate unchanged) but is now structurally correct against documented protocol

**Docs:**
- `docs/architecture/p10_harbor_conformance_plan.md`
- `docs/architecture/harbor_protocol_assumptions.md` (explicitly labels inferred vs documented protocol details)
- `docs/architecture/benchmark_report_schema.md`
- Update `README.md` with `benchmark-report` usage and protocol-conformance status

## Remaining Open Questions

1. **Harbor output format (inference, non-blocking):** Exact JSON/JSONL schema of Harbor's verifier output is inferred. The parser is designed as `HarborOutputFormat`-versioned and pluggable so the default format can be refined when a real Harbor run is captured. This does not block implementation; it blocks first-real-run correctness, which is explicitly out of scope for P10.

2. **Agent config schema for `--agent claude-code` (inference, non-blocking):** Whether Harbor expects a file path, a registered name, or inline JSON for custom agents is inferred from the documented `--agent claude-code` usage. The `AgentAdapter` protocol supports both named-agent and config-file paths. First real run will determine which adapter is canonical.

3. **Cloud env option shape (inference, non-blocking):** "Cloud env options" are mentioned in the task statement but not specified. `HarborCommandSpec` includes an optional `cloud_env: dict[str, str]` passthrough; exact keys to be documented when Harbor docs are available.

4. **Scope of concurrency orchestration (decision, non-blocking):** Deferred to P11. Harbor's `--n-concurrent` handles parallelism; P10 only needs to pass the flag. A Python-side orchestrator for multi-task batching without Harbor is not required for paper-faithful live runs.

[usage] {"completion_tokens": 6113, "completion_tokens_details": {"reasoning_tokens": 3813}, "prompt_tokens": 41186, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 47299}

---

# Round 2

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

[usage] {"completion_tokens": 2196, "completion_tokens_details": {"reasoning_tokens": 1010}, "prompt_tokens": 45814, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 48010}
