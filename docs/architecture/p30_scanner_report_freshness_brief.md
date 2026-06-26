# P30 Scanner Report Freshness Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p30_production_readiness_plan.md` and
`docs/architecture/glm_p30_production_readiness_convergence.md`.

## Purpose

P30 adds a freshness gate for supplied Trivy scanner reports. It closes the
offline replay gap where an otherwise valid clean report could be reused after
it has become too old for release or operations policy.

## Implemented

- `FreshnessPolicy`, `load_freshness_policy`, `evaluate_freshness_policy`, and
  `load_trivy_report_timestamp` under `self_harness.freshness_policy`.
- `scripts/vuln_check.py --format trivy --audit-json report.json
  --freshness-policy policy.json` evaluates report creation time before
  declaring the vulnerability report acceptable.
- Trivy timestamps are read from top-level `CreatedAt` first, then
  `Metadata.CreatedAt`.
- The JSON report includes a `freshness` block with allow/deny status, code,
  message, report timestamp, evaluated date, age, and policy thresholds.
- Tests cover recent, stale, missing, malformed, future-dated, and
  `not_before` rejection paths.

## Boundary

This slice remains offline tooling. It does not install Trivy, run scanners,
pull images, contact registries, inspect scanner database age, or validate a
live Harbor execution. It only evaluates the timestamp carried by a supplied
Trivy JSON report against an operator-owned policy.

No audit schema, corpus schema, release provenance schema, readiness hash, or
reproduction-claim semantics are changed.

## Deferred

- Automated scanner execution.
- Scanner database freshness such as advisory DB update time.
- Grype or multi-scanner freshness policy.
- Live Harbor image discovery.
