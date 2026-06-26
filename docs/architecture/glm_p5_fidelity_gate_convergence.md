# Round 1

CONVERGED: NO

## Verdict
Round 1 establishes the candidate architecture for the P5 slice but cannot converge yet because the highest-value *single* next slice is ambiguous between three defensible options (paper-fidelity readiness gate, release/readiness automation, benchmark adapter boundary). Evidence shows P0–P4 are implemented and the repo explicitly defers "real benchmark adapters, richer verifiers, release automation, and migration shims" as the remaining work. No repository facts require user input yet, so the open question is scoping, not evidence. Round 2 should select exactly one slice with acceptance criteria; round 3 critique; round 4 converge.

## Critique
- **Evidence (from docs):** `productionization_brief.md` states P0–P4 are complete and lists exactly four remaining buckets: real benchmark adapters, richer non-subprocess verifier adapters, release automation, and migration shims. `paper_faithful_build_plan.md` claims P0 paper-faithful toy is complete; no Terminal-Bench reproduction.
- **Evidence (from code):** `audit.py` already supports schema versions 1.0–1.2 with a changelog doc reference; `corpus.py` has versioned `TaskCorpus`; `adapters/base.py` defines a neutral `TaskAdapter`; `llm_proposer.py` defines a neutral `LLMClient`. The seams exist; the gap is operationalization (release process, conformance tests, reproducibility gate).
- **Inference:** The cheapest high-leverage next slice is *release/readiness automation* because (a) it does not risk false reproduction claims, (b) it locks in the existing paper-faithful invariants via CI, and (c) it unblocks downstream benchmark-adapter work by stabilizing versioning.
- **Risk if we pick the benchmark-adapter slice next:** high probability of drifting toward reproduction claims without a release-readiness gate already in place to enforce the disclaimer contract.
- **Risk if we pick a readiness-gate-only slice:** useful but incomplete without release automation; the gate is only enforceable if CI runs it on every change and every release tag.
- **Combined slice risk:** bundling gate + automation is feasible but must be scoped tightly to fit a single convergence arc.

## Required Changes
- Round 2 must collapse the plan to one of:
  1. **P5a — Readiness Gate:** machine-checked paper-fidelity invariant suite + a `make readiness` target + CI enforcement. Acceptance: invariants fail when held-out leakage is injected; deterministic byte-stable artifact hash pinned.
  2. **P5b — Release Automation:** version policy tied to `schema_changelog.md`, tag-driven release workflow, signed artifact attestation, `pyproject` polish.
  3. **P5c — Benchmark Adapter Boundary:** a second non-reproduction `TaskAdapter` (e.g., file-verifier-only adapter) plus a conformance test for `TaskAdapter`/`Runner`, strictly without Terminal-Bench/DeepAgent naming.
- The revised plan below selects **P5a + P5b as a single slice** because the gate is what makes the release trustworthy; round 2 should either ratify this bundling or split it.

## Revised Plan
**Candidate slice (for round 2 to ratify or split): P5 — Readiness Gate & Release Automation**

1. **P5.1 Paper-fidelity invariant suite** (`tests/invariants/`).
   - One test per paper invariant: fixed-harness object identity across round; held-out split never present in `ProposerContext.{held_in_patterns, passing_summaries}`; exact-signature clustering (no semantic fallback); bounded-patch whitelist enforcement; aggregate pass-count acceptance under `evaluation_repeats>=2`; merge re-validation before commit; deterministic audit hash stable across runs and Python versions.
   - Includes at least one *negative* test per invariant that fails when the invariant is violated (mutation-style assertion).
   - Acceptance: `make readiness` exits nonzero on any injected violation.

2. **P5.2 Reproducibility gate.**
   - Pin a `DETERMINISM_SEED=0` reference run; store canonical artifact hash in `tests/fixtures/canonical_audit_hash.txt`.
   - CI matrix: Python 3.10, 3.11, 3.12; assert hash equality.
   - Acceptance: any PR changing audit-byte layout fails CI until fixture is intentionally rotated via documented rotation procedure.

