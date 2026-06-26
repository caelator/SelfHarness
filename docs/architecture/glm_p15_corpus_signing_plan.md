CONVERGED: YES
## Verdict
P15 is a small, well-scoped additive slice that fills the generation half of the already-implemented P8 verification path. The repository already exposes a canonical integrity payload, checksum, signature verification, structured `invalid-signature` load failures, and `--require-corpus-signature` gates. P15 only needs to add offline key generation, offline signing, and fingerprint inspection, then reuse the existing loader. All material design decisions are decidable from the abstract task plus existing evidence, so the plan is executable.

## Critique
- Evidence (`src/self_harness/corpus.py`, `tests/test_corpus.py`, `tests/test_cli.py`): verification already exists; `TaskCorpus` already carries optional `checksum` and `signature`; `corpus_integrity_payload` is the canonical, stable signed payload; `load_corpus(verify_signature_key=...)` and `verify_corpus_signature(...)` are stable public API. P15 must produce artifacts compatible with this exact verification path, not invent a new envelope.
- Evidence (`README.md`, `RELEASE.md`, `docs/architecture/p8_input_integrity_brief.md`): provenance is an optional extra (`provenance`), canonical audit hashes and corpus integrity must remain deterministic, and key rotations must be recorded in release notes. P15 key generation therefore must not touch audit byte layout and must remain optional-dependency gated.
- Inference: the new CLI commands should live alongside `validate-tasks`/`local-demo`, return stable JSON for machine consumers, and never emit private key bytes into any corpus, audit directory, or stdout.
- Risk considered: deterministic corpus file output. Signing must serialize the signed corpus with the same stable JSON rules used elsewhere so that downstream `audit-diff`/checksum tooling stays byte-stable.
- Risk considered: public key format compatibility. Existing verifier accepts PEM, raw 32-byte, and base64 raw. To stay interoperable and standard, P15 should emit PEM (SubjectPublicKeyInfo) for public keys and PKCS8 PEM for private keys, since the verifier already handles PEM as a first-class path.
- Risk considered: private key at rest. Default offline keygen should write an unencrypted PEM with explicit docs/release-policy warnings (offline workflow, restricted file permissions, never commit). Optional passphrase encryption is a reasonable follow-up and is explicitly deferred.

## Required Changes
None blocking. The following invariants are mandatory in the implementing PR and are restated so the next round is a rubber-stamp review rather than design:
- New CLI subcommands only: `corpus-keygen`, `corpus-sign`, `corpus-fingerprint`. Do not extend `validate-tasks`/`local-demo` semantics; they already accept signed corpora.
- All three commands depend on `cryptography` and must be importable/invoke-gated by the existing `provenance` extra; importing `self_harness.cli` without the extra must still work, and only the affected subcommand fails closed if the extra is missing.
- `corpus-keygen` writes two files: `<out>` (unencrypted PKCS8 Ed25519 private key PEM) and `<out>.pub` (SubjectPublicKeyInfo PEM). It also prints stable JSON `{private_key, public_key, fingerprint}` with absolute/normalized paths and refuses to overwrite an existing file unless `--force`.
- `corpus-sign` reads an existing unsigned (or already-signed) corpus, recomputes `corpus_checksum(corpus)` and `corpus_integrity_payload(corpus)`, signs the payload bytes with the provided private key, and writes a new corpus JSON containing `corpus_version`, `corpus_id`, `tasks`, `checksum`, and `signature`. Output must be serialized with `stable_json_dumps` (sort_keys, separators consistent with the rest of the codebase) so the file is byte-deterministic. It must not copy any other top-level keys from the input. The command prints stable JSON `{corpus_id, checksum, signature, signed_path}`.
- `corpus-fingerprint` reads a public key (PEM, raw 32 bytes, or base64 raw, mirroring `verify_corpus_signature`) and prints stable JSON `{fingerprint, algorithm:"sha256-spki-der-hex", public_key_path}`. If raw/base64 input is provided, the command must derive the SPKI DER before hashing so PEM and raw forms of the same key produce the same fingerprint.
- No private key bytes may appear in: corpus JSON, stdout JSON, any audit directory, any test fixture beyond a fixture explicitly named `*_private_key*`, or any release artifact. Add an invariant test asserting signed corpus JSON contains exactly the allowed top-level keys.
- Documentation updates are required: README production section (offline signing workflow example), `RELEASE.md` (key generation/rotation policy: keys are operator-held, never in audit artifacts, rotations must be noted in release notes with affected `corpus_id`s), and `docs/architecture/productionization_brief.md` P15 status entry. No `audit_schema_changelog.md` entry is required because audit layout is unchanged.
- Tests must cover: keygen writes both files and the published fingerprint is stable; sign round-trips through existing `load_corpus(..., verify_signature_key=...)`; tampering any signed field under the canonical payload fails with `TaskLoadReason.INVALID_SIGNATURE`; tampering `checksum` alone fails with `CHECKSUM_MISMATCH`; CLI JSON outputs are stable across runs; signed corpus JSON has exactly the allowed top-level keys and no private-key material.
- Stop conditions for P15: green `make check`, `make readiness`, and `make release-smoke`; no canonical audit hash rotation (audit layout unchanged); no schema changelog entry; new CLI commands listed in README; RELEASE.md key policy section present.

