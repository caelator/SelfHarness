# P16 Corpus Keyring Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p16_corpus_keyring_plan.md`.

## Purpose

P15 added offline corpus signing. P16 adds the operator trust layer needed for
production rotations: a portable keyring can map a `corpus_id` to one or more
trusted public keys and status values, and validation can require that a signed
corpus verify against an active key for its own corpus ID.

This is an input-provenance hardening slice. It does not modify audit schemas,
does not write private keys into keyrings or audit artifacts, and does not claim
Terminal-Bench reproduction.

## Implemented

- `corpus-keyring init --out PATH [--force]` writes an empty stable JSON keyring.
- `corpus-keyring add --keyring PATH --corpus-id ID --public-key PATH` embeds a
  normalized Ed25519 public key with a stable fingerprint.
- `corpus-keyring set-status` moves trusted keys between `active`, `retired`,
  and `revoked`.
- `corpus-keyring inspect --json` emits deterministic keyring JSON for review.
- `validate-tasks --require-corpus-keyring PATH` verifies a signed corpus
  against any active trusted key for the matching `corpus_id`.
- `local-demo --require-corpus-keyring PATH` applies the same signed-corpus
  trust gate before executing local subprocess tasks.
- Keyring loading recomputes fingerprints and rejects mismatched or private-key
  material.

## Deferred

- Signed or self-certifying keyrings.
- Provider-specific KMS/HSM trust-store integrations.
- Time-windowed rotation metadata.
- Audit fields recording keyring state.

## Schema

The keyring schema is operator-held release material, not an audit artifact:

```json
{
  "keyring_version": "1",
  "entries": [
    {
      "corpus_id": "local-smoke",
      "fingerprint": "<64 hex characters>",
      "fingerprint_algorithm": "sha256-spki-der-hex",
      "labels": {"environment": "ci"},
      "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
      "status": "active"
    }
  ]
}
```

Only `active` entries satisfy validation gates. Multiple active keys for a
single `corpus_id` are allowed during planned rotations.
