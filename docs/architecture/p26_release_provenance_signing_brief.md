# P26 Release Provenance Signing Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p26_release_attestation_plan.md` and
`docs/architecture/glm_p26_release_attestation_convergence.md`.

## Purpose

P26 adds a detached Ed25519 signature sidecar for the deterministic P25 release
provenance manifest. The signed payload is the exact manifest file bytes on
disk, not a parsed or re-serialized representation.

This hardens release custody while preserving local contributor ergonomics:
manifest verification remains mandatory in release smoke; signature verification
is automatic when a sibling sidecar is present or fail-closed when explicitly
requested.

## Implemented

- `self_harness.corpus_signing` now exposes exact-byte Ed25519 helpers for
  signing, verification, public-key derivation, and raw public-key encoding.
- `self_harness.signing.sign_payload_with_external_signer` generalizes the
  existing external signer protocol without changing protocol version `1`.
- `scripts/sign_provenance.py` writes `MANIFEST.sig` sidecars from either a
  local private PEM key or an external signer command.
- `scripts/verify_provenance_signature.py` validates sidecar schema, manifest
  filename, manifest SHA-256, fingerprint, public key, and Ed25519 signature.
- `scripts/release_smoke.py` verifies a sibling signature sidecar when present
  and fails closed for explicit `--provenance-signature` inputs.
- `make provenance-sign` signs generated provenance when an operator provides
  `RELEASE_PROVENANCE_KEY` or `RELEASE_PROVENANCE_EXTERNAL_SIGNER`.
- Tests cover local signing, encrypted key passphrases, external signer custody,
  manifest tampering, signature tampering, schema rejection, sidecar secret
  exclusion, exact-byte external signing, and release-smoke sidecar discovery.

## Sidecar Schema

Schema version `1` records:

- `manifest_filename`: basename of the signed provenance manifest;
- `manifest_sha256`: SHA-256 of the exact manifest bytes;
- `signature_algorithm`: currently `ed25519`;
- `signature_b64`: base64 Ed25519 signature;
- `public_key_b64`: base64 raw Ed25519 public key bytes;
- `fingerprint`: SHA-256 SPKI DER fingerprint;
- `fingerprint_algorithm`: `sha256-spki-der-hex`;
- `key_id`: operator-provided or signer-provided key id;
- `provider`: `local-pem`, `external`, or a provider-specific label.

The sidecar intentionally avoids wall-clock timestamps, absolute paths, private
key material, passphrases, signer stderr, and secret-manager metadata.

## Trust Boundary

The sidecar attests the manifest bytes, not the artifacts directly. Artifact
hash and size verification stays in `scripts/verify_provenance.py` and
`scripts/release_smoke.py`; operators should run both manifest verification and
signature verification before publishing.

Embedded public keys let CI and release smoke detect tampering and malformed
sidecars. Production promotion still requires an operator-owned trusted public
key or external trust registry.

## Deferred

- Sigstore and PyPI OIDC attestations.
- Provider-specific KMS/HSM/YubiKey wrapper scripts.
- in-toto or SLSA certification.
- Cross-platform release-smoke runners beyond Ubuntu.
- Reproducible-build environment pinning beyond `SOURCE_DATE_EPOCH`.
