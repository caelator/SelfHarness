# Round 1

CONVERGED: NO

## Verdict

The repo has mature offline scaffolding through P38; the highest-value locally implementable P39 slice is **offline audit integrity verification** — a deterministic, no-network verification pass over a completed audit directory that proves internal consistency (manifest↔lineage↔rounds↔proposals↔evaluations↔harness hashes). This directly strengthens release/operator readiness and reproducibility claims without touching Harbor/Docker/Trivy/PyPI/Sigstore/cloud. The initial plan below is concrete and execution-ready, but one boundary decision needs confirmation (whether verification is read-only over arbitrary audit trees or requires a self-produced manifest sidecar). Marking NO to force that decision before YES.

## Critique

- The repository already produces deterministic, schema-versioned audit artifacts and a canonical readiness hash, but there is no standalone verifier that an operator can run against an arbitrary audit directory to confirm it is internally self-consistent. This is a real gap for release material, archival, and reviewer trust.
- Evidence: `audit.py` exposes `load_audit_run`, `summarize_audit_run`, `audit_trajectory_rows`, and `inspect_harness_run` but no `verify_audit_run`. `RELEASE.md` lists release gates but no audit-integrity gate.
- Risk: without an integrity verifier, a corrupted/partially-rewritten audit tree (e.g., truncated `evaluations.jsonl`, mismatched `harness_after.json` vs `lineage.harness_after_hash`, accepted proposal id missing from lineage) would only be caught by ad-hoc inspection. This weakens the paper-fidelity auditability contract.
- The slice is genuinely high-value and not a toy subset: it improves auditability and reproducibility of the exact protocol the repo already implements, without redefining success.
- Boundary: must remain offline, read-only, deterministic, and must not claim benchmark reproduction. It should fail closed on schema violations and internal inconsistencies.

## Required Changes

1. Confirm scope decision: verifier consumes an existing audit directory only (no new manifest sidecar). If a sidecar is required, that changes schema work.
2. Pin the verification contract to currently-supported schema versions (`1.0`–`1.4`) and reject others with a typed error, mirroring `audit.py`.
3. Add a new invariant test that an audit directory mutated post-hoc (e.g., edited `harness_after.json`) is detected.
4. Wire the verifier into `make readiness` as a new offline gate with a fixture audit directory, without rotating the canonical readiness hash (it is a new gate, not a byte-layout change).
5. Decide whether verification should also validate the `migration_provenance` block added in P38 when present (recommend: yes, but optional/lenient when absent for older trees).

## Revised Plan

**P39 — Offline audit integrity verification**

Files to add:
- `src/self_harness/audit_verify.py`
  - `verify_audit_run(path: Path) -> AuditVerificationReport`
  - Checks: manifest loads and `schema_version` ∈ supported set; lineage is a list of integer-`round` rows covering every `rounds/<n>` directory; each lineage row's `harness_before_hash`/`harness_after_hash` matches recomputed hash of the corresponding `harness_before.json`/`harness_after.json`; accepted/merged proposal ids in `rounds/<n>/proposals.jsonl` are a subset of `accepted_proposal_ids` in the matching lineage row; every proposal row's `schema_version` matches manifest; `__split_total__` rows exist for `__baseline__` and the committed candidate arm; no held-out evidence leakage in any proposal row (no held-out pattern ids, no held-out task ids in `task_id` beyond split totals); if `migration_provenance` is present in manifest, its `source_hash` and target schema are internally consistent.
  - Returns typed report: `AuditVerificationReport(schema_version, checks: list[AuditVerificationCheck], ok: bool, errors: list[str])`.
  - Fails closed (`AuditCorruptError` / new `AuditVerificationError`) only for unrecoverable structural problems; recoverable inconsistencies are captured in the report with `ok=False`.
- `src/self_harness/cli.py`
  - New subcommand `self-harness audit-verify PATH [--json]`.
  - Writes structured report; exit code 0 on `ok=True`, 2 on `ok=False`, 3 on hard structural failure.
- `tests/test_audit_verify.py`
  - Happy path over the canonical demo audit fixture.
  - Mutation cases: tampered `harness_after.json`, missing round dir, mismatched accepted id, held-out pattern leakage injected into a proposal row, truncated `evaluations.jsonl`, schema-version mismatch, unsupported schema.
  - Migration-provenance present/absent cases.
- `tests/invariants/test_audit_integrity_invariant.py`
  - Paper-fidelity invariant: every fixture audit tree under `tests/fixtures` verifies clean.
- `docs/source/audit_integrity.md`
  - Documents the verification contract, supported schema versions, failure modes, and the boundary statement: verification proves internal self-consistency, not benchmark reproduction and not external provenance of model/Harbor/Docker.

Files to modify:
- `Makefile`
  - Add `audit-verify` target running `self-harness audit-verify tests/fixtures/<canonical_audit_dir> --json --out dist/self-harness-audit-verify.json`.
  - Add to `readiness` and `release-candidate-evidence` inputs.
- `scripts/release_candidate_evidence.py`
  - Add optional `--audit-verify-result` input; gate decision goes `blocked` if missing or `ok=False`.
- `RELEASE.md`
  - Document the new audit-verify gate and its boundary.
- `README.md`
  - Add a usage example for `self-harness audit-verify`.
- `src/self_harness/types.py` (or `audit.py` if types live there)
  - Add `AuditVerificationReport`, `AuditVerificationCheck`, and `AuditVerificationError` exception.

Gates:
- `make check`, `make readiness`, `make release-smoke`, `make release-candidate-evidence` all include `audit-verify`.
- Canonical readiness hash is NOT rotated: the verifier is a new gate, not an audit byte-layout change. Confirm this by running readiness before/after the change and asserting the hash file is byte-identical.

Boundary statements:
- No live Harbor, Docker, Trivy, PyPI, Sigstore, registry, OAuth/OIDC, KMS/HSM, or cloud-model contact.
- Verification is read-only and offline; it does not execute tasks, does not invoke models, does not write to the audit tree.
- Verification does not claim benchmark reproduction; it proves internal self-consistency of an audit directory against its declared schema.
- No audit schema, corpus schema, manifest schema, or reproduction-claim semantics change.

Stop conditions:
- All new tests green across Python 3.11/3.12/3.13 in CI.
- `make readiness` passes with the new gate and the canonical readiness hash file is byte-identical to its pre-change value.
- `make release-candidate-evidence` includes the audit-verify result and fails closed on `ok=False`.
- `docs/source/audit_integrity.md` merged with explicit boundary language.

## Remaining Open Questions

1. Should `audit-verify` also accept operator-supplied external trust material (e.g., a corpus public key) to re-verify corpus signatures referenced by manifest provenance? Recommendation: defer to a later slice; P39 stays strictly internal-consistency.
2. Should the verifier emit a stable canonical hash of its report for inclusion in release-candidate-evidence? Recommendation: yes, hash the JSON report deterministically and surface it as `report_hash`, but do not rotate the readiness hash.
3. Should `migration_provenance` validation be strict (reject unknown fields) or lenient (only check declared fields)? Recommendation: strict for declared schema, fail closed on unknown top-level keys with `--strict-migration` flag defaulting to strict in `audit-verify` CLI and lenient in the library API for backward compatibility.

[usage] {"completion_tokens": 2389, "completion_tokens_details": {"reasoning_tokens": 655}, "prompt_tokens": 40217, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 42606}

---

# Round 2

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

[usage] {"completion_tokens": 1643, "completion_tokens_details": {"reasoning_tokens": 40}, "prompt_tokens": 43712, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 45355}
