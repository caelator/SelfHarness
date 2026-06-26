CONVERGED: YES

## Verdict

Round 2 closes the loop. The three blocking-looking items (D1 naming guardrail, D2 closed verifier taxonomy, D3 schema policy) are product-shape decisions that have a single defensible architecturally-recommended default each, and the convergence rules permit the architect to adopt those defaults rather than block on a missing owner. The plan is internally consistent, layering is correct (corpus → adapter → classifier), and every remaining open question has a sound default inferred from existing repo evidence (e.g. `local_subprocess.py` already distinguishes `verifier-pass`/`verifier-fail`/`timeout`, confirming the proposed taxonomy closure). The plan is ready to execute; owner ratification of D1/D2/D3 can run in parallel with P4.1 without blocking the foundation work.

## Critique

- **D1 (naming guardrail) is safe to adopt as default.** README already states explicitly "This project does not reproduce the paper's Terminal-Bench-2.0 experiments" and lists Docker/Harbor/DeepAgent as absent. The neutral-name rule is the architecturally correct operationalization of that existing posture. No evidence in repo contradicts it.
- **D2 (closed taxonomy) maps cleanly onto existing code.** `LocalSubprocessRunner` already emits exactly three `terminal_cause` values: `verifier-pass`, `verifier-fail`, `timeout` (see `_timeout_record` and the verify outcome construction). Extending to `{verifier-pass, verifier-fail, timeout, missing-artifact, assertion-fail, environment-error}` is an additive closure, not a rewrite; existing audit artifacts remain readable because `audit.py` only validates `schema_version`, not `terminal_cause` values.
- **D3 (schema policy) is non-blocking by design** and the repo already has the precondition: `SUPPORTED_SCHEMA_VERSIONS = {"1.0","1.1"}` in `audit.py` plus `LineageRecord.schema_version = "1.1"` default. A changelog doc is purely additive.
- **Layering is correct.** Corpus stabilization (P4.1) is genuinely the foundation: `load_tasks_json` currently lives in `adapters/local_subprocess.py` and couples loading to a specific adapter. Promoting it to `self_harness.corpus` with a `TaskCorpus` schema is the right seam before introducing `TaskAdapter`.
- **FailureSignature/stable_id risk is real but bounded.** `FailureSignature.stable_id` is derived from `terminal_cause|causal_status|mechanism`. Adding new categories will produce new stable_ids but will not collide with existing ones; existing audit artifacts are unaffected because mining happens at runtime, not on disk.
- **One real residual risk:** if D1 is *rejected* by the owner mid-slice, P4.2 README work must be redone. Mitigation: do P4.1 (corpus) first, which is naming-neutral and survives either D1 outcome; start P4.2 README/adapter naming only after owner sign-off or after a hard two-business-day timeout with the default applied.

## Required Changes

None blocking. The following are absorbed as defaults pending owner ratification (parallel, non-blocking):

- **D1 — adopt:** no public symbol/README/flag references Terminal-Bench, DeepAgent, Harbor, or Docker. Adapter is `TaskAdapter` protocol; reference impl stays `LocalSubprocessRunner`-shaped.
- **D2 — adopt:** closed taxonomy `{verifier-pass, verifier-fail, timeout, missing-artifact, assertion-fail, environment-error}`. Out-of-set signals fall back to `verifier-fail` with raw preserved in `outcome.message`.
- **D3 — adopt:** additive field changes → minor bump; breaking → major bump + `migrate_vN_vM.py` shim; old readers reject unknown majors.
- **Q2 (split ratio):** runtime validation with configurable minimum per split (default 1 each). Not encoded in `TaskCorpus` schema.
- **Q3 (audit-diff):** machine-readable first; exit code nonzero on diff; `--json` flag for structured diff; default human-readable summary.
- **Q4 (inspect-harness):** defer to P5.

## Revised Plan

**P4 Slice Name:** Task Adapter & Corpus Stabilization (TACS)

**Execution order with D1-risk sequencing:**

1. **P4.1 — `self_harness.corpus` module + `TaskCorpus` schema v1.**
   - Move loader logic out of `adapters/local_subprocess.py` into `self_harness.corpus`.
   - Schema: `{corpus_version: "1", corpus_id: str, checksum?: str, tasks: [...]}`.
   - `load_corpus(path) -> TaskCorpus` with typed `TaskLoadError` reasons.
   - `self-harness validate-tasks <path>` CLI.
   - Acceptance: malformed corpora rejected with structured errors; `local-demo` flow still works via thin adapter; checksum + min-per-split validation covered by tests.

2. **P4.2 — `TaskAdapter` protocol boundary** (start only after P4.1 merged or after owner D1 ratification / 2-day timeout).
   - Protocol: `load(corpus) -> list[Task]`, `runner() -> Runner`.
   - `LocalSubprocessTaskAdapter` as reference implementation.
   - `SelfHarnessEngine` drivable without touching `Task` constructors.
   - Acceptance: grep in `src/` finds zero occurrences of `terminal-bench|deepagent|harbor|docker` (case-insensitive). README "Limitations" section unchanged in substance.

3. **P4.3 — `FailureCategory` enum + classifier mapping** (depends on P4.1).
   - Closed enum from D2.
   - Extend `LocalSubprocessRunner` to classify `missing-artifact` (verify command pattern), `assertion-fail` (nonzero exit with stderr assertion-like signal), `environment-error` (env/template errors), preserve existing `verifier-pass`/`verifier-fail`/`timeout`.
   - Raw signal preserved in `outcome.message`; `FailureSignature.stable_id` unaffected for existing categories.
   - Acceptance: one-task-per-category corpus yields six distinct `terminal_cause` values; existing audit artifacts still loadable by `audit.py`.

4. **P4.4 — Audit schema policy docs + changelog test** (parallel).
   - `docs/architecture/audit_schema_policy.md` codifying D3.
   - `docs/architecture/schema_changelog.md` starting at 1.1.
   - Unit test: `SUPPORTED_SCHEMA_VERSIONS` is sourced from a single constant whose docstring points to the changelog.
   - No migration code.

5. **P4.5 — Derived CLI** (parallel with P4.3/P4.4).
   - `self-harness validate-tasks` (from P4.1).
   - `self-harness audit-diff <run_a> <run_b>` with `--json`; exit nonzero on diff.
   - `--corpus` flag on `local-demo`, deprecation shim for positional path.
   - Acceptance: `make check` green; CLI tests use existing pattern.

**Out of scope:** any Docker/Harbor/Terminal-Bench/DeepAgent integration; real provider SDKs; parallel/distributed evaluation; prompt/response content in audit artifacts.

**Cross-cutting acceptance:**
- `make check` green; mypy strict on new modules.
- README "Stable API" section updated to include `TaskCorpus`, `load_corpus`, `TaskAdapter`.
- README limitations section still disclaims Terminal-Bench reproduction.
- New schema additions bump to `1.2` and pass `audit.py` under D3.
- No public symbol references reproduction-only systems (enforced by test in P4.2).

## Remaining Open Questions

All non-blocking. Tracked for owner awareness only:

1. Owner ratification of D1/D2/D3 — defaults already applied; rejection triggers a renaming pass only in P4.2 surface area.
2. Whether `TaskCorpus` checksum should be SHA-256 of canonicalized JSON or raw bytes — infer SHA-256 over `stable_json_dumps` output for determinism; revisit if corpus authors push back.
3. Future P5 candidates surfaced during planning but explicitly deferred: `inspect-harness`, corpus signing, provider SDK adapters.
