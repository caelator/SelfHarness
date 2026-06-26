# Operator Policy Binding

Operator policy binding verifies that the operator policy bundle and the
operator promotion manifest describe the same release-policy files.

The bundle is path-oriented: it tells offline gates which local policy files to
load. The promotion manifest is digest-oriented: it binds policy files to
SHA-256 digests and lifecycle states. Both can be valid on their own while
pointing at different files. This binding gate catches that drift.

## Local Verification

Run the standalone gate with the bundle and promotion manifest:

```bash
python scripts/operator_policy_binding_verify.py \
  --bundle ops/operator_bundle.json \
  --promotion ops/promotion.json \
  --today 2026-06-24 \
  --result-out dist/self-harness-operator-policy-binding.json
```

When the promotion manifest is signed, verify the signature at the same time:

```bash
python scripts/operator_policy_binding_verify.py \
  --bundle ops/operator_bundle.json \
  --promotion ops/promotion.json \
  --signature ops/promotion.json.sig \
  --trusted-public-key keys/operator-promotion.ed25519.pub \
  --result-out dist/self-harness-operator-policy-binding.json
```

`make operator-policy-binding-check` runs the fixture version of this gate.

## What It Checks

The verifier fails closed when:

- a bundle policy file is missing from active promotion entries;
- an active promotion entry points at a file not referenced by the bundle;
- an active promotion entry has a digest or byte size that does not match the
  bundle-referenced file;
- bundle or promotion JSON is malformed;
- an optional promotion signature sidecar fails verification.

Retired promotion entries are ignored so old policy material can remain in the
promotion history without blocking the current active bundle.

## Boundary

This report is release/operator material. It does not mutate audit artifacts,
does not change corpus or audit schemas, does not rotate readiness hashes, and
does not claim benchmark reproduction.

The verifier is intentionally offline. It reads local JSON files and optional
signature material; it does not contact Harbor, Docker, registries, scanners,
PyPI, Sigstore, models, or cloud providers.

`make release-candidate-evidence` consumes this report as a required
release-candidate gate. Missing, malformed, failed, or reproduction-claiming
binding evidence blocks the decision.
