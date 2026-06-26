CONVERGED: YES

## Verdict
The P21 external signer boundary is sufficiently specified to execute. The round-1 open questions are all answerable at the architecture level with defensible defaults: payload-bytes-in (eliminates canonicalization ambiguity), a concrete versioned failure JSON shape, concrete timeout/output bounds (15 s / 16 KiB), platform-keychain explicitly deferred to a follow-on, and the fixture signer command as the only test seam. Remaining items are execution details that do not change the architecture. No additional repository evidence is required; the design relies only on the verified facts in the task statement (existing Ed25519 verification contract, stable `corpus-sign` envelope, `cryptography`-free core import requirement, no-secret-bytes invariant, structured fail-closed CLI behavior).

## Critique
- Evidence (from task statement): verification remains standard Ed25519 over `corpus_integrity_payload(corpus)`; `corpus-sign` envelope is `{corpus_version, corpus_id, tasks, checksum, signature}`; existing P15–P17 provide corpus signatures, encrypted local material, fingerprints, keyrings; documented production gap is KMS/HSM-backed key management; hard constraints enumerated.
- Inference: the only architecture change is the *source* of the signature field. Envelope assembly, checksum, and verification are untouched, which preserves the verification contract and audit-schema-free property by construction.
- Decision: external signer receives the *exact* `corpus_integrity_payload(corpus)` bytes (base64 over stdin), not a digest. This removes any cross-implementation canonicalization risk and keeps the signer protocol independent of the corpus serializer.
- Decision: structured failure envelope is versioned (`schema_version: 1`) and machine-readable, with stable required fields plus optional context. This satisfies the fail-closed/structured-JSON requirement and gives operators actionable diagnostics.
- Decision: defaults `--signer-timeout 15` (seconds) and `--signer-max-output 16384` (bytes stdout). 15 s covers cold KMS/HSM latency with comfortable headroom; 16 KiB is ample for the defined response schema while bounding malformed/abusive output.
- Decision: platform-keychain (macOS Keychain, Windows DPAPI, Linux Secret Service) is out of scope for P21 and deferred to P22+. The P21 boundary is provider-agnostic by design, so a keychain wrapper is a later plugin, not an architecture change.
- Decision: the only test seam for the signer transport is a fixture executable invoked via the production `subprocess.run` path. No monkeypatching of transport internals; this preserves end-to-end realism and deterministic reproducibility.
- Risk (accepted, non-blocking): fingerprint trust establishment remains an operator out-of-band responsibility; CLI only *displays* provenance, never attests it. Documented as a non-goal.
- Risk (accepted, non-blocking): very large corpora are not streamed to the signer in P21. The existing path already materializes `corpus_integrity_payload` in memory, so this adds no new limit. Streaming is a follow-on concern.
- Risk (mitigated): `cryptography` must remain lazy-imported inside the local signer adapter and verifier entrypoint so `import <core>` works without it. Acceptance gate #2 enforces this.

## Required Changes
No further owner decisions are required to execute. The following execution-time constraints are locked in:
1. Signer I/O contract is payload-bytes-in (base64), signature-bytes-out (base64). No digest input path.
2. Failure JSON schema is fixed at `schema_version: 1` with shape below.
3. `key_id` is required in the signer response (aids rotation; empty string permitted only for the fixture/test signer).
4. Default timeout 15 s, default max stdout 16384 bytes; both operator-overridable, clamped to a hard floor of 1 s and 256 bytes and a hard ceiling of 60 s / 1 MiB to prevent accidental DoS of the fail-closed posture.
5. Platform-keychain is explicitly non-goal for P21.
6. Fixture signer command is the sole transport test seam; transport-layer monkeypatching is prohibited.

## Revised Plan

### Boundary
`ExternalSigner` protocol:
- Input (stdin, JSON, length-bounded): `{schema_version: 1, request_id, deadline_ms, payload_b64}` where `payload_b64` is exactly `base64(corpus_integrity_payload(corpus))`.
- Output (stdout, JSON, ≤ `--signer-max-output` bytes): `{schema_version: 1, signature_b64, public_key_b64, fingerprint, key_id, provider}`. No secret bytes are returned.
- Errors: non-zero exit + versioned JSON on stderr; CLI emits the same structured envelope to stderr and exits non-zero.

