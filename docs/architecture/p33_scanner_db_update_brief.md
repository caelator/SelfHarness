# P33 Scanner DB Update Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p33_production_readiness_plan.md` and
`docs/architecture/glm_p33_production_readiness_convergence.md`.

## Purpose

P33 adds deterministic Trivy DB update command orchestration so the cache
directory validated by P32 freshness checks has an explicit operator-maintained
update path.

## Implemented

- `ScannerDbUpdateCommand`, `ScannerDbUpdateResult`, and
  `build_trivy_db_update_command` under `self_harness.scanner_db_update`.
- `scripts/scanner_db_update.py` for dry-run and live operator execution.
- `make scanner-check` dry-run coverage for DB update command construction.
- Tests for command construction, dry-run behavior, missing-Trivy live
  fail-closed behavior, validation errors, and CLI JSON output.

## Boundary

This slice constructs and optionally executes Trivy's DB update command. CI
uses dry-run only and does not install Trivy, download databases, contact
registries, run Docker, discover Harbor images, or validate live Terminal-Bench
execution.

No audit schema, corpus schema, release provenance schema, readiness hash, or
reproduction-claim semantics are changed.

## Deferred

- Live scanner DB download/update verification.
- Private registry mirror authentication helpers.
- Automated DB rotation and cleanup.
- CI execution of real Trivy against real images.
- Sigstore/PyPI attestations.
