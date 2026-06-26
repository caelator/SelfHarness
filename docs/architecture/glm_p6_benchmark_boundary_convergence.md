# Round 1

CONVERGED: NO

## Verdict

The most valuable P6 slice is a **Terminal-Bench / Harbor adapter scaffold** that moves the project from "paper-faithful local toy" to "can speak the real benchmark protocol", while explicitly *not* claiming reproduction. This is the highest-leverage fidelity gap remaining: without it, no amount of corpus signing, cross-Python CI, or richer verifier taxonomies makes the harness benchmark-environment-capable. The first round is not yet converged because two material decisions (agent-surface mapping policy, Harbor availability strategy) must be pinned down before execution, and the plan needs scope-splitting so the adapter can land without depending on a flaky external runtime.

Evidence (verified): TB2 protocol is `harbor run --dataset terminal-bench@2.0 --agent <agent>` with Harbor downloading tasks and running containers; legacy TB tasks carry `(instruction, verifier/test script, oracle solution)`. Inference: the Self-Harness editable surfaces (system/bootstrap/execution/verification/failure-recovery/runtime-policy/tools/skills/memory/subagents) map cleanly onto TB legacy fields and onto a Harbor agent config, but the exact mapping is a design decision, not a discovered fact.

## Critique

- An adapter that *requires* Harbor to be installed will rot in CI. It must have an offline/dry-run path with a tiny in-repo fake Harbor stub or recorded fixture.
- "Adapter" is two different things bundled together: (a) corpus/task ingestion (TB2 dataset → `TaskCorpus`) and (b) execution backend (`Runner` over containers). Conflating them will leak Harbor concerns into the corpus layer. Split them.
- Audit provenance must record benchmark lineage (dataset id/version, Harbor version, container image digest, per-task source hash). Otherwise the project cannot distinguish "we ran TB2" from "we ran something TB2-shaped". Schema-minor bump required, plus changelog entry per existing policy.
- Reproduction-claim hygiene must be enforced mechanically, not just in README: a readiness invariant that fails if any audit directory containing `benchmark_protocol="terminal-bench@2.0"` is labeled `reproduction=true`. Without this, drift will reintroduce false claims.
- The Self-Harness proposer edits *harness text*, but TB2's `--agent` is a runtime plug-in. Without a stated policy for how an accepted harness patch materializes into the next round's `--agent` config, the loop is not actually closed. This is the one genuinely blocking design decision.
- Defer corpus signing, non-subprocess verifier taxonomies, cross-Python matrix, and migration shims to P7+. They are real but lower-leverage and would dilute the slice.

## Required Changes

1. Split P6 into two adapters behind one slice:
   - `TerminalBenchCorpusAdapter` (ingestion only): TB2/legacy manifest → versioned `TaskCorpus`, with `failure_mode="terminal_bench"`, no execution.
   - `HarborRunner` (execution only): implements `Runner`, invokes `harbor run` per attempt with fresh container, returns `RunRecord` with verifier-grounded terminal cause / causal status / mechanism preserved.
2. Add an offline execution mode (`HarborRunner(mode="dry-run")` plus a stub `harbor` script under `tests/fixtures/`) so CI does not depend on Harbor being installed.
3. Pin the agent-mapping policy decision: an accepted harness patch is materialized by writing the next round's `--agent` config from a render of the editable surfaces (text surfaces become the agent's instruction stack; tools/skills/memory/subagents become agent config blocks). No DeepAgent dependency; agent config is a renderable artifact under `adapters/terminal_bench/agent_render.py`.
4. Extend audit schema to `1.3` (minor) with `benchmark_protocol`, `benchmark_dataset_version`, `harbor_version`, `container_image_digest`, `task_source_hash` per task. Add changelog entry; do not rotate canonical hash for the toy demo (benchmark fields are null in toy mode).
5. Add a readiness invariant forbidding `benchmark_protocol="terminal-bench@2.0"` from co-occurring with `reproduction_claimed=true`. Audit summary API must surface this.
6. Gate the adapter behind an optional extra (`pip install -e '.[terminal-bench]'`) so the core package keeps its zero-external-SDK contract.
7. Mark all new modules `experimental` in docstrings and in the schema changelog; README "Production Status" must restate "not a reproduction".

