CONVERGED: YES

## Verdict
The candidate slice is well-scoped and the right next productionization step. It closes the only locally executable gap in the corpus-signing trust chain without dragging in KMS/HSM machinery or audit schema churn. Convergence is reachable now; remaining questions are non-blocking implementation details.

## Critique

**Candidate slice (passphrase-protected PKCS8 PEM) is the correct choice over alternatives:**

- *KMS/HSM adapters:* would require a provider abstraction, credential plumbing, and live integration tests. That belongs in a later P18+ provenance-hardening slice. Adding the seam now without a real backend would be speculative architecture.
- *Richer verifier adapters:* orthogonal to private-key handling; verification already accepts PEM/PEM/raw/base64 public keys and keyring entries. No gap there.
- *Migration shims:* not needed. Preserving unencrypted PEM behavior as the default keeps P15/P16 artifacts and tests byte-compatible; encrypted keys become opt-in via explicit flags/env.

**Risks in the candidate, all addressable in-plan:**

1. *Secret leakage in error/log surfaces.* Passphrases must never appear in `CorpusSigningError` messages, audit JSON, keyring JSON, signed corpus JSON, or stdout JSON. Plan must mandate a redaction boundary.
2. *Interactive prompts in tests/CI.* Constraint already says no prompts. Plan must forbid `input()`/`getpass()` paths and require explicit noninteractive sources only, with a hard failure when a private key is encrypted and no passphrase source is supplied.
3. *Compatibility regression.* Existing `corpus-keygen` / `corpus-sign` invocations and the canonical audit hash must be unchanged when encryption is not requested. Encryption must be opt-in.
4. *Cipher/KDF choice drift.* Pin a named encryption profile (PBES2 + scrypt or PBKDF2) so future cryptography versions don't silently change the PEM envelope semantics. Document the KDF parameters.
5. *Exit-code/JSON stability.* The current CLI JSON contract (`ok`, `reason`, `message`, paths) must be preserved. New fields must be additive.

**Evidence vs inference:**
- Evidence (from repo): `generate_keypair()` uses PKCS8 + `NoEncryption()`; `sign_corpus()` calls `load_pem_private_key(..., password=None)`; CLI JSON shape is stable; no audit/keyring schema change is needed; README/RELEASE forbid private-key material in corpus/audit/keyring.
- Inference: cryptography's `BestAvailableEncryption(passphrase)` produces PBES2 PKCS8; explicit `serialization.BestAvailableEncryption` is preferred over relying on default KDFs. Will validate during implementation.

## Required Changes

The plan must include, explicitly:

1. **Noninteractive passphrase sources, in strict precedence order**, documented and tested: explicit `--passphrase` flag → `--passphrase-file` path → `--passphrase-env` var name. No fallback to prompt. No reading the env var directly by name in tests.
2. **Encryption is opt-in only.** Default `corpus-keygen` continues to emit unencrypted PEM. Encryption is requested via `--passphrase*` (or a dedicated `--encrypted` flag carrying the passphrase source).
3. **Redaction contract.** Errors raised when passphrase is wrong must be a fixed string like `"private key passphrase is required or incorrect"`; passphrases must never be interpolated into exceptions, JSON, or log output. Add a regression test that greps outputs for the test passphrase.
4. **KDF profile pinning.** Name the encryption profile in docs and tests; assert the generated PEM header is `-----BEGIN ENCRYPTED PRIVATE KEY-----`.
5. **Backward compatibility gates.** Keep all existing P15/P16 tests green without modification; add new tests alongside, not as replacements. Confirm canonical audit hash unchanged (it only covers the demo path, which is unaffected, but assert it explicitly).
6. **No new audit fields, no new public keyring fields.** Passphrase support touches only `corpus_signing.py`, CLI flag plumbing, README/RELEASE docs, and tests.
7. **Stop conditions:** plan is done when (a) encrypted keygen→sign→validate/keyring round-trip works, (b) unencrypted path is byte-identical to current behavior, (c) wrong/missing passphrase fails with a redacted error, (d) no passphrase/private-key bytes appear in any produced JSON or written artifact, (e) `make check && make readiness` pass on 3.11/3.12/3.13.

## Revised Plan

### Scope (P17)
Add passphrase-protected Ed25519 PKCS8 PEM support to offline corpus key generation and signing, using explicit noninteractive passphrase sources. Preserve unencrypted default for compatibility.

### CLI shape
- `corpus-keygen --out PATH [--force] [--passphrase TEXT | --passphrase-file PATH | --passphrase-env NAME]`
  - If any passphrase option is given → emit encrypted PKCS8 PEM (`BEGIN ENCRYPTED PRIVATE KEY`).
  - Without passphrase options → current unencrypted behavior, byte-compatible.
  - Public key file unchanged.
