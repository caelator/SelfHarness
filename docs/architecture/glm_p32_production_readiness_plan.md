CONVERGED: YES

## Verdict
The highest-value locally-implementable slice is **P32: Scanner database freshness validation**. It directly completes the P31 scanner execution orchestrator (which currently only checks that a Trivy DB metadata file *exists* during preflight) and reuses the existing offline-testable fixtures and freshness-policy machinery. It requires no live Trivy, Docker, registry, cloud credentials, or network, and does not touch audit/corpus/benchmark reproduction semantics.

## Critique
Why this slice over the alternatives in the remaining-work list:

- **Provider KMS/HSM wrappers, registry/OAuth/secret-manager helpers**: require cloud credentials and live external services to validate meaningfully. The external-signer protocol (P21) already defines the boundary; provider adapters are additive ops material, not architecture work, and cannot be fully tested offline.
- **Pre-run Harbor image discovery, real Harbor/Docker benchmark execution**: explicitly require a provisioned Harbor host — out of scope per the task constraints.
- **Sigstore/PyPI attestations**: valuable, but P26 already established the detached-signature boundary for release provenance, and Sigstore adds a network/sigstore playground dependency for any real verification test. Lower local value than closing the scanner-DB preflight gap.
- **Migration shims**: premature; no breaking schema change is planned.
- **Scanner DB freshness**: the preflight introduced in P31 hard-fails only on *missing* metadata. It does not detect a stale DB (e.g., `NextUpdate` in the past), which is the exact failure mode operators hit in air-gapped/offline scanner workflows. This is a real production gap with a purely offline fix path.

Evidence for feasibility: `src/self_harness/scanner_execution.py::_db_metadata_check` only checks file existence; `tests/fixtures/vuln/trivy_db_metadata.json` already contains `Version`, `NextUpdate`, `UpdatedAt`; `src/self_harness/freshness_policy.py` already parses ISO-8601 timestamps and computes age/future decisions.

## Required Changes
None blocking. The slice is fully specified by the revised plan below and is executable with current repository context.

## Revised Plan

**P32: Scanner database freshness validation**

Goal: extend the Trivy DB preflight to parse `metadata.json` and reject stale DBs (`NextUpdate` in the past, or `UpdatedAt` older than an operator-owned policy), without changing audit/corpus/manifest/readiness/reproduction semantics.

Files:
1. `src/self_harness/scanner_db_freshness.py` (new)
   - `TrivyDbMetadata` frozen dataclass: `version: int`, `next_update: date | None`, `updated_at: date | None`, `source_path: str`.
   - `parse_trivy_db_metadata(path: Path) -> TrivyDbMetadata` — strict JSON parse, fail closed on missing/malformed/unparseable fields. Tolerate either `metadata.json` or `db/metadata.json` layouts (already handled in preflight discovery).
   - `ScannerDbFreshnessPolicy` frozen dataclass: `policy_version="1"`, optional `max_age_days`, optional `require_next_update: bool = True`.
   - `ScannerDbFreshnessDecision` with `allowed`, `code` (e.g., `missing-metadata`, `malformed-metadata`, `missing-next-update`, `stale-next-update`, `stale-updated-at`, `allowed`), `message`, parsed dates, source path.
   - `evaluate_scanner_db_freshness(metadata, policy, *, evaluated_at=None) -> ScannerDbFreshnessDecision`.
   - `load_scanner_db_freshness_policy(path) -> ScannerDbFreshnessPolicy` and `*_to_jsonable` helpers.

2. `src/self_harness/scanner_execution.py` (edit)
   - Extend `_db_metadata_check` to return the resolved metadata path (already does via `detail`).
   - Add optional `db_freshness_policy: ScannerDbFreshnessPolicy | None = None` to `ScannerCommand` (frozen dataclass, default `None` preserves existing behavior and the canonical readiness hash).
   - When a policy is supplied and the metadata file is found, parse and evaluate; map `allowed=False` to preflight `fail` with the decision code in `detail`. Missing metadata continues to fail closed when a policy requires it.
   - `scanner_run_result_to_jsonable` already serializes preflight checks; extend the check dict with an optional `freshness` block when evaluated.

3. `scripts/scanner_run.py` (edit)
   - Add `--db-freshness-policy PATH` CLI flag. Mutually compatible with `--db-dir`; incompatible with `--dry-run` only in the sense that dry-run still skips evaluation (documented).
   - Include the freshness decision in the combined JSON result under `scanner.preflight.checks[*].freshness`.

4. `docs/operations/scanner_execution.md` and `docs/operations/vulnerability_policy.md` (edit)
   - Document scanner DB freshness policy schema, fail-closed behavior, and that it is release/operator material like the report freshness policy.

5. `tests/test_scanner_execution.py` and new `tests/test_scanner_db_freshness.py`
   - Unit tests: parse valid/missing/malformed metadata; `NextUpdate` past/today/future; `UpdatedAt` older than `max_age_days`; `require_next_update` rejection when field absent.
   - Integration via `scripts/scanner_run.py --replay` path: stale DB metadata under `--db-dir` fails preflight with exit code 2 and does not execute Trivy; fresh metadata passes.
   - Determinism: use `--today` injection already present in the freshness module.

6. `Makefile` (edit)
   - Optionally extend `scanner-check` to demonstrate a DB freshness replay (non-blocking; keeps CI offline).

Schema/preservation boundaries:
- No change to audit schema, corpus schema, manifest schema, trajectory schema, readiness hash, or `reproduction_claimed` semantics.
- New `ScannerDbFreshnessPolicy` schema version `1` is operator-owned release material, parallel to the existing freshness policy.
- Default behavior (`db_freshness_policy=None`) is byte-identical to current preflight — verified by the existing dry-run test and canonical hash coverage.

Acceptance tests:
- Stale `NextUpdate` → preflight `fail`, scanner not invoked, exit 2, combined JSON `scanner.preflight.passed == false`.
- Fresh metadata → preflight `pass`, replay continues, vulnerability/image/freshness report gates run normally.
- Malformed metadata JSON → preflight `fail` with `malformed-metadata`, exit 2.
- `--db-freshness-policy` omitted → behavior unchanged from P31 (regression guard via existing tests).

Explicit deferrals:
- Live Trivy DB download/update orchestration (still operator-owned).
- Provider KMS/HSM signer wrappers, registry/OAuth/secret-manager helpers (require credentials; boundary already in P21).
- Pre-run Harbor image discovery and real Harbor benchmark execution (requires Harbor host).
- Sigstore/PyPI attestation signing (separate trust boundary; P26 detached signature remains the current release path).
- CI invocation of real Trivy against real images (requires scanner DB network access).

## Remaining Open Questions
None blocking. Two operator-side decisions worth recording but not required for execution:
1. Should `ScannerDbFreshnessPolicy` eventually merge with the existing report `FreshnessPolicy`, or stay separate to keep DB-vs-report semantics distinct? Recommendation: keep separate for now; revisit at the next major review.
2. Should the Makefile `scanner-check` target include a DB freshness replay by default? Recommendation: yes, as a non-blocking follow-up after P32 lands, to keep the initial slice focused.
