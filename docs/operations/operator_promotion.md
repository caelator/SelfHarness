# Operator Policy Promotion

Operator promotion manifests version the release-policy files used by offline
production gates. They bind file paths, byte sizes, SHA-256 digests, lifecycle
status, and an optional detached Ed25519 signature.

Promotion material is release/operator material. It is not an audit artifact,
does not change readiness hashes, and does not claim benchmark reproduction.

## Manifest Lifecycle

Create a manifest, add policy files, and advance entries through monotonic
statuses:

```bash
self-harness operator-promotion init --manifest ops/promotion.json
self-harness operator-promotion add \
  --manifest ops/promotion.json \
  --name image_policy \
  --kind image_policy \
  --file ops/image_policy.json \
  --status draft
self-harness operator-promotion set-status \
  --manifest ops/promotion.json \
  --name image_policy \
  --status candidate
self-harness operator-promotion set-status \
  --manifest ops/promotion.json \
  --name image_policy \
  --status active
```

Supported kinds mirror the current operator bundle: `image_policy`,
`freshness_policy`, `scanner_db_freshness_policy`, `vulnerability_policy`, and
`trusted_public_keys`.

Statuses are `draft`, `candidate`, `active`, and `retired`. Transitions may
advance or stay at the same status; backwards transitions fail closed, and
retired entries cannot be reactivated.

## Signing And Verification

Sign the canonical manifest bytes with an operator-held Ed25519 key:

```bash
self-harness operator-promotion sign \
  --manifest ops/promotion.json \
  --private-key keys/operator-promotion.ed25519 \
  --public-key keys/operator-promotion.ed25519.pub \
  --provider local-pem \
  --key-id operator-promotion-2026q2 \
  --out ops/promotion.json.sig
self-harness operator-promotion verify \
  --manifest ops/promotion.json \
  --signature ops/promotion.json.sig \
  --trusted-public-key keys/operator-promotion.ed25519.pub \
  --json
```

For KMS, HSM, YubiKey, or platform-keychain custody, use the same external
signer protocol as corpus signing:

```bash
self-harness operator-promotion sign \
  --manifest ops/promotion.json \
  --external-signer "python ops/kms_signer_wrapper.py" \
  --provider aws-kms \
  --key-id alias/self-harness-operator-promotion \
  --fingerprint <64-hex-public-key-fingerprint> \
  --out ops/promotion.json.sig
```

The signature sidecar records the manifest hash, signature, public key,
fingerprint, provider, key id, and signing mode. Private keys, passphrases, and
secret-manager material must never be committed or copied into sidecars.

## Release Gate

`make operator-promotion-check` signs the checked-in fixture manifest with a
temporary private key in a `mktemp` directory, copies only the public key and
signature sidecar under `dist/`, verifies them, and writes
`dist/self-harness-operator-promotion-preflight.json`.

`make release-candidate-evidence` requires that preflight artifact. Missing,
malformed, failed, or reproduction-claiming promotion evidence blocks the
release-candidate decision.

The preflight is intentionally offline: it reads local files, verifies recorded
digests and optional signature material, and does not contact Harbor, Docker,
registries, scanners, PyPI, Sigstore, models, or cloud providers.
