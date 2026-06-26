# Round 1

CONVERGED: NO

## Verdict

Initial P4 plan produced, self-critiqued, and revised. The architecture is clear enough to recommend a decisive prioritization, but two material items keep this from a clean YES in round 1: (a) the scope boundary between "Terminal-Bench-like adapter" and "Terminal-Bench reproduction" needs an explicit user-owned guardrail decision, and (b) acceptance criteria for "richer verifier classification" depend on a product decision about what failure categories are first-class. These are user/product decisions, not planning gaps, but they are also small enough that one more convergence round with a yes/no from the owner will close them. Plan is otherwise executable.

## Critique

Critique of the first-draft plan I considered (do all five in parallel):

1. **Terminal-Bench adapter without a hard scope contract invites over-reach.** The README and briefs repeatedly warn against claiming reproduction. A `TerminalBenchTaskAdapter` class name or any harness that imports Docker/Harbor semantics crosses the line. The adapter must be a *protocol shape*, not an integration.
2. **Richer verifier classification is under-specified without a failure taxonomy.** Current `VerifierOutcome` already has `terminal_cause`/`causal_status`/`mechanism`. "Richer" only has value if we commit to a closed taxonomy (e.g. `command-not-found`, `nonzero-exit`, `timeout`, `missing-artifact`, `assertion-fail`). Without that decision, the slice becomes bikeshedding.
3. **Audit schema migration policy is small but has release implications.** Hard-coding `SUPPORTED_SCHEMA_VERSIONS = {"1.0","1.1"}` is fine today; a migration *policy* doc plus a `schema_changelog.md` is the real deliverable, not code.
4. **Deterministic task corpus format already exists implicitly** via `load_tasks_json`. The work is stabilization (manifest, checksums, validation errors), not invention.
5. **CLI ergonomics should be a side-effect of the above, not its own slice.** A `validate-tasks` and `audit-diff` subcommand fall out of corpus + schema work for free.

Net: the five priorities are not peers. Adapter shape and corpus format are the foundation; verifier classification sits on top of corpus; audit policy is a small parallel doc; CLI is a derived artifact.

## Required Changes

For round 2, the plan needs the owner to confirm or reject these three decisions before it can move to YES:

- **D1 (blocking):** Adopt the rule "no symbol, file, package, README claim, or CLI flag may reference Terminal-Bench, DeepAgent, Harbor, or Docker by name in the public/stable API." Adapter work proceeds under a neutral name (e.g. `SubprocessTaskAdapter` extension + `TaskCorpus` interface). Accept/reject?
- **D2 (blocking):** Commit to a closed v1 verifier taxonomy in this slice: `{verifier-pass, verifier-fail, timeout, missing-artifact, assertion-fail, environment-error}`. Anything outside the set maps to `verifier-fail` with the raw signal preserved in `outcome.message`. Accept/reject?
- **D3 (non-blocking but requested):** Audit schema migration policy = additive-only field changes bump minor, breaking changes bump major and require a `migrate_vN_vM.py` shim; old readers reject unknown majors. Accept as policy?

## Revised Plan

**P4 Slice Name:** Task Adapter & Corpus Stabilization (TACS)

**Theme:** Make external task corpora a first-class, validated, versioned input so real evaluators can be plugged in *without* this project claiming to be a Terminal-Bench reproduction.

**Priority order (architecturally justified, not peer-ranked):**

1. **P4.1 — Task corpus format stabilization (foundation).**
   - Promote the implicit `tasks.json` shape to a documented `TaskCorpus` schema v1: top-level `corpus_version`, `corpus_id`, optional `checksum`, `tasks[]`.
   - Add `self_harness.corpus.load_corpus(path) -> TaskCorpus` with strict validation, returning typed `TaskLoadError` reasons.
   - Add `self-harness validate-tasks <path>` CLI subcommand.
   - Acceptance: corpus loader rejects malformed input with structured errors; existing `local-demo` flow still works through an adapter; new tests cover schema, checksum, and split-balance checks.