3. **P5.3 Release automation.**
   - `RELEASE.md` defining: version bump rule mapped to `schema_changelog.md` major/minor; tag format `vX.Y.Z`; release notes sourced from changelog.
   - GitHub Actions `release.yml`: on tag, build sdist+wheel, run `make check`, publish to TestPyPI then PyPI, attach SBOM via `pip-audit` or `cyclonedx-py`.
   - Acceptance: dry-run release workflow passes on a `v0.0.0-rc.*` tag without publishing.

4. **P5.4 Naming guardrail as CI check** (cheap insurance against reproduction drift).
   - Grep test asserting zero occurrences of `terminal-bench|deepagent|harbor|docker` in `src/` and README non-limitation sections.
   - Acceptance: test fails if a reproduction claim is introduced outside the explicit limitations section.

**Out of scope (explicit):**
- Any real Terminal-Bench/DeepAgent/Harbor/Docker adapter.
- Any provider SDK integration beyond the existing `LLMClient` protocol.
- New audit schema versions beyond documenting the rotation procedure for 1.2→1.3.
- Parallel/distributed evaluation.

**Cross-cutting acceptance:**
- `make check && make readiness` green.
- CHANGELOG entry in `docs/architecture/schema_changelog.md` (no schema bump this slice).
- README "Production status" section gains a "Readiness gate" subsection; limitations section unchanged.

## Remaining Open Questions
1. **Scope ratification (blocking round 2):** is the P5a+P5b bundle the right next slice, or should we ship P5a (readiness gate) first and defer release automation to P6? My recommendation: bundle, because the gate is only credible when CI-enforced on every release.
2. **Python version support window (non-blocking):** `productionization_brief.md` does not pin a minimum Python. Round 2 should set 3.10+ as the floor unless repo evidence contradicts.
3. **Artifact signing authority (non-blocking):** release automation needs a signer identity; default to OIDC-based trusted publishing on PyPI; flag for owner in round 3.
4. **Canonical hash rotation owner (non-blocking):** who is authorized to rotate `canonical_audit_hash.txt` and under what documented criteria.

[usage] {"completion_tokens": 2095, "completion_tokens_details": {"reasoning_tokens": 647}, "prompt_tokens": 20296, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 22391}

---

# Round 2

CONVERGED: NO

## Verdict
Round 2 ratifies the **P5a + P5b bundle** as the correct next slice. The two halves are mutually load-bearing: a paper-fidelity gate is only credible when CI enforces it on every release tag, and release automation is only trustworthy when the gate is green on the artifact being published. Splitting them would ship a gate that no one runs or a release pipeline that proves nothing. The remaining risk is scope creep inside the bundle, so this round tightens acceptance criteria and cuts optional sub-work. Plan is now concrete enough for round 3 critique; not yet CONVERGED because two non-blocking owner decisions (signing identity, hash-rotation authority) should be visible to the human before execution begins.

## Critique
- **Evidence (from repo):** `audit.py` already exposes `SCHEMA_CHANGELOG_DOC` and `SUPPORTED_SCHEMA_VERSIONS = {"1.0","1.1","1.2"}` with a docstring pointing to the changelog — this is the natural anchor for both the readiness gate (assert supported versions match the doc) and release automation (version policy derived from changelog semver). `productionization_brief.md` lists release automation as remaining work; nothing in `pyproject.toml`-adjacent files visible here contradicts that.
- **Evidence (from `engine.py` + `evaluation.py`):** the invariants named in P5.1 are all already structurally enforced at runtime (`assert all(pattern.split == Split.HELD_IN ...)`, `acceptance_rule` strict-improvement check, `merge_patches` + re-validation). The invariant suite therefore codifies *existing* behavior rather than specifying new behavior — low implementation risk, high audit value.
- **Evidence (from `test_engine_demo.py`):** byte-stable determinism across two runs is *already asserted*. P5.2's reproducibility gate extends this from "two same-process runs" to "hash pinned across Python versions and CI environments" — a strict strengthening, not a new contract.
- **Inference:** the bundle is one slice because (a) `make readiness` only becomes a release gate when wired into `release.yml`, and (b) `release.yml` only proves trustworthiness when `make readiness` is what it runs. Shipping either alone produces a credential that no consumer can verify.
- **Risk if we ship gate-only:** README claims a readiness gate but releases are still ad-hoc; the gate is decorative.
- **Risk if we ship release-only:** publishes artifacts with no machine-checked paper-fidelity invariants; drift can land on PyPI undetected.
- **Risk in the bundle as scoped in round 1:** the multi-Python matrix (3.10/3.11/3.12) is engineering polish, not readiness. The hash-equality property only needs one canonical environment initially; matrix expansion is non-blocking follow-up. Cutting it keeps the slice shippable in one convergence arc.
- **Risk in P5.4 naming guardrail:** cheap and high-value; keep, but scope the grep to `src/`, `tests/`, and README outside the explicit limitations section to avoid false positives in doc references.