### Locked failure JSON
```
{
  "schema_version": 1,
  "type": "external_signer_error",
  "code": "<stable short token, e.g. signer_timeout|signer_oversize|signer_malformed_json|signer_nonzero_exit|signer_missing_field|signer_payload_mismatch>",
  "message": "<human string>",
  "provider": "<from CLI flag or signer response if available>",
  "key_id": "<from CLI flag or signer response if available>",
  "request_id": "<echoed>",
  "timeout_ms": <int, only when timeout-related>,
  "exit_status": <int, only when nonzero-exit-related>,
  "cause": "<short provider/transport hint, no secret bytes>"
}
```

### Files (planned)
- `src/.../signing/external_signer.py` — protocol dataclass, `subprocess.run` runner (no `shell=True`, explicit argv list), timeout enforcement, stdout byte cap, base64 + JSON validation, field-presence checks, structured error emission.
- `src/.../signing/external_signer_errors.py` — error types, versioned JSON serializer, stable `code` taxonomy.
- `src/.../signing/signer_factory.py` — selects local-vs-external; lazily imports `cryptography` *only* inside the local adapter module; rejects `--private-key` when `--external-signer` is set.
- `src/.../cli/corpus_sign.py` (modify) — new flags `--external-signer`, `--signer-timeout`, `--signer-max-output`, `--public-key`, `--fingerprint`, `--key-id`, `--signer-provider`; fail-closed structured JSON to stderr on any signer error; non-zero exit.
- `tests/.../signing/test_external_signer.py` — fixture executable (small Python script in `tests/fixtures/signers/`) invoked via the real subprocess path; covers deterministic signature, verifier compatibility, timeout, oversize stdout, malformed JSON, nonzero exit, missing-field, payload-mismatch.
- `tests/.../cli/test_corpus_sign_external.py` — end-to-end CLI through the fixture signer; asserts envelope shape unchanged, verifier still passes, no secret bytes anywhere.
- `tests/.../signing/test_core_import_no_cryptography.py` — imports core module in a subprocess with `cryptography` uninstalled (or mocked-unavailable) and asserts success.
- `tests/gateways/test_no_shell_true.py` — grep gate for `shell=True` under `src/`.
- `tests/gateways/test_no_secret_bytes.py` — grep gate across corpus/keyring/audit/release artifacts produced in tests.
- `docs/architecture/productionization_brief.md` (update) — record P21 external signer boundary, protocol schema, non-goals, rotation/`key_id` notes, KMS/HSM/YubiKey integration as documentation-only.
- `docs/operations/external_signer.md` (new) — operator runbook: wrapping KMS/HSM/YubiKey, publishing public key + fingerprint out-of-band, timeout/output tuning, fail-closed behavior, troubleshooting by `code`.

### Acceptance gates
1. Existing corpus verification path unchanged; externally-signed envelopes verify against the existing verifier.
2. `python -c "import <core module>"` succeeds with `cryptography` unavailable.
3. No secret bytes in corpus JSON, keyring, audit records, release notes, or GLM artifacts (CI grep).
4. Fixture signer yields byte-identical signatures for identical payloads across runs.
5. Timeout, oversize stdout, nonzero exit, malformed JSON, missing field, and payload mismatch each produce structured JSON (`schema_version: 1`, stable `code`) and non-zero CLI exit.
6. `public_key`, `fingerprint`, `key_id`, and `provider` are operator-visible in CLI output and keyring metadata only.
7. No `shell=True` in `src/` (CI grep).
8. No audit schema change (schema diff gate).
9. `--private-key` rejected when `--external-signer` is set.
10. Timeout/max-output clamped to documented floors/ceilings.

### Non-goals
- Real cloud KMS credentials or live HSM/YubiKey hardware in CI.
- Platform-keychain wrappers (macOS Keychain / Windows DPAPI / Linux Secret Service) — deferred to P22+.
- Establishing trust in the public key/fingerprint (operator out-of-band responsibility).
- Changing corpus envelope, checksum, signature algorithm, or audit schema.
- Streaming very large corpora to the signer.
- Storing any secret bytes anywhere.

## Remaining Open Questions
None blocking. The following are execution-time refinements only:
- Final shortlist of `code` tokens can be expanded during implementation as long as existing tokens remain stable.
- Whether to add a `--signer-request-id-source` flag for distributed tracing is an observability enhancement, not a contract change.
- Exact `provider` vocabulary (e.g., `aws-kms`, `yubikey`, `fixture`) is operator-supplied free text in P21; a controlled vocabulary can be added later without breaking the protocol.
