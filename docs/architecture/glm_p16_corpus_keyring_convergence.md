# Round 1

# CONVERGED: YES

## Verdict
The P16 candidate slice is well-bounded, additive, and consistent with the existing P15 corpus signing seam. It does not require an audit schema change, fits naturally behind the existing `provenance` extra, and the alternatives (richer verifier adapters, migration shims) are correctly deferred. The plan below is executable as-is.

## Critique
- **Evidence:** `corpus.py` already exposes `verify_corpus_signature(corpus, public_key)` that accepts a single key path. `corpus_signing.py` exposes `public_key_fingerprint(...)` and `FINGERPRINT_ALGORITHM`. `TaskLoadReason.INVALID_SIGNATURE` already exists. `validate-tasks --require-corpus-signature` is the existing single-key gate. The new keyring slice is a strict superset of this and can reuse these primitives.
- **Inference:** The codebase's stable JSON discipline (`stable_json_dumps`) and lazy cryptography imports are the right pattern for the keyring writer/loader as well.
- **Alternative — richer verifier adapters:** Not relevant. The keyring addresses input-provenance trust, not task verifier extensibility. Different seam; deferring is correct.
- **Alternative — migration shims:** Not needed. The keyring is operator-held material, not an audit artifact. `RELEASE.md` already says key rotations are recorded in release notes; this tooling produces the rotation without touching audit schema. No shim is required.
- **Risk — multiple active keys per corpus:** Acceptable. During a rotation window an operator may have two active keys; `validate-tasks` should accept a signature from any entry whose `corpus_id` matches and `status == "active"`. This is simpler and safer than forcing a single-active invariant.
- **Risk — keyring file integrity (tampering of the keyring itself):** Out of scope. Keyrings are operator-held release material like signing keys; do not introduce a self-signing scheme now. Document as deferred.

## Required Changes
None blocking. The plan below adopts the required constraints (no private keys in keyrings/audits, no audit schema change, cryptography stays in `provenance`, deterministic JSON, no reproduction claim).

## Revised Plan

### Scope
Add a portable corpus keyring/trust manifest plus a `validate-tasks` gate that verifies signed corpora against the active trusted keys for the corpus's `corpus_id`.

### Data model (`keyring_version: "1"`)
File path is operator-chosen (e.g. `keys/corpus.keyring.json`). Deterministic JSON via `stable_json_dumps`. No private key material.

```json
{
  "keyring_version": "1",
  "entries": [
    {
      "corpus_id": "local-smoke",
      "fingerprint": "<64 hex chars>",
      "fingerprint_algorithm": "sha256-spki-der-hex",
      "public_key_pem": "<PEM body>",
      "status": "active",
      "labels": {"environment": "ci"}
    }
  ]
}
```

- Multiple entries per `corpus_id` allowed.
- `status ∈ {"active", "retired", "revoked"}`. Only `active` entries satisfy the gate.
- `labels` optional object; values must be strings; unknown keys allowed.
- `fingerprint` is recomputed and verified on load (rejects mismatched fingerprint/PEM pairs).
- Unknown future fields are ignored on load (forward-compatible), but writer emits exactly the fields above.

### Module
New `src/self_harness/corpus_keyring.py`:
- `KeyringEntry` (frozen dataclass)
- `Keyring` (frozen dataclass with `entries: tuple[KeyringEntry, ...]`)
- `load_keyring(path: Path) -> Keyring` — validates version, recomputes fingerprints, normalizes status enum.
- `save_keyring(keyring: Keyring, path: Path) -> None` — deterministic write.
- `add_entry(...)`, `set_status(...)`, `entries_for(corpus_id, *, status=None)`.
- Lazy cryptography import inside `_public_key_spki_der` reuse path; reuse `corpus_signing.public_key_fingerprint`.
- New `KeyringError` in `self_harness.exceptions`.

### CLI
All under `self-harness`:
- `corpus-keyring init --out PATH` — empty keyring.
- `corpus-keyring add --keyring PATH --corpus-id ID --public-key PATH [--status active|retired|revoked] [--label K=V]...` — appends entry; rejects duplicate `fingerprint` for same `corpus_id`.
- `corpus-keyring set-status --keyring PATH --corpus-id ID --fingerprint FP --status STATUS` — updates an entry.
- `corpus-keyring inspect --keyring PATH [--corpus-id ID] [--json]` — stable JSON listing.
- `validate-tasks PATH --require-corpus-keyring PATH` — new flag, mutually exclusive with `--require-corpus-signature`. Verifies signature against any `active` entry whose `corpus_id` matches the loaded corpus's `corpus_id`. Emits structured failure with `reason="invalid-signature"` if no active match.
- `local-demo --require-corpus-keyring PATH` — same gate (mirror existing `--require-corpus-signature` wiring).

### Audit / schema
No audit schema change. Keyring contents are never embedded into audit artifacts. `manifest.json` may continue to record only that the corpus was signed; it does not record the keyring.

### Tests
- `tests/test_corpus_keyring.py`: init/add/inspect round-trip; fingerprint recomputation rejects tampered PEM; status transitions; duplicate fingerprint rejection; unknown status rejection.
- `tests/test_cli.py` additions:
  - keyring add → validate-tasks with `--require-corpus-keyring` succeeds.
  - signed corpus fails keyring gate when entry is `revoked`.
  - signed corpus fails when `corpus_id` not in keyring.
  - `--require-corpus-keyring` and `--require-corpus-signature` are mutually exclusive (`parser.error`).
  - stable JSON byte-for-byte across save/load.

### Docs
- New `docs/architecture/p16_corpus_keyring_brief.md` mirroring `p15_corpus_signing_brief.md`.
- README additions under the existing corpus signing section: keyring workflow example.
- `RELEASE.md` note: keyring is operator-held material, parallel to signing keys; rotations still recorded in release notes with corpus IDs and new fingerprints.

### Stop conditions
- All tests above pass.
- `make check` and `make readiness` pass (readiness gate unaffected; canonical audit hash unchanged because no audit layout change).
- Manual CLI round-trip documented in README works without `provenance` extra failing on import (core import remains dependency-free; only keyring commands require the extra).

### Out of scope
- Signed/self-certifying keyrings.
- KMS/HSM-backed keys.
- Passphrase-protected signing keys.
- Time-windowed automatic rotation (e.g. `not_after`).
- Audit schema fields recording keyring state.
- Signing Terminal-Bench captured fixtures via the keyring.
- Multi-algorithm keyrings (Ed25519 only).

## Remaining Open Questions
- Should the keyring later embed a small `notes`/`rotation_reason` free-text field? Non-blocking; defer until operators request it.
- Should `validate-tasks` emit a distinct failure reason for "no active key for corpus_id" vs "signature invalid against active keys"? Current plan reuses `invalid-signature` with a descriptive message; can split later without schema churn. Non-blocking.

[usage] {"completion_tokens": 2342, "completion_tokens_details": {"reasoning_tokens": 702}, "prompt_tokens": 20851, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 23193}
