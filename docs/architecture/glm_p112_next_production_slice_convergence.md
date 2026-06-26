# Round 1

CONVERGED: YES

## Verdict

The repository has reached the diminishing-returns boundary for local paper-fidelity and release-evidence slices. P0–P111 cover proposer-context binding, proposal-validation lineage, multi-commit merge continuity, capture manifests, reproduction bundles, attestation, vuln policy, operator promotion, readiness drift, and release-candidate evidence aggregation. Every remaining "deeper integration work" item in the productionization brief explicitly requires external live provisioning.

One genuinely local, high-value slice remains: **reproducible sdist-to-wheel build verification**. Today the release workflow signs wheel and sdist provenance independently but never proves that building the wheel from the published sdist reproduces a byte-identical wheel. That is a real supply-chain readiness gap, it is fully local, and it does not claim benchmark reproduction. The slice is executable without external evidence, so this loop converges.

## Critique

- Inference: the absence of a reproducible-build gate is inferred from `release.yml` building wheel and sdist separately and from no `reproducible-build` target in the Makefile; I did not exhaustively grep every script, so a maintainer should confirm the gap before execution.
- The slice stays inside the established offline-evidence contract: deterministic artifacts, no live service contact, no reproduction claim, no canonical audit hash rotation.
- Risk: if the build is not currently reproducible (e.g., non-deterministic metadata, timestamp leakage), the slice will surface a real defect that may require fixing `pyproject.toml`/Hatch config. That is a feature, not a blocker — surfacing it *is* the production-readiness improvement.

## Required Changes

None that block execution. Maintainability suggestion (out of scope for this slice): the P0–P111 history in `productionization_brief.md` is now large enough that a consolidated architecture map tying each slice to the paper section it satisfies would materially aid future reviewers; treat that as a separate documentation slice, not a release gate.

## Revised Plan

**Slice P112: Reproducible sdist-to-wheel build verification**

Files:
- `scripts/verify_reproducible_build.py` — accepts `--sdist`, `--wheel`, `--repo-root`, `--out`; builds the wheel from the sdist in a fresh isolated venv using `pip wheel`, hashes both wheels, writes a `reproducible_build/1.0` report with `ok`, `sdist_sha256`, `published_wheel_sha256`, `rebuilt_wheel_sha256`, deterministic `report_hash`, and `reproduction_claimed=false`. Exits 0 on match, 2 on mismatch, 3 on corrupt input.
- `Makefile` — add `reproducible-build-check` target depending on `build`; consumes `dist/*.tar.gz` and `dist/*.whl`.
- `.github/workflows/ci.yml` — add `reproducible-build` matrix job across Python 3.11/3.12/3.13.
- `.github/workflows/release.yml` — add a `Reproducible build verification` step after `Build distributions` and before provenance signing; fail the release on mismatch.
- `tests/test_reproducible_build.py` — unit tests over the report shape validator plus a fixture-backed test that runs the verifier against two identical synthetic wheel bytes.
- `tests/fixtures/reproducible_build/` — fixture report and expected hash.
- `docs/operations/reproducible_build.md` — policy: what reproducibility means here (sdist→wheel), known non-determinism sources to avoid (timestamps, filesystem ordering, generated files), and the boundary that this gate does not verify provenance signing, SBOM correctness, or benchmark reproduction.
- `docs/architecture/schema_changelog.md` — add `Reproducible Build Report 1.0` entry.

Tests:
- Report shape validation (ok/sdist/wheels/report_hash/reproduction_claimed boundary language).
- Deterministic `report_hash` over identical inputs.
- Mismatch detection when rebuilt wheel bytes differ.
- Missing/malformed input exits 3.
- CI job passes on the real package across all three Python versions.

Stop conditions:
- `make reproducible-build-check` is green locally and in CI on all supported Python versions.
- `release.yml` gates publishing on the new step.
- Canonical audit hash unchanged; no reproduction claim introduced; no live service contact.

## Remaining Open Questions

- If the current build is non-reproducible, does the maintainer want to (a) fix the build in this slice, or (b) land the gate as advisory-only first and track the fix separately? Recommendation: land the gate as a hard release blocker in `release.yml` and fix any surfaced defect in the same slice, since advisory-only does not improve release safety.
- Should the gate also compare the rebuilt wheel's RECORD file against the published wheel's RECORD to localize non-determinism? Optional polish; not required for the core supply-chain invariant.

[usage] {"completion_tokens": 2596, "completion_tokens_details": {"reasoning_tokens": 1553}, "prompt_tokens": 71640, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 74236}
