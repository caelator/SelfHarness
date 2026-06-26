CONVERGED: YES

## Verdict

The P39 offline audit integrity verification slice is implementation-ready. It advances the real objective (production-ready, paper-faithful, non-falsifying) by adding a deterministic, offline, read-only verifier that proves internal self-consistency of an audit directory. The scope boundary decision flagged in round 1 has been resolved (consume an existing audit directory; no new manifest sidecar, no schema bump). Remaining open questions are non-blocking recommendations, not gating decisions.

## Critique

- Evidence (from repo context): `src/self_harness/audit.py` exposes `load_audit_run`, `summarize_audit_run`, `audit_trajectory_rows`, `inspect_harness_run`, and `diff_audit_runs`, plus `SUPPORTED_SCHEMA_VERSIONS = {"1.0","1.1","1.2","1.3","1.4"}` and `AuditCorruptError`. There is no `verify_audit_run`. The engine (`engine.py`) writes `manifest.json`, `lineage.json`, per-round `harness_before.json`/`harness_after.json`/`proposals.jsonl`/`evaluations.jsonl`, including `__split_total__` rows and `schema_version` on rows. `RELEASE.md` and `Makefile` enumerate release gates; none include an audit-integrity verifier.
- Inference: A standalone verifier is a genuine gap for release material and reviewer trust; it does not require Harbor/Docker/Trivy/PyPI/Sigstore/cloud and does not rotate the readiness hash if implemented as a new read-only gate over a fixture audit tree.
- Risk addressed: post-hoc mutation of `harness_after.json`, lineage hash drift, accepted-proposal id mismatch, held-out evidence leakage, truncated `evaluations.jsonl`, and unsupported schema versions are all detectable by deterministic re-derivation. The plan correctly fails closed on structural problems and reports recoverable inconsistencies.
- Boundary discipline is correct: verification is not benchmark reproduction and not external provenance attestation; it proves internal self-consistency against the declared schema.

## Required Changes

1. Implement `verify_audit_run(path: Path) -> AuditVerificationReport` exactly as specified: supported schema version gate (`SUPPORTED_SCHEMA_VERSIONS` reuse), manifest↔lineage↔rounds coverage, harness hash re-derivation matching `harness_hash` semantics in `harness.py`, accepted/merged proposal id subset of lineage `accepted_proposal_ids`, proposal row `schema_version` matching manifest, `__split_total__` presence for `__baseline__` and committed candidate arm, held-out leakage detection (no held-out pattern ids; no held-out `task_id` rows beyond split totals), and optional `migration_provenance` internal-consistency check when present.
2. Add `AuditVerificationReport`, `AuditVerificationCheck`, and `AuditVerificationError` types; reuse `AuditCorruptError` for unrecoverable structural failures where appropriate to avoid duplicate error semantics.
3. Add CLI subcommand `self-harness audit-verify PATH [--json] [--out PATH]` with exit codes 0 (ok), 2 (ok=False), 3 (structural failure).
4. Add deterministic `report_hash` over the canonical JSON of the report (sorted keys, UTF-8, SHA-256) for inclusion in release-candidate evidence; do not rotate the canonical readiness hash.
5. Wire into `Makefile` as `audit-verify` target over a canonical fixture audit directory; add to `readiness` and `release-candidate-evidence` inputs. Assert byte-identical `tests/fixtures/canonical_audit_hash.txt` before/after.
6. Extend `scripts/release_candidate_evidence.py` with optional `--audit-verify-result` input; decision goes `blocked` if missing or `ok=False`.
7. Add `tests/test_audit_verify.py` with happy path over the canonical demo audit fixture and mutation cases: tampered `harness_after.json`, missing round dir, mismatched accepted id, injected held-out pattern leakage, truncated `evaluations.jsonl`, schema-version mismatch, unsupported schema, and migration-provenance present/absent.
8. Add `tests/invariants/test_audit_integrity_invariant.py` asserting every fixture audit tree under `tests/fixtures` verifies clean.
9. Add `docs/source/audit_integrity.md` with the verification contract, supported schema versions, failure modes, and explicit boundary statement (internal self-consistency only; not benchmark reproduction; not external model/Harbor/Docker provenance).
10. Update `RELEASE.md`, `README.md`, and the Stable API section of `README.md` to document `verify_audit_run`, `AuditVerificationReport`, `AuditVerificationCheck`, and `AuditVerificationError`.

## Revised Plan

P39 — Offline audit integrity verification.

Files to add:
- `src/self_harness/audit_verify.py` — `verify_audit_run`, report/check types, deterministic `report_hash`.
- `tests/test_audit_verify.py` — happy path plus all mutation cases enumerated above.
- `tests/invariants/test_audit_integrity_invariant.py` — all fixture audit trees verify clean.
- `docs/source/audit_integrity.md` — contract, schema versions, failure modes, boundary statement.

Files to modify:
- `src/self_harness/types.py` (or `audit.py`) — add `AuditVerificationReport`, `AuditVerificationCheck`, `AuditVerificationError`.
- `src/self_harness/cli.py` — add `audit-verify` subcommand.
- `Makefile` — add `audit-verify` target; add to `readiness` and `release-candidate-evidence` prerequisites.
- `scripts/release_candidate_evidence.py` — add `--audit-verify-result`; block on missing or `ok=False`.
- `RELEASE.md` — document the new audit-verify gate and boundary.
- `README.md` — usage example and Stable API entry.

Gates:
- `make check`, `make readiness`, `make release-smoke`, `make release-candidate-evidence` all include `audit-verify`.
- Canonical readiness hash file is byte-identical pre/post change (verified by running readiness before and after).

Boundary statements:
- No live Harbor, Docker, Trivy, PyPI, Sigstore, registry, OAuth/OIDC, KMS/HSM, or cloud-model contact.
- Verification is read-only and offline; it does not execute tasks, invoke models, or write to the audit tree.
- Verification does not claim benchmark reproduction; it proves internal self-consistency of an audit directory against its declared schema.
- No audit schema, corpus schema, manifest schema, or reproduction-claim semantics change.

Stop conditions:
- All new tests green across Python 3.11/3.12/3.13 in CI.
- `make readiness` passes with the new gate and canonical readiness hash is byte-identical to its pre-change value.
- `make release-candidate-evidence` includes the audit-verify result and fails closed on `ok=False`.
- `docs/source/audit_integrity.md` merged with explicit boundary language.

## Remaining Open Questions

1. External trust material (e.g., corpus public key) for re-verifying corpus signatures referenced by manifest provenance: defer; P39 stays strictly internal-consistency. Non-blocking.
2. `migration_provenance` strict vs lenient: implement strict-by-default in the library and expose `--strict-migration` on the CLI defaulting to strict; lenient mode tolerated for older trees without the block. Non-blocking.
3. Canonical report hash inclusion in release-candidate-evidence: yes, include as `report_hash`; do not rotate readiness hash. Non-blocking.
