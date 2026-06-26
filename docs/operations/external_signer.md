# External Corpus Signer Runbook

Use `corpus-sign --external-signer` when corpus signing material must remain in
a KMS, HSM, YubiKey, or platform-keychain wrapper outside Self-Harness.

## Contract

Self-Harness writes one JSON request to the signer command's stdin:

```json
{
  "schema_version": 1,
  "request_id": "self-harness-...",
  "deadline_ms": 15000,
  "payload_b64": "..."
}
```

The signer must sign `payload_b64` after base64 decoding it. The payload is
already canonicalized by Self-Harness; do not reserialize the corpus or sign a
different digest unless your wrapper signs that exact byte string internally.

The signer writes one JSON response to stdout:

```json
{
  "schema_version": 1,
  "signature_b64": "...",
  "public_key_b64": "...",
  "fingerprint": "64 lowercase hex characters",
  "key_id": "alias/self-harness-corpus",
  "provider": "aws-kms",
  "request_id": "self-harness-..."
}
```

Errors should exit nonzero and write this shape to stderr:

```json
{
  "schema_version": 1,
  "type": "external_signer_error",
  "code": "provider_unavailable",
  "message": "provider did not return a signature",
  "provider": "aws-kms",
  "key_id": "alias/self-harness-corpus",
  "request_id": "self-harness-...",
  "cause": "short provider hint with no secret material"
}
```

## Example

```bash
self-harness corpus-sign \
  --corpus path/to/corpus.json \
  --external-signer "python ops/kms_corpus_signer.py" \
  --signer-provider aws-kms \
  --key-id alias/self-harness-corpus \
  --fingerprint 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --out path/to/corpus.signed.json
```

For local protocol testing, `scripts/example_external_signer.py` implements the
same stdin/stdout contract with an Ed25519 PEM key read from
`SELF_HARNESS_EXAMPLE_SIGNER_KEY`:

```bash
export SELF_HARNESS_EXAMPLE_SIGNER_KEY=keys/example.ed25519
self-harness corpus-sign \
  --corpus path/to/corpus.json \
  --external-signer "python scripts/example_external_signer.py" \
  --signer-provider example-local-pem \
  --key-id example-local-ed25519 \
  --out path/to/corpus.signed.json
```

The example signer is reference material only. Do not point it at production
KMS, HSM, YubiKey, or cloud secret material. Production wrappers should keep
provider credentials outside Self-Harness and must not print key material,
passphrases, or secret-manager responses.

After signing, validate the output with an out-of-band trusted public key or a
keyring:

```bash
self-harness validate-tasks path/to/corpus.signed.json \
  --require-corpus-signature keys/corpus.ed25519.pub
```

## Operational Notes

- `--external-signer` and `--private-key` are mutually exclusive.
- Passphrase flags are valid only with `--private-key`.
- Default timeout is 15 seconds; default stdout cap is 16 KiB.
- Timeouts are clamped to 1-60 seconds; stdout caps are clamped to 256 bytes-1
  MiB.
- Self-Harness invokes the signer without `shell=True`.
- Public key and fingerprint trust remains an operator responsibility. Publish
  fingerprints through release notes or an internal trust registry before using
  them in keyrings.
- The signed corpus envelope does not store provider, key id, public key, or
  fingerprint. Keep that provenance in release records and keyrings.