## Revised Plan

**P6 — Terminal-Bench / Harbor adapter scaffold (experimental, no reproduction claim)**

Goal: make the harness capable of speaking the real TB2 protocol end-to-end in a controlled, audited, offline-testable way, while remaining explicitly not-a-reproduction.

Scope:
- `src/self_harness/adapters/terminal_bench/__init__.py` (experimental)
- `src/self_harness/adapters/terminal_bench/corpus.py`
  - `TerminalBenchCorpusAdapter(TaskAdapter)` ingests TB2 dataset manifest (path or dataset id) into a versioned `TaskCorpus`.
  - Preserves `(instruction, verifier_script, oracle_solution)` as task metadata under stable keys.
  - Records per-task source hash; emits `TaskLoadReason` values for missing/malformed manifests.
- `src/self_harness/adapters/terminal_bench/runner.py`
  - `HarborRunner(Runner)` with `mode ∈ {"live","dry-run"}`.
  - Live mode shells out to `harbor run --dataset <id> --agent <rendered-config> --task <id>` per attempt; parses verifier exit into `VerifierOutcome` using the existing closed taxonomy; fresh container per attempt.
  - Dry-run mode reads a recorded fixture directory and replays verifier outcomes deterministically.
- `src/self_harness/adapters/terminal_bench/agent_render.py`
  - Pure function `render_agent_config(harness: HarnessSpec) -> dict` mapping editable surfaces to a Harbor-compatible agent config.
  - Documented mapping policy; no DeepAgent dependency.
- `src/self_harness/adapters/terminal_bench/provenance.py`
  - Helpers to compute and verify dataset/container/task source digests.
- CLI:
  - `self-harness terminal-bench --dataset terminal-bench@2.0 --corpus-cache <dir> --mode live|dry-run --rounds N --out runs/tb2`.
  - Requires `[terminal-bench]` extra in live mode; dry-run works with core install.
- Audit schema `1.3` (additive):
  - manifest: `benchmark_protocol`, `benchmark_dataset_version`, `harbor_version`, `container_image_digest`.
  - evaluation rows: `task_source_hash`.
  - Schema changelog entry; canonical toy hash unchanged.
- Readiness:
  - New invariant: no audit run may carry `benchmark_protocol="terminal-bench@2.0"` and `reproduction_claimed=true`.
  - New invariant: dry-run TB2 smoke run is byte-stable under ambient env changes (reuses existing determinism harness).
- Tests:
  - corpus adapter ingests a fixture TB2 manifest and produces the expected `TaskCorpus`.
  - dry-run `HarborRunner` produces deterministic `RunRecord`s with verifier-grounded taxonomy.
  - agent render is a pure function of `HarnessSpec` and is stable.
  - schema 1.3 fields round-trip through `load_audit_run`.
  - reproduction-claim invariant trips on a crafted bad manifest.
- Docs:
  - README: new "Experimental Terminal-Bench integration" subsection; explicit non-reproduction statement.
  - `docs/architecture/p6_terminal_bench_adapter_brief.md`.
  - Optional-extra declared in `pyproject.toml`.

Acceptance criteria:
- `make check && make readiness` pass with no canonical-hash rotation.
- `self-harness terminal-bench --mode dry-run ...` runs end-to-end against a fixture dataset and emits a schema-1.3 audit directory with `benchmark_protocol` populated.
- No new hard runtime dependency on Harbor in the core install.
- No file in the repo claims Terminal-Bench reproduction; the new readiness invariant enforces this in CI.
- One round of the Self-Harness loop in dry-run mode consumes an accepted harness patch and renders a new agent config for the next round, proving the loop is closed.

Out of scope (deferred):
- Real DeepAgent / A-Evolve agent wiring.
- Live Harbor execution in CI.
- Corpus signing (sigstore or similar) — P7.
- Cross-Python canonical-hash matrix — P7.
- Non-subprocess verifier adapters beyond what TB2 needs — P8.
- Major-version audit migration shims — only when a breaking schema change is proposed.
- Reproduction runs against the full published TB2 task set.

