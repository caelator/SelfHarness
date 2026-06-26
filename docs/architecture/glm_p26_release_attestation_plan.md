CONVERGED: YES

## Verdict
P26 is the right next locally implementable slice. The repo already has deterministic P25 provenance bytes, a dependency-guarded Ed25519 primitive layer (`self_harness.corpus_signing`), encrypted-key/passphrase handling, a versioned external signer protocol, and a release-smoke harness that already verifies the manifest. A detached Ed25519 signature over the manifest's canonical file bytes is a small, well-bounded addition that reuses those primitives without touching audit, corpus, or reproduction-claim schemas. The plan below is executable from current evidence.

## Critique
- The strongest reuse lever is the external signer protocol, but it is currently hard-wired to corpus integrity bytes (`_corpus_integrity_bytes(corpus)`). Reusing it for the manifest requires factoring out a payload-parameterized entry point. This is a low-risk generalization, not a schema change.
- The corpus signing helpers are reusable for private-key loading, passphrase handling, fingerprinting, and base64 signature encoding. Duplicating that logic would be a risk; importing `self_harness.corpus_signing` from the new script is the right call.
- The signature must be detached. Embedding signature fields inside the manifest would either invalidate the signed bytes after signing or force a two-pass write, and would conflict with P25's "unsigned content-addressed evidence" trust boundary stated in `docs/architecture/p25_release_provenance_brief.md`.
- The signed payload must be the exact UTF-8 bytes of the manifest file produced by `scripts/build_provenance.py`, not a re-serialization. Re-serializing would re-introduce nondeterminism risk and would diverge from what `verify_provenance.py` checks.
- Release-smoke should treat a present signature sidecar as an additional verification input, not silently ignore it. Making signing itself mandatory in `make release-smoke` would force every contributor to hold a key, which is the wrong default for local-only release material.

## Required Changes
None blocking. The recommended shape:

1. Generalize the external signer seam.
   - In `src/self_harness/signing/external_signer.py`, extract `sign_payload_with_external_signer(payload: bytes, command, *, provider, key_id, timeout_seconds, max_output_bytes, expected_fingerprint) -> ExternalSignerResponse`.
   - Re-implement `sign_corpus_with_external_signer` as a thin wrapper that calls it with `_corpus_integrity_bytes(corpus)`.
   - Keep `EXTERNAL_SIGNER_PROTOCOL_VERSION = 1` unchanged.

2. Add detached signing/verification scripts.
   - `scripts/sign_provenance.py`
     - Inputs: `--manifest PATH`, exactly one of `--private-key PATH` or `--external-signer COMMAND`, optional `--passphrase`, `--passphrase-file`, `--passphrase-env`, `--public-key PATH` (for embedding/recording), `--key-id`, `--provider`, `--fingerprint`, `--out PATH` (default `dist/self-harness-<version>-provenance.json.sig`).
     - Behavior: read manifest file bytes verbatim, sign those exact bytes, write a sidecar JSON file with stable serialization.
     - Sidecar schema v1:
       ```json
       {
         "schema_version": 1,
         "manifest_filename": "self-harness-<version>-provenance.json",
         "manifest_sha256": "<64 hex>",
         "signature_algorithm": "ed25519",
         "signature_b64": "...",
         "public_key_b64": "...",
         "fingerprint": "<64 hex>",
         "key_id": "...",
         "provider": "local-pem | external | ..."
       }
       ```
     - Reuse `self_harness.corpus_signing` for PEM/private-key loading, passphrase handling, `public_key_fingerprint`, and the redacted `PASSPHRASE_ERROR` behavior.
     - Reject if manifest bytes do not match the sidecar's `manifest_sha256` on round-trip self-check.
     - Never write private key material, passphrases, or signer stderr to the sidecar.
   - `scripts/verify_provenance_signature.py`
     - Inputs: `--manifest PATH`, `--signature PATH`, `--public-key PATH|BYTES|B64` (accept the same formats `public_key_fingerprint` accepts).
     - Behavior: load manifest bytes, recompute SHA-256, compare to `manifest_sha256`, load public key, verify Ed25519 signature over manifest bytes, recompute and compare `fingerprint`.
     - Stop conditions: missing manifest, missing signature, schema mismatch, manifest digest mismatch, signature verification failure, fingerprint mismatch, unsupported algorithm.

3. Make release-smoke aware of the sidecar, but do not require it.
   - In `scripts/release_smoke.py`, after `_verify_provenance(...)`, if a sibling `*.sig` exists next to the manifest or `--provenance-signature` is passed, invoke `verify_provenance.py`'s sibling verifier. If the sidecar is explicitly requested and missing/invalid, fail closed.
   - Default behavior remains: manifest verification is required; signature verification is opt-in.

