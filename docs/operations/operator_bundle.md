# Operator Policy Bundle

Operator policy bundles collect release/operator policy file paths behind one
versioned JSON document. They are paths-only in schema `1`; bundle files must
not embed policy JSON, private keys, registry credentials, tokens, passphrases,
or secret-manager responses.

Bundles are release/operator material. They do not change audit schemas, corpus
schemas, readiness hashes, or benchmark reproduction claims.

## Schema

```json
{
  "bundle_version": "1",
  "owner": "release-engineering",
  "expires_on": "2026-12-31",
  "image_policy": "security/image-policy.json",
  "freshness_policy": "security/scanner-report-freshness.json",
  "vulnerability_policy": "security/vulnerability-policy.json",
  "scanner_db_freshness_policy": "security/scanner-db-freshness.json",
  "trusted_public_keys": ["keys/corpus.ed25519.pub"]
}
```

All policy paths are optional, but referenced files must exist. Relative paths
are resolved from the bundle file's directory. Bundles fail closed when they are
expired, malformed, use an unsupported version, reference missing files, or
include unknown top-level fields.

## Offline Preflight

Run the consolidated offline preflight before release or before handing policy
material to live scanner/Harbor jobs:

```bash
python scripts/operator_preflight.py \
  --bundle security/operator-bundle.json \
  --db-registry-config "$TRIVY_REGISTRY_CONFIG" \
  --harbor-url https://harbor.example \
  --harbor-project terminal-bench \
  --harbor-repository agents/verifier \
  --harbor-reference stable \
  --harbor-replay tests/fixtures/harbor/harbor_artifact_valid.json
```

`make operator-check` runs the same preflight against checked-in non-secret
fixtures.

## What It Checks

- bundle schema, expiry, and referenced file existence;
- image, freshness, vulnerability, and scanner DB freshness policy parsing via
  existing loaders;
- trusted public key readability and fingerprinting;
- deterministic Trivy scanner command construction;
- deterministic Trivy scanner DB update command construction;
- optional registry-config path existence without reading file contents;
- optional Harbor discovery dry-run or replay.

The preflight does not run Trivy, download scanner databases, contact Harbor,
run Docker, publish to PyPI, invoke Sigstore, or contact cloud providers.

## Secret Boundaries

Keep registry configs, private keys, KMS credentials, tokens, and passphrases
outside bundle files. The preflight reports paths and public-key fingerprints
only. Do not paste bundle reports containing internal paths into public release
notes without reviewing them first.
