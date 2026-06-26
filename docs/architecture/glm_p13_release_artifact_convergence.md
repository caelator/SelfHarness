# Round 1

CONVERGED: NO

## Verdict

The P13 candidate (release artifact installability and local package smoke gate) is the correct highest-value slice given the current state. The repository has strong source-level gates (`make check`, `make readiness`), a release workflow that builds dist + SBOM, and a CI `core-import` job that proves bare `import self_harness` works. However, no local gate currently proves that a *built wheel* installs cleanly in an isolated environment, that the `self-harness` console script works from the installed artifact, or that the deterministic audit hash is reproducible from an installed (non-editable) package. This is the exact gap between "source passes" and "release artifact is usable," and it can be closed without Harbor/Docker. Alternatives (more paper docs, migration shims, upstream comparison) are lower value: paper fidelity is already enforced by invariants, no breaking schema change is pending, and upstream comparison is blocked by the same Harbor/Docker constraint.

## Critique

**Strengths of the candidate:**
- Directly fills the stated gap: built wheel installability, installed CLI behavior, local artifact sanity.
- Fully executable without Harbor/Docker; uses the existing deterministic demo + audit-summary + audit-trajectory path.
- Composes cleanly with existing `make build` and the release workflow.
- Preserves the no-reproduction-claim contract by reusing the existing demo/audit commands.

**Weaknesses to address in revision:**
- The original candidate is underspecified on *what* the smoke gate proves beyond import. It must assert installed-CLI behavioral parity (deterministic audit hash from installed wheel matches the source-tree fixture), otherwise it is only marginally stronger than the existing `core-import` CI job.
- No explicit decision on whether the gate runs against wheel only, or wheel + sdist. Recommendation: wheel for behavior, sdist presence check only.
- No decision on SBOM locality. Current release workflow generates SBOM only in CI; a local `make sbom` would close the loop but requires `cyclonedx-bom` in the `release` extra (already present). Should be included as optional.
- Must guard against editable-install false positives: the smoke venv must not inherit `src/` on `sys.path`.
- Must define the relationship to `tests/fixtures/canonical_audit_hash.txt`: the installed-wheel run must reproduce the same hash, proving the artifact carries everything needed for the readiness contract.

## Required Changes

1. **Scope the smoke gate to prove behavioral parity, not just import.** The gate must run `self-harness demo`, `self-harness audit-summary`, and `self-harness audit-trajectory` from the installed wheel and assert the resulting `audit_tree_hash` equals `tests/fixtures/canonical_audit_hash.txt`.
2. **Enforce venv isolation purity.** The smoke script must create a fresh venv with `--no-system-site-packages`, must not set `PYTHONPATH` to the repo `src/`, and must `pip install` only the built wheel (plus provenance extra if needed for signature paths used in smoke).
3. **Add a local `make smoke` target** distinct from `make build`, and a `make release-smoke` that chains `build` + `smoke` + optional SBOM.
4. **Add a CI job** `release-smoke` that runs on the Python 3.11/3.12/3.13 matrix, builds the wheel, and runs the smoke gate. Keep it separate from the existing `test` job so a smoke failure does not mask source-test failures.
5. **Make SBOM local generation optional but documented.** Add `make sbom` that uses the existing `release` extra; do not fail `make smoke` if SBOM tooling is absent.
6. **Document the gate** in `RELEASE.md` as a required pre-tag step and in `README.md` under Development.
7. **Explicit deferral list** in the plan: live Harbor execution, real PyPI publish round-trip, cross-platform wheel matrix beyond linux/macOS, signed-artifact verification, and any `reproduction_claimed=true` path.

## Revised Plan

**P13: Release Artifact Installability and Local Package Smoke Gate**

### Code / Script Changes
- Add `scripts/release_smoke.py` (or `.sh`) that:
  1. Requires exactly one wheel argument (and optionally an sdist path).
  2. Creates an isolated venv under a temporary directory with `--no-system-site-packages`.
  3. Installs the wheel with `pip install <wheel>[provenance]`.
  4. Asserts `python -c "import self_harness; from self_harness import EngineConfig, SelfHarnessEngine, audit_tree_hash"` succeeds.
  5. Runs `self-harness demo --rounds 1 --seed 0 --out <tmp>/demo` from the venv.
  6. Runs `self-harness audit-summary <tmp>/demo` and `self-harness audit-trajectory <tmp>/demo`.
  7. Computes `audit_tree_hash(<tmp>/demo)` via the installed package and compares to `tests/fixtures/canonical_audit_hash.txt` (path resolved relative to repo root passed via env var).
  8. Exits non-zero on any mismatch.
  9. Optionally, if `cyclonedx-py` is on PATH, generate SBOM into `sbom/` and fail on generator error.

### Makefile Changes
```
smoke: build
	$(PYTHON) scripts/release_smoke.py --wheel $$(ls dist/*.whl) --repo-root .

release-smoke: check readiness build smoke

sbom:
	mkdir -p sbom && cyclonedx-py environment -o sbom/self_harness-sbom.json
```

