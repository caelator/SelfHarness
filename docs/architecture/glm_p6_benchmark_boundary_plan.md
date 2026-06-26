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