4. Makefile wiring (optional but warranted).
   - Add `provenance-sign`: depends on `provenance`, requires `RELEASE_PROVENANCE_KEY` (path) or `RELEASE_PROVENANCE_EXTERNAL_SIGNER`, plus optional `RELEASE_PROVENANCE_PASSPHRASE_FILE` / `RELEASE_PROVENANCE_FINGERPRINT`.
   - Do not add `provenance-sign` to the default `release-smoke` dependency chain.
   - Document that signature sidecars are release material, not audit artifacts, mirroring the P25 trust boundary language.

5. CI wiring (optional, non-blocking for convergence).
   - In the release workflow, after `make provenance`, if signing inputs are present (via repository secrets), run `make provenance-sign` and upload the sidecar alongside the manifest. Do not fail the workflow if signing inputs are absent; treat signing as an operator-promoted step.
   - Add a CI-only job that generates an ephemeral Ed25519 keypair, signs, and verifies, to keep the path green without persisting keys.

6. Tests.
   - Extend `tests/test_release_provenance.py` (or add `tests/test_release_provenance_signing.py`):
     - generate keypair → sign manifest → verify sidecar; deterministic across reruns.
     - encrypted private key via `--passphrase-env`, with redacted failure on wrong/missing passphrase.
     - external signer path using the existing fixture pattern from `tests/test_external_signer.py`, generalized through the new `sign_payload_with_external_signer`.
     - tamper rejection: edit manifest bytes after signing → verifier fails with manifest digest mismatch.
     - tamper rejection: edit signature → verifier fails.
     - schema mismatch rejection (`schema_version != 1`).
     - sidecar contains no `PRIVATE KEY`, no passphrase bytes.
     - fingerprint recomputation equivalence between PEM, raw bytes, and base64 raw (reuse `public_key_fingerprint`).
     - core-import-without-cryptography invariant still holds for `import self_harness`.
     - `release_smoke.py` end-to-end: when sidecar is present, it is verified; when absent, the smoke gate still passes.

7. Docs.
   - Update `RELEASE.md` with a "Release Provenance Signature" section: detached sidecar, operator-held key or external signer, optional Makefile target, no audit/corpus schema impact.
   - Add `docs/architecture/p26_release_provenance_signing_brief.md` mirroring the P25/P21 brief structure.
   - Add an entry to `docs/architecture/productionization_brief.md` once implemented.

## Revised Plan
Summary of the converged execution slice:

- Generalize `external_signer.py` to expose `sign_payload_with_external_signer(payload, ...)`. Keep protocol version 1. Corpus function becomes a wrapper.
- Add `scripts/sign_provenance.py` and `scripts/verify_provenance_signature.py` reusing `self_harness.corpus_signing` for PEM, passphrase, fingerprint, base64 signature, and redacted passphrase errors.
- Sign exact manifest file bytes; record `manifest_sha256`, `signature_algorithm = "ed25519"`, `signature_b64`, `public_key_b64`, `fingerprint`, `key_id`, `provider`, `schema_version = 1`.
- Extend `release_smoke.py` to verify a sidecar when present or explicitly requested; fail closed on explicit-missing/invalid, pass through when absent.
- Add optional `make provenance-sign`; do not chain it into `release-smoke`.
- Add CI job that signs and verifies with an ephemeral keypair; gate production upload on it only when signing secrets are configured.
- Tests: roundtrip, encrypted key, external signer, tamper on manifest bytes and on signature, schema mismatch, no-private-material invariant, fingerprint equivalence, core-import-without-cryptography invariant, release-smoke sidecar-aware path.
- Docs: `RELEASE.md` section, P26 brief, productionization brief entry.
- Stop conditions: digest mismatch, signature verification failure, fingerprint mismatch, schema mismatch, missing required inputs when signing explicitly requested, private-key/passphrase leakage check.
- Deferred: Sigstore/PyPI attestations, KMS/HSM/YubiKey wrappers inside the package, embedding signatures inside the manifest, in-toto/SLSA, cross-platform release-smoke runners, reproducible-build environment pinning beyond `SOURCE_DATE_EPOCH`.

## Remaining Open Questions
Non-blocking, decided by reasonable defaults:

1. Should `make provenance-sign` be a hard dependency of the published release workflow? Default: no — signing is operator-promoted; CI exercises the path with an ephemeral key only.
2. Should the sidecar filename embed the version or stay adjacent to the manifest? Default: `dist/self-harness-<version>-provenance.json.sig`, mirroring the manifest naming already used by `build_provenance.py`.
3. Should we record `signed_at` timestamps in the sidecar? Default: no, to preserve deterministic, environment-independent release material, consistent with P25's avoidance of wall-clock timestamps.
4. Should `verify_provenance_signature.py` also re-run `verify_provenance.py`'s artifact hash checks? Default: no — verify manifest-byte integrity only; artifact verification stays in `verify_provenance.py` and `release_smoke.py`. Operators run both.
5. Should the external signer protocol gain a `payload_kind` field to distinguish corpus vs manifest payloads? Default: not in P26; protocol remains payload-agnostic and the sidecar records `signature_algorithm` and `manifest_sha256` for out-of-band interpretation.