### Test Changes
- Add `tests/release/test_smoke_contract.py` that documents the smoke contract (what the script must verify) as a readable spec; it does not itself create venvs (the script does). This keeps the pytest suite fast and lets the smoke script be the executor.
- No change to existing invariants or canonical hash fixture.

### CI Changes
- New job in `.github/workflows/ci.yml`:
  - `release-smoke` on Python 3.11/3.12/3.13.
  - Steps: checkout, setup-python, `pip install -e ".[dev,provenance,release]"`, `make PYTHON=python release-smoke`.
  - Upload `dist/` and `sbom/` as artifacts for inspection.

### Docs Changes
- `RELEASE.md`: add "Local Release Smoke Gate" section stating `make release-smoke` is required before tagging and that the smoke gate asserts installed-wheel behavioral parity with the canonical audit hash.
- `README.md` Development section: add `make smoke` / `make release-smoke` to the individual-commands list.
- `docs/architecture/productionization_brief.md`: add a P13 status entry after P12 documenting the implemented slice and deferrals.

### Stop Conditions
- `make release-smoke` passes locally on Python 3.11+.
- New CI job passes on all three Python versions.
- Installed-wheel `audit_tree_hash` matches the committed canonical fixture.
- No regression in `make check` or `make readiness`.
- No reproduction claim introduced anywhere.

### Explicit Deferrals
- Live Harbor/Docker Terminal-Bench reproduction.
- Real TestPyPI/PyPI publication round-trip (trusted publishing already configured; not exercised here).
- Cross-platform wheel testing beyond linux/macOS x86-64.
- Signed-artifact (sigstore) verification.
- Schema migration shims (not needed until next major schema bump).
- Upstream DeepAgent/Harbor source comparison (blocked on Harbor availability).

## Remaining Open Questions

1. **Should the smoke gate also install and exercise the `anthropic` extra?** Inference: probably no, to keep the gate offline-deterministic; but a decision is needed on whether to add a separate `make smoke-extras` target. Non-blocking for P13 core.
2. **Should `make smoke` run on macOS in CI, or linux-only?** Evidence: current CI is ubuntu-only. Decision needed on whether to add macOS runners. Non-blocking; can be added later.
3. **Should the smoke gate verify the sdist installs as well as the wheel?** Inference: wheel-only is sufficient for behavioral parity; sdist presence check is enough. Confirm in round 2.
4. **Canonical hash fixture path resolution:** the smoke script needs the repo root to locate `tests/fixtures/canonical_audit_hash.txt`; decide whether to pass via `--repo-root` or require the script be run from repo root. Non-blocking; default to `--repo-root` flag.

[usage] {"completion_tokens": 3073, "completion_tokens_details": {"reasoning_tokens": 1212}, "prompt_tokens": 31634, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 34707}

---

# Round 2

CONVERGED: YES

## Verdict

The P13 plan (release artifact installability and local package smoke gate) is ready to execute. All round 1 required changes are incorporated: behavioral parity assertion via canonical audit hash, venv isolation purity, dedicated `make smoke` / `make release-smoke` targets, separate CI job on the Python matrix, optional local SBOM, docs updates, and an explicit deferral list. The remaining open questions have sensible non-blocking defaults, and no material risk is unaddressed. The candidate correctly prioritizes artifact-level validation over alternatives (more paper docs, migration shims, upstream comparison) given current constraints.

## Critique

**Evidence-backed strengths of the revised plan:**
- The repository already has `make check`, `make readiness`, `make build`, a release workflow with SBOM, and a CI `core-import` job proving bare `import self_harness` (per `ci.yml`). The gap is real: nothing currently proves a built wheel installs cleanly in an isolated venv and produces the canonical audit hash from a non-editable install.
- The canonical hash fixture (`tests/fixtures/canonical_audit_hash.txt`) and the invariants test (`test_canonical_audit_hash_matches_fixture_and_detects_layout_mutation`) establish the exact behavioral parity contract the smoke gate must reproduce: `EngineConfig(rounds=1, seed=0)` + `write_audit_trajectory`. The plan correctly references this contract.
- The no-reproduction-claim invariant is already enforced (`test_terminal_bench_audit_cannot_claim_reproduction`, `validate_benchmark_claims`), so reusing `self-harness demo` / `audit-summary` / `audit-trajectory` preserves the contract by construction.

**Risks addressed in revision:**
- Editable-install false positive: handled by `--no-system-site-packages` and not setting `PYTHONPATH` to `src/`.
- Wheel vs sdist scope: wheel for behavior, sdist presence check only — correct, since behavioral parity is the goal.
- Hash fixture path resolution from installed wheel: handled by `--repo-root` flag; the installed package has no access to `tests/fixtures/`, so the script must read the fixture from the repo tree.
- SBOM locality: optional, documented, does not fail smoke if `cyclonedx-bom` is absent.
- Trajectory bytes coverage: the canonical hash includes derived `trajectory.jsonl` (per `RELEASE.md` and the invariants test), so the smoke must call `audit-trajectory` before hashing. The plan's step 6 covers this.

