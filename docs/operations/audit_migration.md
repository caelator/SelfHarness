# Audit Migration

`self-harness audit-migrate` is release/operator tooling for upgrading audit
schema metadata in a copied audit tree. It never mutates the source directory
and it is not benchmark reproduction evidence.

## Usage

```bash
self-harness audit-migrate runs/old-audit --out runs/old-audit.schema-1.4
self-harness audit-migrate runs/schema-1.2 --target-major 1 --out runs/schema-1.2.schema-1.4
```

The command defaults to the latest readable audit schema version and writes a
structured JSON report containing the source hash, destination hash, changed
files, and release/operator boundary statement.

When `--target-major` is supplied, the source manifest must carry an explicit
`schema_version`. This keeps breaking-schema migration separate from the
historical pre-versioned `1.0` compatibility path.

## Rules

- Migrations are upgrade-only.
- The destination directory must not already exist.
- Schema `1.0` is detected when `manifest.json` has no `schema_version`,
  matching the historical pre-versioned audit layout.
- Unknown schema versions, malformed manifests, downgrade requests, and
  already-current targets fail closed.
- Source and migrated output manifests must not claim benchmark reproduction.
- Source audit bytes are hashed before and after migration; any source mutation
  fails closed.
- Migration output may be used as a compatibility copy, but historical source
  audit evidence should remain unchanged.

## Breaking-Schema Framework

Breaking-schema migration is modeled as an explicit transform registry. Built-in
transforms cover the supported audit schema chain `1.0 -> 1.1 -> 1.2 -> 1.3 ->
1.4` and are classified as `lossless`. Each migrated destination manifest gains:

- `migration_applied=true`;
- `migration_provenance.schema_version`;
- source audit hash;
- source and target schema versions;
- transform ids;
- classification: `lossless`, `lossy`, or `unsupported`;
- operator notes and whether lossy migration was allowed.

Operator override registries are JSON files passed with `--transforms-json`.
They are local, in-repo release/operator material. No plugin or entry-point
surface is loaded.

Supported operator transform shape:

```json
{
  "schema_version": "1.0",
  "transforms": [
    {
      "id": "drop-legacy-field",
      "source_schema_version": "1.0",
      "target_schema_version": "1.4",
      "classification": "lossy",
      "drop_manifest_fields": ["legacy_drop"],
      "notes": ["drops a field with no schema 1.4 equivalent"]
    }
  ]
}
```

Lossy transforms are drop-only. They may remove explicit manifest fields, but
they may not rename, remap, synthesize, or silently reinterpret historical
evidence. Lossy transforms require `--allow-lossy`; without that flag the
command exits with a structured error.

Unsupported transforms are accepted only as explicit fail-closed registry
entries. They are useful for documenting a known impossible path, but they never
execute.

## Standalone Gate

Run the offline fixture matrix with:

```bash
make migration-check
```

This gate verifies schema `1.0` through `1.4` fixtures, deterministic migrated
hashes, lossy rejection/success behavior, unsupported transform rejection,
source immutability, and no reproduction claim. It is a standalone production
gate and is not a `release-smoke` prerequisite.

## Boundaries

The migration command updates schema-version metadata and compatible additive
fields in the copied tree. It does not rotate canonical readiness fixtures,
change the default demo writer, contact external systems, load plugin
transforms, or claim Terminal-Bench reproduction.
