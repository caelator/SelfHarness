# P11 Harbor Artifact Ingestion Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p11_harbor_artifact_ingest_plan.md` and
`docs/architecture/glm_p11_harbor_artifact_ingest_convergence.md`.

P11 adds an artifact ingestion boundary for preserved Harbor run directories.
The parser is explicitly candidate until a real Harbor run is captured and the
layout is validated.

## Implemented

- `harbor-inspect` CLI for stable redacted tree inspection with file sizes and
  SHA-256 hashes.
- `harbor-ingest` CLI for offline conversion of preserved Harbor artifacts into
  schema `1.4` audit directories.
- `HarborArtifactProvenance` and `HarborTrialRecord` with per-field source
  attribution.
- Reward parsing for JSON numbers, JSON objects, and plain text rewards.
- Generic JSONL trajectory parsing into `TraceEvent` rows.
- `terminal-bench --mode live --keep-run-dir <path>` support.
- Provenance validation requiring `harbor_artifact_validation_status="validated"`
  before any future reproduction claim.

## Remaining Boundary

The first real Harbor run must be inspected with `harbor-inspect`. Until that
output is reviewed and the layout is marked validated, ingested audits remain
candidate or partial evidence and cannot support a reproduction claim.