**Remaining open questions are non-blocking:**
1. Anthropic extra in smoke: keep offline-deterministic for P13; a separate `make smoke-extras` can be added later.
2. macOS CI: current CI is ubuntu-only (evidence: `ci.yml`); adding macOS runners is additive and not required for the core contract.
3. sdist install: wheel-only is sufficient for behavioral parity; sdist presence check is enough.
4. Fixture path resolution: default to `--repo-root` flag (minor wording inconsistency between "env var" and "flag" in the plan text is not material; the executor should pick `--repo-root` flag).

## Required Changes

No further changes required. The plan is executable as written, with the minor clarification that the smoke script should use a `--repo-root` CLI flag (not an env var) to locate `tests/fixtures/canonical_audit_hash.txt`, and that `self-harness audit-trajectory <tmp>/demo` must be invoked before `audit_tree_hash(<tmp>/demo)` so the derived trajectory bytes are present.

## Revised Plan

**P13: Release Artifact Installability and Local Package Smoke Gate**

### Code / Script Changes
- Add `scripts/release_smoke.py` that:
  1. Requires exactly one wheel path and a `--repo-root` flag.
  2. Creates an isolated venv under a temporary directory with `--no-system-site-packages`.
  3. Installs the wheel with `pip install <wheel>[provenance]` (provenance extra is needed because the demo path may exercise signature helpers).
  4. Asserts `python -c "import self_harness; from self_harness import EngineConfig, SelfHarnessEngine, audit_tree_hash"` succeeds.
  5. Runs `self-harness demo --rounds 1 --seed 0 --out <tmp>/demo` from the venv (rounds=1, seed=0 matches the canonical hash fixture contract).
  6. Runs `self-harness audit-trajectory <tmp>/demo` so `trajectory.jsonl` is written in place.
  7. Runs `self-harness audit-summary <tmp>/demo` (sanity; output not asserted beyond exit 0).
  8. Computes `audit_tree_hash(<tmp>/demo)` via the installed package and compares to `<repo-root>/tests/fixtures/canonical_audit_hash.txt`; exits non-zero on mismatch.
  9. Optionally, if `cyclonedx-py` is on PATH, generate SBOM into `<repo-root>/sbom/`; do not fail if absent.

### Makefile Changes
```
smoke: build
	$(PYTHON) scripts/release_smoke.py --wheel $$(ls dist/*.whl) --repo-root .

release-smoke: check readiness build smoke

sbom:
	mkdir -p sbom && cyclonedx-py environment -o sbom/self_harness-sbom.json
```

### Test Changes
- Add `tests/release/test_smoke_contract.py` as a readable spec documenting what the smoke script must verify (imports, demo run, trajectory write, hash parity, exit codes). It does not itself create venvs.
- No change to existing invariants or canonical hash fixture.

### CI Changes
- New job `release-smoke` in `.github/workflows/ci.yml` on Python 3.11/3.12/3.13, ubuntu-latest.
- Steps: checkout, setup-python, `pip install -e ".[dev,provenance,release]"`, `make PYTHON=python release-smoke`.
- Upload `dist/` and `sbom/` as artifacts.
- Keep separate from `test` job so smoke failures do not mask source-test failures.

### Docs Changes
- `RELEASE.md`: add "Local Release Smoke Gate" section — `make release-smoke` is required before tagging; asserts installed-wheel behavioral parity with the canonical audit hash.
- `README.md` Development section: add `make smoke` / `make release-smoke` to individual-commands list.
- `docs/architecture/productionization_brief.md`: add P13 status entry after P12.

### Stop Conditions
- `make release-smoke` passes locally on Python 3.11+.
- New CI job passes on all three Python versions.
- Installed-wheel `audit_tree_hash` matches committed `tests/fixtures/canonical_audit_hash.txt`.
- No regression in `make check` or `make readiness`.
- No `reproduction_claimed=true` path introduced.

### Explicit Deferrals
- Live Harbor/Docker Terminal-Bench reproduction.
- Real TestPyPI/PyPI publication round-trip (trusted publishing already configured; not exercised).
- Cross-platform wheel testing beyond linux/macOS x86-64.
- Signed-artifact (sigstore) verification.
- Schema migration shims (not needed until next major schema bump).
- Upstream DeepAgent/Harbor source comparison (blocked on Harbor availability).
- Anthropic-extra smoke (offline-deterministic core first; separate `make smoke-extras` later).

## Remaining Open Questions

All non-blocking; defaults are safe to execute under:
1. **Anthropic extra in smoke?** Default: no for P13 core; offline-deterministic first. Add `make smoke-extras` later if needed.
2. **macOS in CI?** Default: ubuntu-only for now (matches current CI); add macOS runners additively later.
3. **sdist install in smoke?** Default: wheel-only for behavioral parity; sdist presence check only.
4. **Fixture path resolution?** Default: `--repo-root` CLI flag (not env var).

[usage] {"completion_tokens": 3071, "completion_tokens_details": {"reasoning_tokens": 1348}, "prompt_tokens": 35383, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 38454}
