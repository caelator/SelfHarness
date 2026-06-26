# Round 1

CONVERGED: YES

## Verdict
P25 should be a local, deterministic **release provenance manifest** slice that hardens the existing `build`/`release-smoke`/optional SBOM flow without introducing PyPI, Sigstore, Docker, Harbor, cloud KMS, or live credentials. The repo already has the required seams: `make build`, `make sbom`, `scripts/release_smoke.py`, a canonical audit hash fixture, and release CI. The missing piece is a single auditable manifest that binds wheel, sdist, optional SBOM, package version, builder, and source revision together, plus a verify step inside `release-smoke`. This is fully implementable from current repo evidence and does not require an audit schema change.

## Critique
- Evidence: P13 (`scripts/release_smoke.py`, `make smoke`, `make release-smoke`, `make sbom`) already proves wheel installability and canonical audit hash parity, and CI already uploads `dist/*` and `sbom/*` artifacts. The natural next hardening step is to bind those artifacts cryptographically by content hash in a portable manifest.
- Evidence: `pyproject.toml` already exposes `build`, `cryptography`, and `cyclonedx-bom` via optional extras, so no new core dependency is needed and the core package remains dependency-free.
- Evidence: RELEASE.md and the productionization brief establish a strong "no reproduction claim, no external services" posture. A provenance manifest fits cleanly: it is a release-material artifact, not an audit-schema artifact.
- Inference: The project wants local determinism, so the manifest must avoid wall-clock timestamps and absolute paths; it should record `SOURCE_DATE_EPOCH` if present and git revision in a fail-safe way.
- Inference: Signing (Sigstore/KMS/detached Ed25519) is intentionally out of scope and should remain deferred; operators already have corpus signing tooling for trusted corpora, and artifact signing is a distinct trust boundary.

## Required Changes
- The plan must not modify the audit schema or manifest schema. The provenance manifest is a release artifact living under `dist/`, not under audit directories.
- The plan must not require SBOM. SBOM is optional; when present, its hash is included; when absent, the field is omitted or `null`.
- The plan must not introduce nondeterminism: no wall-clock timestamps, no absolute build paths, no locale-dependent fields.
- The plan must fail closed in `release-smoke`: if a provenance manifest exists, hashes must verify; if building, the manifest must be produced and self-consistent.
- The plan must preserve paper-fidelity invariants: `make readiness` and the canonical audit hash comparison remain unchanged.
- The plan must not add new core package dependencies. New tooling belongs in `scripts/` and the `release` extra.

## Revised Plan
### Goal
Add a local, deterministic release provenance manifest for built wheel, sdist, and optional SBOM, and verify it inside `release-smoke`. No external services, no signing, no schema changes.

### Files
- `scripts/build_provenance.py` (new): emits `dist/self-harness-<version>-provenance.json`.
  - Inputs: `dist/*.whl`, `dist/*.tar.gz`, optional `sbom/self_harness-sbom.json`.
  - Fields: `schema_version: "1.0"`, `package_name`, `package_version`, `python_requires`, `builder: {"build_module": "build", "build_module_version": <importlib.metadata.version("build") or "unknown">, "backend": "hatchling"}`, `source: {"git_commit": <git rev-parse HEAD or "git-unavailable">, "git_dirty": <bool or null>, "source_date_epoch": <env or null>}`, `artifacts: [{"kind": "wheel"|"sdist"|"sbom", "filename": <basename>, "sha256": <hex>, "size_bytes": <int>}]`.
  - Behavior: deterministic JSON (sort_keys, indent=2, trailing newline). No timestamps, no absolute paths. Missing git is non-fatal and recorded as `"git-unavailable"`. Missing SBOM is omitted from `artifacts`.
- `scripts/verify_provenance.py` (new): recomputes hashes for referenced artifacts under `dist/` and `sbom/`, compares to manifest, exits nonzero on mismatch, missing file, or schema violation.
- `scripts/release_smoke.py` (modified):
  - Discover `dist/*-provenance.json`; if present, run `verify_provenance` against the installed wheel/sdist and, if SBOM exists, the SBOM file.
  - If `--provenance` is explicitly passed, require it; otherwise auto-discover and require it when any dist artifact exists.
  - Failure to verify is a hard release-smoke failure.
