# P21 External Signer Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p21_external_signer_plan.md`.

## Purpose

P15-P17 added local corpus signing, keyrings, and encrypted local signing
material. P21 closes the next production custody gap by allowing
`corpus-sign` to delegate the Ed25519 signing operation to a trusted external
command. That command can wrap KMS, HSM, YubiKey, or platform-keychain tooling
without this package reading or storing signing material.

The verification model is unchanged: signed corpora still carry the same
`signature` field, and validators still verify standard Ed25519 signatures over
`corpus_integrity_payload(corpus)`.

## Implemented

- `self_harness.signing.external_signer` with a versioned stdin/stdout signer
  protocol.
- `ExternalSignerFailure` and `ExternalSignerError` with structured,
  machine-readable failure JSON.
- `corpus-sign --external-signer COMMAND` as a mutually exclusive alternative
  to `--private-key`.
- `--signer-timeout`, `--signer-max-output`, `--signer-provider`, `--key-id`,
  `--public-key`, and `--fingerprint` operator controls.
- No `shell=True`; the command string is parsed into argv and invoked with
  `subprocess.run`.
- Tests for verifier-compatible external signatures, deterministic fixture
  signing, timeout, oversize stdout, malformed JSON, nonzero exit, missing
  fields, request mismatch, stderr failure JSON, and core import without
  `cryptography`.

## Protocol

The signer receives JSON on stdin:

```json
{
  "schema_version": 1,
  "request_id": "self-harness-...",
  "deadline_ms": 15000,
  "payload_b64": "..."
}
```

`payload_b64` is the exact canonical corpus integrity bytes used by the local
signer and verifier, not a digest. The signer writes JSON on stdout:

```json
{
  "schema_version": 1,
  "signature_b64": "...",
  "public_key_b64": "...",
  "fingerprint": "64 lowercase hex characters",
  "key_id": "provider key identifier",
  "provider": "provider name",
  "request_id": "self-harness-..."
}
```

The signed corpus file still contains exactly `corpus_version`, `corpus_id`,
`tasks`, `checksum`, and `signature`.

## Deferred

- Real cloud KMS credentials or live HSM/YubiKey hardware in CI.
- Provider-specific wrappers maintained inside this package.
- Platform-keychain wrappers.
- Streaming signer payloads for very large corpora.
- Audit schema changes or reproduction claims.

## Schema

No corpus, keyring, or audit schema change. Public-key provenance is printed in
CLI output so operators can add the key to a keyring or validate it
out-of-band.
