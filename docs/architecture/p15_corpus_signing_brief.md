# P15 Corpus Signing Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p15_corpus_signing_plan.md`.

## Purpose

P8 added Ed25519 signature verification for production task corpora. P15 closes
the offline authoring half of that workflow: operators can generate a signing
keypair, sign a versioned corpus, inspect a stable public-key fingerprint, and
validate the signed corpus through the existing loader.

This is an input-provenance hardening slice. It does not modify audit schemas,
does not write private keys into corpora or audit artifacts, and does not claim
Terminal-Bench reproduction.

## Implemented

- `corpus-keygen --out PATH [--force]` writes an Ed25519 private key PEM and
  public key PEM.
- `corpus-sign --corpus PATH --private-key PATH --out PATH` writes a stable
  signed corpus JSON envelope with exactly `corpus_version`, `corpus_id`,
  `tasks`, `checksum`, and `signature`.
- `corpus-fingerprint --public-key PATH` reports a stable
  `sha256-spki-der-hex` fingerprint.
- Lazy cryptography imports keep core package import working without optional
  extras.
- Tests cover sign/verify round-trip, tamper detection, checksum mismatch,
  PEM/raw/base64 fingerprint equivalence, CLI JSON output, overwrite safety,
  and private-key exclusion from signed corpora.

## Deferred

- Provider-specific HSM/KMS wrapper implementations.
- Automated key rotation tooling.
- Signing captured Terminal-Bench fixtures.

## Schema

No audit schema change. The signed corpus envelope reuses the existing corpus
metadata fields: `checksum` and `signature` are outside the signed payload.