## Remaining Open Questions

1. **Agent-mapping policy** (blocking): is rendering the harness's text surfaces as a Harbor agent's instruction stack, and emitting tools/skills/memory/subagents as agent-config blocks, acceptable as the P6 contract? This decides whether the loop is "closed" or merely "observable". My recommended answer is yes (above), but it is a design commitment, not a discovery.
2. **Harbor CLI output contract** (blocking for *live* mode, non-blocking for the slice): exact stdout/exit-code/schema Harbor returns for a verifier outcome. The slice can ship with dry-run only and document live mode as best-effort until a Harbor run is captured. Decide whether P6 must include at least one captured live run, or whether dry-run + documented live path is sufficient for this round.
3. **Dataset caching layout** (non-blocking): whether `--corpus-cache` mirrors Harbor's own cache or is a Self-Harness-owned mirror. Recommend Self-Harness-owned for auditability; confirm in round 2.
4. **Reproduction-claim enforcement surface** (non-blocking): invariant over manifest fields only, or also over a `CLAIMS.md` file scan? Recommend manifest-only for P6.

[usage] {"completion_tokens": 2964, "completion_tokens_details": {"reasoning_tokens": 830}, "prompt_tokens": 17264, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 20228}

---

# Round 2

CONVERGED: YES

## Verdict

Round 1 produced a sound, executable P6 slice that moves the project from "paper-faithful toy" to "speaks the real TB2 protocol without claiming reproduction." The remaining open questions from round 1 are all design commitments within the architect's authority to pin, not evidence gaps requiring external input. I am pinning them now and converging. The slice is ready to execute.

Evidence (verified from repo): `adapters/base.py` defines a clean `TaskAdapter` protocol (`load() -> list[Task]`, `runner() -> Runner`) that the new `TerminalBenchCorpusAdapter` will fit. `corpus.py` already supports `corpus_version`/`corpus_id` and checksum verification, which the provenance helpers will extend. `evaluation.py`'s `Runner` protocol is narrow (`run(task, harness, attempt_index) -> RunRecord`), so `HarborRunner` slots in without engine changes. `RELEASE.md` confirms schema-minor bumps are permitted for additive fields with a changelog entry. `readiness.py`'s `audit_tree_hash` is already environment-stable, so the new determinism invariant can reuse it.

Inference: the agent-mapping policy is a render contract, not a discovered fact. Rendering editable text surfaces to the Harbor agent's instruction stack and emitting tools/skills/memory/subagents as agent-config blocks is the minimal, reversible contract that closes the loop without coupling to DeepAgent. No repo fact contradicts it.

## Critique

- Round 1 correctly identified that the adapter is two concerns (corpus ingestion vs. execution backend) and must be split. The revised plan already does this.
- Round 1 correctly required an offline dry-run path. The revised plan already includes it.
- Round 1 correctly required mechanical enforcement of the no-reproduction claim. The revised plan already includes the readiness invariant.
- Round 1's one genuinely blocking design decision (agent-mapping policy) is now pinned below. No further planning can de-risk it; only execution will.
- The Harbor CLI output contract remains unknown, but it only blocks *live* mode, which is explicitly out of scope for the slice's acceptance criteria (dry-run end-to-end + documented live path). This is acceptable.
- Schema 1.3 is additive; canonical toy hash stays unchanged because benchmark fields are null in toy mode. Consistent with `RELEASE.md`.

No material risks remain unaddressed at the planning level. Execution risks (Harbor output parsing, fixture fidelity) are inherent to the slice and will be handled in implementation.

## Required Changes

All round 1 required changes are accepted as written. Round 2 adds only the pinned resolutions to the open questions:

