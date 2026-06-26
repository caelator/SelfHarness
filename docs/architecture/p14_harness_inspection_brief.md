# P14 Harness Inspection Brief

## Status

Implemented under internal review because the GLM bridge was unavailable for
this slice. Two `glm-architect-converge` attempts failed with Z.ai HTTP 429
overload responses. The slice is intentionally narrow and uses only existing
audit artifacts.

## Purpose

The Self-Harness paper emphasizes that useful harness improvement is an
empirical state transition: retained edits should be tied to evidence, promoted
through validation, and inspectable as small changes to declared harness
surfaces. P14 adds a first-class retained-edit inspection view so users do not
need to manually reconstruct the lineage from raw JSONL files.

This is a derived report. It does not rerun tasks, mutate audit artifacts,
change the audit schema, or claim benchmark reproduction.

## Implemented

- `inspect_harness_run(path)` builds a stable retained-edit report from an audit
  directory.
- `write_harness_inspection(path, out_path=None)` writes
  `harness_inspection.json`.
- `self-harness inspect-harness AUDIT_DIR` writes the report beside the audit.
- `self-harness inspect-harness AUDIT_DIR --json [--pretty]` prints the report
  to stdout.
- Reports include per-round harness hashes, accepted proposal ids, committed
  ops, reverse ops, changed surfaces, proposal statuses and reasons, final
  harness hash, final surface values, and per-surface value hashes.

## Deferred

- Visual diff rendering.
- Redaction policy for sensitive production harness values.
- Live Harbor/Docker Terminal-Bench reproduction.
- Schema migration shims; no breaking schema change is introduced.

## Schema

No primary audit schema bump. The derived harness inspection view has schema
`1.0`, tracked separately from audit schemas.
