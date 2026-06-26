# P32 Scanner DB Freshness Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p32_production_readiness_plan.md` and
`docs/architecture/glm_p32_production_readiness_convergence.md`.

## Purpose

P32 extends the P31 scanner execution preflight from "DB metadata exists" to
"DB metadata is current enough for operator policy." This closes an offline
scanner workflow gap where a Trivy report could be generated from an expired or
stale advisory database.

## Implemented

- `TrivyDbMetadata`, `ScannerDbFreshnessPolicy`, and
  `ScannerDbFreshnessDecision` under
  `self_harness.scanner_db_freshness`.
- Strict JSON parsing for Trivy DB metadata `Version`, `NextUpdate`, and
  `UpdatedAt`.
- `scripts/scanner_run.py --db-freshness-policy` and `--db-dir` preflight
  wiring.
- Replay-mode DB freshness evaluation without requiring a Trivy executable.
- Structured preflight freshness decisions in scanner result JSON.
- `make scanner-check` coverage for dry-run command construction and offline
  DB freshness replay.

## Boundary

This slice validates supplied Trivy DB metadata only. It does not install Trivy,
download or update scanner databases, pull images, contact registries, run
Docker, discover Harbor images, or validate live Terminal-Bench execution.

No audit schema, corpus schema, release provenance schema, readiness hash, or
reproduction-claim semantics are changed.

## Deferred

- Live scanner DB download/update orchestration, with deterministic dry-run
  command construction implemented later in P33.
- CI execution of real Trivy against real images.
- Provider-specific registry/OAuth/secret-manager helpers.
- Sigstore/PyPI attestations.
- Grype or multi-scanner support.