- `Makefile` (modified):
  - Add `provenance: build` target invoking `scripts/build_provenance.py`.
  - Change `build` target? Keep `build` as-is; provenance is a separate target so source-only `build` remains deterministic and side-effect-free.
  - Update `release-smoke` to depend on `provenance` and, when `sbom` exists, include it: `release-smoke: check readiness build provenance smoke`.
  - Keep `sbom` optional; `provenance` must succeed with or without SBOM.
- `pyproject.toml` (modified):
  - Add `build>=1.2` is already in `release`; no new dependency. Optionally pin nothing new.
  - No change to core `dependencies = []`.
- `.github/workflows/release.yml` and `ci.yml` (modified):
  - In `release-smoke` and release jobs, run `make provenance` after `make build` and before smoke.
  - Upload `dist/*-provenance.json` alongside `dist/*` and `sbom/*`.
- `tests/test_provenance.py` (new):
  - Unit tests using `tmp_path`: build a fake `dist/` and `sbom/`, run `build_provenance.py`, run `verify_provenance.py`, assert fields and verification.
  - Tamper tests: mutate a byte in wheel/sdist/sbom and assert verification fails.
  - Absence tests: missing git, missing SBOM, multiple wheels, schema version mismatch.
- `docs/architecture/p25_release_provenance_brief.md` (new):
  - Purpose, manifest schema, determinism rules, optional SBOM handling, stop conditions, and deferred work (Sigstore, KMS-backed signing, in-toto/SLSA, PyPI attestations, reproduction claims).
- `RELEASE.md` (modified):
  - Add a "Release Provenance Manifest" section: `make build`, `make provenance`, `make release-smoke`; manifest path; that it is required before tagging; that it is not a signed attestation.
- `docs/architecture/productionization_brief.md` (modified):
  - Add P25 implemented entry after P24.

### Tests
- Unit: manifest generation is deterministic across runs for identical inputs.
- Unit: verification passes for pristine artifacts and fails for any tampered referenced artifact.
- Unit: missing SBOM does not block; present SBOM is hashed and verified.
- Unit: missing git records `"git-unavailable"` and is non-fatal.
- Integration: `make release-smoke` end-to-end on CI produces and verifies the manifest and uploads it.

### Stop Conditions
- `make provenance` produces `dist/self-harness-<version>-provenance.json` deterministically.
- `make release-smoke` fails if manifest is missing, malformed, or hash-mismatched.
- CI release-smoke and release jobs run provenance build and verify on all supported Python versions.
- Docs updated: P25 brief, RELEASE.md, productionization brief.
- No audit schema change, no core dependency change, no external service required.

### Deferred
- Sigstore / PyPI OIDC attestations.
- KMS/HSM/YubiKey-backed manifest signing.
- in-toto / SLSA provenance level certification.
- Reproducible-build environment pinning beyond `SOURCE_DATE_EPOCH`.
- Cross-platform release-smoke runners beyond Ubuntu.
- Binding manifest to operator corpus signing keys (separate trust boundary).

## Remaining Open Questions
- Should the manifest include a `source_archive_sha256` computed from `git archive` output for environments without a built sdist? Current plan: no; only artifacts physically present in `dist/` are referenced. This is non-blocking and can be revisited in a later slice.
- Should `make build` automatically produce the manifest, or keep it as a separate target? Plan keeps it separate to preserve the determinism and side-effect profile of `build`; non-blocking.
- Should the manifest optionally support a detached Ed25519 signature reuse path from existing corpus key tooling? Deferred; non-blocking for P25 because unsigned content-addressed provenance already raises the bar over the current state.

[usage] {"completion_tokens": 2807, "completion_tokens_details": {"reasoning_tokens": 879}, "prompt_tokens": 8441, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 11248}
