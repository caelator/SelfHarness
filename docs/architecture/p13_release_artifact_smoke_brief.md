# P13 Release Artifact Smoke Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p13_release_artifact_plan.md`.

## Purpose

Source-tree checks are necessary but not sufficient for a production Python
package. P13 proves that the built wheel installs into a fresh environment and
that the installed console script can reproduce the same deterministic audit
contract as the source tree.

This remains an offline release-readiness gate. It does not publish to PyPI,
does not exercise live providers, and does not claim Terminal-Bench
reproduction.

## Implemented

- `scripts/release_smoke.py` creates an isolated virtual environment, installs a
  built wheel, imports the stable API, runs the installed `self-harness` CLI,
  writes `trajectory.jsonl`, and compares the installed-run audit hash to
  `tests/fixtures/canonical_audit_hash.txt`.
- `make smoke` builds and runs the wheel smoke gate.
- `make release-smoke` chains source checks, readiness, build, and smoke.
- `make sbom` generates a local CycloneDX SBOM when release tooling is
  installed.
- P25 moved SBOM production out of `scripts/release_smoke.py`; release
  provenance includes an SBOM only when `make sbom` ran before provenance
  generation.
- CI includes a separate `release-smoke` matrix job so artifact-install failures
  are visible independently from source-test failures.
- P25 extends release smoke with deterministic release provenance verification
  before the isolated wheel install.

## Deferred

- Live Harbor/Docker Terminal-Bench execution.
- Real TestPyPI/PyPI publish round trips.
- Sigstore or other signed-artifact attestation verification.
- macOS/Windows release-smoke runners.
- Anthropic-extra smoke with live provider credentials.

## Schema

No audit schema bump. P13 validates the existing canonical audit hash and
trajectory bytes from an installed wheel.