2. **P4.2 — Task adapter protocol boundary (depends on P4.1).**
   - Introduce a `TaskAdapter` protocol (distinct from `Runner`): `load(corpus) -> list[Task]`, `runner() -> Runner`. Keep `LocalSubprocessRunner` as the reference implementation.
   - Document explicitly in code and README that this is a *protocol shape for future adapters*, not a Terminal-Bench adapter. Enforce D1.
   - Acceptance: `SelfHarnessEngine` can be driven entirely through a `TaskAdapter` without touching `Task` constructors; no Terminal-Bench/DeepAgent/Docker/Harbor symbols anywhere in `src/`.

3. **P4.3 — Verifier classification v1 (depends on P4.1, D2).**
   - Introduce a `FailureCategory` enum closed over the D2 taxonomy.
   - Extend the local subprocess adapter to map exit-code/stderr/missing-file signals into categories; preserve raw `outcome.message`.
   - Update `FailureSignature` consumers to bucket on category without breaking the existing `stable_id`.
   - Acceptance: a corpus with one task per category produces six distinct `terminal_cause` values; existing audit artifacts remain readable by current `audit.py`.

4. **P4.4 — Audit schema migration policy (parallel, small).**
   - Add `docs/architecture/audit_schema_policy.md` codifying D3.
   - Add `docs/architecture/schema_changelog.md` starting at 1.1.
   - Add a unit test asserting `SUPPORTED_SCHEMA_VERSIONS` is sourced from the changelog file or a single constant with a docstring pointer.
   - No migration code yet; policy + test only.

5. **P4.5 — CLI ergonomics (derived).**
   - `self-harness validate-tasks` (from P4.1).
   - `self-harness audit-diff <run_a> <run_b>` for byte-stable comparison (reuses existing stable JSON).
   - `--corpus` flag on `local-demo` replacing positional file, with deprecation shim.
   - Acceptance: `make check` passes; new commands tested via the existing CLI test pattern.

**Out of scope for P4 (explicit non-goals):**
- Any Docker/Harbor/Terminal-Bench/DeepAgent integration.
- Real LLM provider SDK integration.
- Parallel or distributed evaluation.
- Prompt/response logging into audit artifacts (still avoided for leakage/determinism).

**Cross-cutting acceptance criteria:**
- `make check` green; mypy strict on new modules.
- README "Stable API" section updated; limitations section still clearly states no Terminal-Bench reproduction.
- New schema additions bump to `1.2` and are accepted by `audit.py` under the migration policy.
- No public symbol references reproduction-only systems.

**Risks addressed:**
- *Reproduction overreach:* mitigated by D1 + naming review in acceptance criteria.
- *Schema drift:* mitigated by D3 + changelog test.
- *Corpus format churn:* mitigated by versioning `corpus_version` from day one.
- *Determinism regression:* mitigated by keeping stable JSON ordering and adding an `audit-diff` self-test on a fixture pair.

## Remaining Open Questions

1. **Owner decision on D1, D2, D3** — required to advance to YES.
2. Should `TaskCorpus` carry an explicit held-in/held-out split *ratio* constraint, or is that a runtime validation concern? Inference: make it a runtime validation with a configurable minimum per split (default 1 each). Confirm in round 2.
3. Is `audit-diff` expected to be machine-readable (exit code on diff) or human-readable? Inference: machine-readable first (`--json` flag), human default. Confirm in round 2.
4. Do we need a `self-harness inspect-harness <run>` command in this slice, or defer? Inference: defer to P5; not on any critical path for TACS.

[usage] {"completion_tokens": 2942, "completion_tokens_details": {"reasoning_tokens": 1125}, "prompt_tokens": 13598, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 16540}

---

# Round 2

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

[usage] {"completion_tokens": 2046, "completion_tokens_details": {"reasoning_tokens": 240}, "prompt_tokens": 17257, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 19303}