- `corpus-sign --corpus PATH --private-key PATH --out PATH [--passphrase TEXT | --passphrase-file PATH | --passphrase-env NAME]`
  - Passphrase used only when loading the private key; `cryptography`'s loader auto-detects encrypted PEM.
  - If the PEM is encrypted and no passphrase resolves → fail with redacted `CorpusSigningError`, exit 2, JSON `reason="corpus-signing-error"`.
  - If the PEM is unencrypted and a passphrase is supplied → ignore silently (or fail closed — see open question, non-blocking; recommend ignore to ease CI env reuse).

### Precedence for passphrase resolution (single helper, reused by both commands)
1. `--passphrase` literal (highest)
2. `--passphrase-file` contents (stripped trailing newline)
3. `--passphrase-env` variable's value
4. None

### API changes
- `corpus_signing.generate_keypair(passphrase: str | None = None) -> tuple[bytes, bytes]`
  - `passphrase is None` → current `NoEncryption()` path.
  - Else → `serialization.BestAvailableEncryption(passphrase.encode("utf-8"))`.
- `corpus_signing.sign_corpus(corpus, private_key_pem, *, passphrase: str | None = None) -> str`
  - Pass `password=passphrase.encode("utf-8") if passphrase else None` into `load_pem_private_key`.
  - On `TypeError`/`ValueError` from cryptography → raise `CorpusSigningError("private key passphrase is required or incorrect")`. Never include the passphrase or PEM bytes in the message.
- No change to `verify_corpus_signature`, `public_key_fingerprint`, `corpus_keyring.*`, or audit code.

### Docs
- README "Generate offline signing keypair" section: add encrypted variant examples with `--passphrase-env CORPUS_KEY_PASSPHRASE` and warn against `--passphrase` literal in CI.
- RELEASE.md: note P17 adds optional encrypted keygen/signing; no audit schema change; no keyring schema change; rotation guidance unchanged.
- New `docs/architecture/p17_encrypted_signing_brief.md` mirroring P15/P16 briefs.

### Tests (additive)
- `test_corpus_signing.py`:
  - encrypted keygen produces `ENCRYPTED PRIVATE KEY` header; sign+verify round-trip succeeds with passphrase.
  - wrong passphrase → `CorpusSigningError` with fixed redacted message; assert passphrase string not in `str(exc)`.
  - unencrypted path unchanged: existing tests untouched; add explicit byte-equality assertion vs current PKCS8 output where feasible.
- `test_cli.py`:
  - `corpus-keygen --passphrase-env ...` + `corpus-sign --passphrase-env ...` + `validate-tasks --require-corpus-signature pub` + keyring round-trip end-to-end.
  - `corpus-sign` against encrypted key with missing env → exit 2, JSON `reason="corpus-signing-error"`, passphrase absent from stdout.
  - `corpus-sign` against encrypted key with `--passphrase-file`.
  - Assert public-key fingerprint identical whether key was generated encrypted or unencrypted from same underlying key (use deterministic test vector: generate once, fingerprint twice by reloading).
  - Redaction sweep: run encrypted sign through CLI and grep stdout/stderr + signed corpus + keyring (if built) for the test passphrase; must be absent.
- No changes to readiness fixtures; confirm `make readiness` hash unchanged.

### Out of scope
- KMS/HSM/GPG/YubiKey adapters.
- Signed/self-certifying keyrings.
- Automated key rotation tooling or time-window metadata.
- Signing Terminal-Bench captured fixtures.
- Any audit schema bump or keyring schema bump.
- Passphrase complexity policy or key-stretching customization beyond cryptography's `BestAvailableEncryption`.

### Stop conditions
1. Encrypted round-trip works through keygen → sign → validate → keyring.
2. Unencrypted path byte-compatible with P15.
3. Wrong/missing passphrase fails closed with redacted message.
4. No passphrase or private-key bytes in any produced JSON or file.
5. `make check && make readiness` green on 3.11/3.12/3.13.
6. README/RELEASE/brief updated; no claims of reproduction.

## Remaining Open Questions
- Should `corpus-sign` fail closed when a passphrase is supplied but the PEM is unencrypted? Recommendation: ignore (CI env reuse friendliness); non-blocking, reversible later.
- Should `--passphrase` literal be hidden from `--help` to discourage CI use, or documented with a warning? Recommendation: document with warning; non-blocking.
- Pin KDF parameters explicitly (e.g., force scrypt) vs accept cryptography's `BestAvailableEncryption` defaults across versions? Recommendation: accept defaults but document the envelope type; revisit only if reproducibility across cryptography versions becomes an issue. Non-blocking for this slice.

CONVERGED: YES
