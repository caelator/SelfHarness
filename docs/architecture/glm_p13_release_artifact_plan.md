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