## Required Changes
- Adopt the **bundled P5a+P5b slice** as P5.
- Cut the cross-Python-version hash matrix from acceptance: pin one canonical environment (Python 3.11, `DETERMINISM_SEED=0`) and document matrix expansion as P6 follow-up.
- Pin Python floor at **3.10** in `pyproject.toml` (`requires-python = ">=3.10"`); no evidence in repo contradicts.
- Default release identity to **PyPI OIDC trusted publishing**; record the requirement in `RELEASE.md` and flag the owner-config step as non-blocking.
- Default canonical-hash rotation authority to **"any maintainer, with a CHANGELOG entry justifying the rotation and a paired schema-minor bump"**; record in `docs/architecture/schema_changelog.md`.
- Scope the P5.4 grep guardrail to `src/`, `tests/`, and README outside the limitations section. Allow doc files (`docs/`) to reference the names for historical/comparative context.
- Negative tests in P5.1 must include: held-out leakage into `ProposerContext.held_in_patterns`, held-out leakage into `PassingSummary.split`, semantic-clustering fallback, whitelist-violating op, tie-only candidate acceptance, and audit-byte-layout mutation.

## Revised Plan

**P5 — Readiness Gate & Release Automation (bundled)**

1. **P5.1 Paper-fidelity invariant suite** — `tests/invariants/`.
   - One positive and at least one negative (mutation) test per invariant: fixed-harness identity across round; held-in-only proposer context (patterns *and* passing summaries); exact-signature clustering; bounded-patch whitelist; aggregate pass-count acceptance under `evaluation_repeats>=2`; merge re-validation before commit; schema-version set matches `schema_changelog.md`.
   - `make readiness` target: `make test invariants` plus an audit-bytes-stability check.
   - Acceptance: any single mutation flips `make readiness` to nonzero.

2. **P5.2 Reproducibility gate.**
   - `DETERMINISM_SEED=0` reference run; canonical SHA-256 of the audit tree stored at `tests/fixtures/canonical_audit_hash.txt`.
   - CI (single environment: Python 3.11, ubuntu-latest) asserts equality on every PR and every tag.
   - Rotation procedure documented in `docs/architecture/schema_changelog.md`: requires paired schema-minor bump + CHANGELOG justification.
   - Acceptance: a PR that changes audit byte layout fails CI; rotation is the only documented path to green.

3. **P5.3 Release automation.**
   - `RELEASE.md`: semver rule mapped to `schema_changelog.md` (additive field → minor, breaking → major + migration shim), tag format `vX.Y.Z`, release notes sourced from changelog.
   - `.github/workflows/release.yml`: on tag, run `make check && make readiness`, build sdist+wheel, publish to TestPyPI then PyPI via OIDC trusted publishing, attach SBOM (`cyclonedx-py`).
   - `pyproject.toml`: `requires-python = ">=3.10"`, project metadata polish (license, classifiers, URLs).
   - Acceptance: dry-run on `v0.0.0-rc.*` tag builds, runs the full gate, and skips publication.

4. **P5.4 Naming guardrail** — `tests/test_naming_guardrail.py`.
   - Grep asserts zero case-insensitive occurrences of `terminal-bench|deepagent|harbor|docker` in `src/`, `tests/`, and README outside the limitations section.
   - Acceptance: introducing a reproduction claim outside the limitations section fails CI.

**Out of scope (explicit):**
- Any real Terminal-Bench / DeepAgent / Harbor / Docker adapter.
- Any provider SDK integration beyond the existing `LLMClient` protocol.
- New audit schema versions beyond documenting the rotation procedure for 1.2→1.3.
- Parallel / distributed evaluation.
- Cross-Python hash-stability matrix (deferred to P6).
- `inspect-harness` CLI (deferred from P4 open questions).
- Corpus signing.

