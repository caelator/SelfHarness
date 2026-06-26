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
