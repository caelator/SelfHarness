# P25 Release Provenance Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p25_release_provenance_plan.md` and
`docs/architecture/glm_p25_release_provenance_convergence.md`.

## Purpose

P25 binds local release artifacts together with a deterministic provenance
manifest. This hardens the existing build, release-smoke, and optional SBOM flow
without requiring PyPI, Sigstore, Harbor, Docker, KMS, HSM, or live credentials.

## Implemented

- `scripts/build_provenance.py` writes
  `dist/self-harness-<version>-provenance.json`.
- `scripts/verify_provenance.py` recomputes SHA-256 hashes and byte sizes for
  manifest-referenced artifacts.
- `make provenance` builds distributions and writes the manifest.
- `make smoke` and `make release-smoke` require and verify the manifest before
  installing the wheel in an isolated environment.
- Release CI generates and verifies provenance before artifact upload and PyPI
  publishing steps.
- Tests cover deterministic manifest generation, optional SBOM omission,
  tamper rejection, and schema-version rejection.

## Manifest

The manifest schema is `1.0` and records:

- package name, version, and Python requirement from `pyproject.toml`;
- build backend and installed `build` module version;
- best-effort source metadata: git commit, git dirty state, and
  `SOURCE_DATE_EPOCH` if supplied;
- artifact kind, basename, SHA-256, and byte size for each wheel, source
  distribution, and optional SBOM.

The manifest intentionally avoids wall-clock timestamps, absolute paths, and
environment-specific build directories.

## Trust Boundary

The provenance manifest is release material, not an audit artifact. It does not
change Self-Harness audit schemas, task corpora, benchmark provenance, or
reproduction-claim rules. P26 adds a detached signature sidecar over the exact
manifest bytes; publishing authorities remain a separate trust boundary.

## Deferred

- Sigstore and PyPI OIDC attestations.
- Provider-specific KMS/HSM/YubiKey wrapper scripts.
- in-toto or SLSA certification.
- Cross-platform release-smoke runners beyond Ubuntu.
- Reproducible-build environment pinning beyond `SOURCE_DATE_EPOCH`.