**Cross-cutting acceptance:**
- `make check && make readiness` green in CI.
- `docs/architecture/schema_changelog.md` gains a 1.2→(no bump this slice) note documenting the rotation procedure; no schema bump this slice.
- README "Production status" gains a "Readiness gate" subsection; limitations section unchanged.
- `RELEASE.md` and `release.yml` merged with a successful dry-run rc tag.

## Remaining Open Questions
1. **Owner ratifies OIDC trusted publishing identity (non-blocking):** default applied; rejection forces a signing-key decision in round 3.
2. **Owner ratifies hash-rotation policy (non-blocking):** default "maintainer + paired minor bump + CHANGELOG" applied; rejection forces an alternative authority model in round 3.
3. **Round 3 should stress-test:** whether the single-environment hash pin is too brittle (any locale/timezone/socket artifact leaking into the audit tree would break it) — the negative test must prove the hash is invariant to non-audit inputs.

[usage] {"completion_tokens": 2036, "completion_tokens_details": {"reasoning_tokens": 246}, "prompt_tokens": 23217, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 25253}

---

# Round 3

CONVERGED: YES

## Verdict

Round 3 confirms the P5a+P5b bundle is ready to execute. The two non-blocking owner decisions from round 2 (OIDC trusted publishing identity, hash-rotation authority) have defensible defaults that the convergence rules allow me to adopt; blocking further planning on owner sign-off would be procedural rather than substantive. The round-2 brittleness concern (non-audit inputs leaking into the canonical hash) is a real implementation hazard but is already mitigated by the existing `stable_json_dumps` discipline in `audit.py` and `engine.py` — I verified the audit writer uses stable key ordering and contains no timestamps, PIDs, or host-derived fields. The negative test required in P5.1 acceptance is the right safeguard; no additional planning round is needed to discover whether the hazard is real.

## Critique

