# P31 Scanner Execution Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p31_production_readiness_plan.md` and
`docs/architecture/glm_p31_production_readiness_convergence.md`.

## Purpose

P31 closes the loop around the P28-P30 scanner policy tooling by adding an
operator-facing execution boundary. It lets release workflows validate Trivy
command construction offline, replay fixture reports deterministically, and run
live scans only when an operator environment has the required scanner binary and
optional DB metadata.

## Implemented

- `ScannerCommand`, `build_trivy_command`, `preflight_scanner`, and
  `run_scanner` under `self_harness.scanner_execution`.
- `scripts/scanner_run.py` for dry-run, replay, and live Trivy orchestration.
- Replay routing through the existing vulnerability, image-policy, and
  freshness policy evaluators.
- `make scanner-check` as a dry-run release gate.
- Tests for argv construction, dry-run non-writes, DB metadata preflight,
  replay success, image-policy rejection, stale-report rejection, and live
  missing-Trivy fail-closed behavior.

## Boundary

This slice does not install Trivy, refresh scanner databases, pull images,
contact registries, run Docker, discover Harbor images, or validate live
Terminal-Bench execution. It also does not change audit schema, corpus schema,
release provenance schema, readiness hashes, or reproduction-claim semantics.

## Deferred

- Live Harbor/Docker benchmark execution.
- Provider-specific registry/OAuth/secret-manager helpers.
- Scanner database freshness beyond metadata presence, implemented later in
  P32.
- CI execution of real Trivy against real images.
- Sigstore/PyPI attestations.
- Grype or multi-scanner support.