1. **Agent-mapping policy (PINNED):** `render_agent_config(harness: HarnessSpec) -> dict` is the P6 contract. Text surfaces (`system_prompt`, `bootstrap`, `execution`, `verification`, `failure_recovery`) render to the agent's ordered instruction stack. `runtime_policy` renders to agent runtime controls. `tools`, `skills`, `memory_sources`, `subagents` render to agent config blocks of the same names. No DeepAgent dependency. The render is a pure function and is tested for stability. This closes the loop: an accepted harness patch in round N materializes as the `--agent` config in round N+1.
2. **Harbor CLI output contract (DEFERRED):** P6 ships dry-run only for acceptance. Live mode is implemented but marked best-effort and is not in acceptance criteria. A captured live run is P7.
3. **Dataset cache layout (PINNED):** `--corpus-cache` is Self-Harness-owned, not a mirror of Harbor's cache. Rationale: auditability and checksum control.
4. **Reproduction-claim enforcement (PINNED):** Manifest-field invariant only for P6. `CLAIMS.md` scan is P7 if needed.

## Revised Plan

**P6 — Terminal-Bench / Harbor adapter scaffold (experimental, no reproduction claim)**

Scope (unchanged from round 1):
- `src/self_harness/adapters/terminal_bench/__init__.py` (experimental)
- `corpus.py` — `TerminalBenchCorpusAdapter(TaskAdapter)`: ingests TB2/legacy manifest into versioned `TaskCorpus`, preserves `(instruction, verifier_script, oracle_solution)` as task metadata, records per-task source hash.
- `runner.py` — `HarborRunner(Runner)` with `mode ∈ {"live","dry-run"}`. Live shells out to `harbor run`; dry-run replays a recorded fixture. Fresh container per attempt. Verifier outcome mapped to existing closed taxonomy.
- `agent_render.py` — pure `render_agent_config(harness) -> dict` with pinned mapping policy above.
- `provenance.py` — dataset/container/task source digest helpers.
- CLI: `self-harness terminal-bench --dataset terminal-bench@2.0 --corpus-cache <dir> --mode live|dry-run --rounds N --out runs/tb2`. Requires `[terminal-bench]` extra in live mode.
- Audit schema `1.3` (additive): manifest gains `benchmark_protocol`, `benchmark_dataset_version`, `harbor_version`, `container_image_digest`; evaluation rows gain `task_source_hash`. Changelog entry required. Canonical toy hash unchanged.
- Readiness invariants: (a) no audit run with `benchmark_protocol="terminal-bench@2.0"` may have `reproduction_claimed=true`; (b) dry-run TB2 smoke run is byte-stable under ambient env changes.
- Tests: corpus ingestion from fixture, dry-run determinism, agent render purity/stability, schema 1.3 round-trip, reproduction-claim invariant trips on crafted bad manifest.
- Docs: README "Experimental Terminal-Bench integration" subsection restating non-reproduction; `docs/architecture/p6_terminal_bench_adapter_brief.md`; `[terminal-bench]` extra in `pyproject.toml`.

Acceptance criteria (unchanged):
- `make check && make readiness` pass with no canonical-hash rotation.
- `self-harness terminal-bench --mode dry-run ...` runs end-to-end against a fixture dataset and emits a schema-1.3 audit directory with `benchmark_protocol` populated.
- No new hard runtime dependency on Harbor in the core install.
- No file in the repo claims Terminal-Bench reproduction; readiness invariant enforces this in CI.
- One round of the Self-Harness loop in dry-run mode consumes an accepted harness patch and renders a new agent config for the next round.

Out of scope (deferred, unchanged):
- Real DeepAgent / A-Evolve agent wiring.
- Live Harbor execution in CI.
- Corpus signing — P7.
- Cross-Python canonical-hash matrix — P7.
- Non-subprocess verifier adapters beyond TB2 needs — P8.
- Major-version audit migration shims.
- Reproduction runs against the full published TB2 task set.
- Captured live Harbor run — P7.

## Remaining Open Questions

None blocking. All round 1 questions are pinned above. Execution-time discoveries (e.g., Harbor's actual stdout schema when a live run is first captured) are P7 concerns and do not gate this slice.

[usage] {"completion_tokens": 2090, "completion_tokens_details": {"reasoning_tokens": 553}, "prompt_tokens": 21557, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 23647}
