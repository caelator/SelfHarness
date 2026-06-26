# P17 Encrypted Signing Keys Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p17_encrypted_signing_keys_plan.md`.

## Purpose

P15 added offline corpus signing and P16 added keyring trust manifests. P17
hardens the private-key side of that workflow by allowing operators to generate
and use encrypted PKCS8 Ed25519 private keys without introducing interactive
prompts, KMS/HSM dependencies, audit schema changes, or keyring schema changes.

## Implemented

- `generate_keypair(passphrase=...)` emits encrypted PKCS8 private-key PEM when
  a passphrase is provided.
- `sign_corpus(..., passphrase=...)` loads encrypted private keys and keeps the
  old unencrypted path compatible.
- `corpus-keygen` and `corpus-sign` support exactly one passphrase source:
  `--passphrase`, `--passphrase-file`, or `--passphrase-env`.
- Missing or incorrect passphrases fail with the fixed redacted message:
  `private key passphrase is required or incorrect`.
- CLI JSON reports whether a generated private key is encrypted without printing
  passphrases or private-key bytes.
- Tests cover encrypted API and CLI round trips, passphrase-file and
  passphrase-env sources, redacted failures, and passphrase exclusion from
  signed corpus/keyring JSON.

## Deferred

- Provider-specific KMS/HSM wrapper implementations.
- GPG, YubiKey, or platform keychain adapters.
- Passphrase complexity policies.
- Custom KDF parameter tuning beyond cryptography's best-available PKCS8 PEM
  envelope.

## Schema

No audit schema or keyring schema changes. Encrypted private keys remain
operator-held release material and must never be written into corpus JSON,
keyring JSON, audit artifacts, or release notes.