- **Evidence (from `engine.py` + `audit.py`):** audit rows are emitted exclusively through `write_stable_json` / `write_jsonl` over dataclasses serialized via `stable_json_dumps`. No `datetime.now()`, no `socket`, no `os.getpid()`, no `platform.*` calls appear in the write paths. The canonical hash pin is therefore structurally defensible in a single environment; the residual risk is environment *ambient* (filesystem enumeration order via `rglob`, Python dict iteration over `os.environ` in the local-subprocess adapter's env merge) — both are already neutralized (`sorted(...)` and `dict(os.environ)` then explicit update).
- **Evidence (from `test_engine_demo.py`):** byte-stable cross-run determinism is already asserted within a single environment. P5.2 strengthens this from "two runs" to "pinned hash across CI runs" — a strict subset extension with no new contract surface.
- **Inference:** the invariant suite codifies existing runtime asserts (`assert all(pattern.split == Split.HELD_IN ...)`, `acceptance_rule` strict-improvement, `merge_patches` + re-validation). Implementation risk is low because the suite makes existing behavior machine-checkable rather than specifying new behavior.
- **Risk assessment of the bundle:** bundling gate + release is correct because a gate without a release pipeline is decorative, and a release pipeline without a gate is untrustworthy. Round 2 already documented this; nothing in round 3 contradicts.
- **Risk on the single-environment pin:** acceptable for this slice. Cross-Python matrix is correctly deferred to P6. The rotation procedure (paired schema-minor bump + CHANGELOG justification) is sufficient governance for the rare event a rotation is needed.
- **Risk on naming guardrail scoping:** the round-2 narrowing (exclude `docs/`, exclude README limitations section) is correct — it prevents false positives on historical/comparative references while still catching new reproduction claims in code.

## Required Changes

None blocking. The following are absorbed as defaults pending parallel owner ratification:

- **OIDC trusted publishing** is the release identity default. If the owner rejects, round 4 of a *future* release-hardening slice can introduce signing keys; it does not block P5 execution because `release.yml` can be written OIDC-first and reconfigured later.
- **Hash-rotation authority** = "any maintainer + paired schema-minor bump + CHANGELOG justification" is the default. Rejection triggers an alternative authority model in a future slice, not a block here.
- **P5.1 negative-test list** must include the six mutations enumerated in round 2 (held-out leakage into `ProposerContext.held_in_patterns`; held-out leakage into `PassingSummary.split`; semantic-clustering fallback; whitelist-violating op; tie-only candidate acceptance; audit-byte-layout mutation). Add a seventh: **non-audit ambient input mutation** — e.g., inject a `LANG`/`TZ` environment variable and assert the canonical hash is unchanged. This closes the round-2 open question experimentally rather than by argument.

## Revised Plan

**P5 — Readiness Gate & Release Automation (bundled)** — execution-ready.

1. **P5.1 Paper-fidelity invariant suite** — `tests/invariants/`.
   - One positive + ≥1 negative mutation test per invariant: fixed-harness identity across round; held-in-only proposer context (patterns *and* passing summaries); exact-signature clustering (no semantic fallback); bounded-patch whitelist; aggregate pass-count acceptance under `evaluation_repeats>=2`; merge re-validation before commit; `SUPPORTED_SCHEMA_VERSIONS` matches `schema_changelog.md`.
   - Negative-test list fixed per "Required Changes" above (7 mutations).
   - `make readiness` target: runs invariant suite + audit-bytes-stability check.
   - Acceptance: any single mutation flips `make readiness` to nonzero.

2. **P5.2 Reproducibility gate.**
   - `DETERMINISM_SEED=0` reference run; canonical SHA-256 at `tests/fixtures/canonical_audit_hash.txt`.
   - CI (single env: Python 3.11, ubuntu-latest, pinned `LANG=C.UTF-8`, `TZ=UTC`) asserts equality on every PR and tag.
   - Rotation procedure in `docs/architecture/schema_changelog.md`: paired schema-minor bump + CHANGELOG justification.
   - Acceptance: PR that changes audit byte layout fails CI; rotation is the only documented path to green; ambient-input mutation test passes.

3. **P5.3 Release automation.**
   - `RELEASE.md`: semver rule mapped to `schema_changelog.md` (additive → minor; breaking → major + migration shim); tag `vX.Y.Z`; release notes from changelog.
   - `.github/workflows/release.yml`: on tag, `make check && make readiness`, build sdist+wheel, publish TestPyPI → PyPI via OIDC trusted publishing, attach SBOM via `cyclonedx-py`.
   - `pyproject.toml`: `requires-python = ">=3.10"`, license/classifiers/URLs polish.
   - Acceptance: dry-run on `v0.0.0-rc.*` builds, runs the full gate, and skips publication.

4. **P5.4 Naming guardrail** — `tests/test_naming_guardrail.py`.
   - Grep asserts zero case-insensitive `terminal-bench|deepagent|harbor|docker` in `src/`, `tests/`, and README outside the limitations section.
   - `docs/` explicitly exempted for historical/comparative references.
   - Acceptance: introducing a reproduction claim outside the limitations section fails CI.

**Out of scope (explicit):** real Terminal-Bench/DeepAgent/Harbor/Docker adapter; provider SDK integration beyond `LLMClient`; new audit schema versions (only rotation procedure documented); parallel/distributed evaluation; cross-Python hash-stability matrix (P6); `inspect-harness` CLI (deferred from P4); corpus signing.

**Cross-cutting acceptance:**
- `make check && make readiness` green in CI.
- `docs/architecture/schema_changelog.md` gains a 1.2→(no bump this slice) note documenting rotation; no schema bump this slice.
- README "Production status" gains a "Readiness gate" subsection; limitations section unchanged.
- `RELEASE.md` + `release.yml` merged with a successful dry-run rc tag.

## Remaining Open Questions

All non-blocking. Tracked for owner awareness only; none prevents P5 execution.

1. **Owner ratifies OIDC trusted publishing identity** — default applied; rejection triggers a signing-key slice in P6+, not a block.
2. **Owner ratifies hash-rotation policy** — default "maintainer + paired minor bump + CHANGELOG" applied; rejection triggers an alternative authority model in P6+, not a block.
3. **Future P6 candidates surfaced but explicitly deferred:** cross-Python (3.10/3.11/3.12) hash-stability matrix; corpus signing; `inspect-harness` CLI; real benchmark adapter boundary.

[usage] {"completion_tokens": 1947, "completion_tokens_details": {"reasoning_tokens": 250}, "prompt_tokens": 25406, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 27353}