## Revised Plan
Execute P15 as the following concrete slice.

1. Schema/format decisions (no audit-schema change):
   - Signed corpus envelope stays as already implemented: top-level `corpus_version`, `corpus_id`, `tasks`, optional `checksum`, optional `signature`. `checksum`/`signature` cover the canonical payload from `corpus_integrity_payload`.
   - Private key format: unencrypted PKCS8 PEM (Ed25519).
   - Public key format: SubjectPublicKeyInfo PEM.
   - Fingerprint: `sha256-spki-der-hex` (lowercase hex of SHA-256 over DER-encoded SubjectPublicKeyInfo), stable across PEM/raw/base64 public key inputs.

2. New module: `src/self_harness/corpus_signing.py` (public, documented in "Stable API" README section after one minor release of soak):
   - `generate_keypair() -> tuple[bytes, bytes]` returning `(private_pem, public_pem)`.
   - `sign_corpus(corpus: TaskCorpus, private_key_pem: bytes) -> str` returning base64 signature over `corpus_integrity_payload(corpus)`.
   - `public_key_fingerprint(public_key: Path | str) -> str` normalizing input through the same logic as `verify_corpus_signature` and hashing SPKI DER.
   - All cryptography imports happen inside these functions so the module imports cleanly without the `provenance` extra.

3. CLI (`src/self_harness/cli.py`):
   - `corpus-keygen --out PATH [--force]`: writes private PEM and `<out>.pub`, prints stable JSON.
   - `corpus-sign --corpus PATH --private-key PATH --out PATH`: loads corpus via `load_corpus(..., verify_signature_key=None)` (so legacy and signed inputs both re-sign deterministically), signs canonical payload, writes stable-JSON corpus with exactly the allowed top-level keys, prints stable JSON.
   - `corpus-fingerprint --public-key PATH`: prints stable JSON.
   - Fail closed with a clear message if `cryptography` is missing.

4. Tests:
   - `tests/test_corpus_signing.py`: unit tests for `generate_keypair`, `sign_corpus`, `public_key_fingerprint` (PEM/raw/base64 equivalence), tamper detection, allowed-keys invariant, no-private-key-bytes invariant.
   - `tests/test_cli.py` additions: CLI round-trip `corpus-keygen` → `corpus-sign` → `validate-tasks --require-corpus-signature`, plus fingerprint stability and `--force` overwrite behavior.

5. Docs/release:
   - README: add "Offline corpus signing" subsection under Production Status with the three-command example.
   - RELEASE.md: add key management policy (operator-held keys, never in audit artifacts, rotation must be noted in release notes with affected `corpus_id`s, no silent rotation).
   - `docs/architecture/productionization_brief.md`: append P15 implemented section.
   - No `docs/architecture/schema_changelog.md` entry; no `canonical_audit_hash.txt` rotation.

6. Stop conditions:
   - `make check`, `make readiness`, `make release-smoke` all green.
   - CI green on Python 3.11/3.12/3.13.
   - No audit schema change, no reproduction claim, no Harbor/Docker dependency.

7. Deferrals (explicit, non-blocking):
   - Passphrase-protected private keys.
   - HSM/KMS-backed signing.
   - Key rotation tooling beyond the release-notes policy.
   - Signing captured Terminal-Bench fixtures (separate provenance path).
   - Promoting `corpus_signing` APIs to the README "Stable API" list before one minor release of soak.

## Remaining Open Questions
None blocking. Two low-priority follow-ups may be revisited later without affecting this slice:
- Whether unencrypted PKCS8 PEM is acceptable long-term or whether passphrase-encrypted private keys should become the default in a future minor release. Current decision: unencrypted default with explicit policy docs; defer.
- Whether `corpus-fingerprint` should also support JOSE/SSH-style base64 fingerprints. Current decision: SHA-256 SPKI DER hex only; defer alternative encodings until a consumer requests one.
