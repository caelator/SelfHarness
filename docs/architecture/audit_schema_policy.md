# Audit Schema Policy

Self-Harness audit artifacts are append-only operational evidence. Readers must
be conservative so old runs stay inspectable and new fields do not silently
change meaning.

## Version Rules

- Additive fields bump the minor schema version.
- Breaking changes bump the major schema version.
- Breaking changes must include a `migrate_vN_vM.py` shim before the new major
  version becomes the default writer version.
- Readers may accept known older minor versions.
- Readers must reject unknown major versions.

## Current Contract

The default writer schema is `1.2`.

Supported reader versions are defined in `self_harness.audit.SUPPORTED_SCHEMA_VERSIONS`
and tracked in `docs/architecture/schema_changelog.md`.

## Non-Goals

- No automatic migration is required for additive minor releases.
- No prompt or raw provider-output logging is added to audit artifacts until a
  leakage review exists.
