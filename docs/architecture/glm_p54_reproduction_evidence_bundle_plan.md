CONVERGED: YES

## Verdict

P54 is the correct next production-readiness slice. Evidence (current `reproduction_readiness_report.py`, `_artifact_shapes.py`, `reproduction_readiness_artifact_shape_lint.py`, and the release-gate list in `RELEASE.md`) confirms that the hard reproduction-readiness gate currently ingests an unordered artifact set via `--artifact-dir` (indexed by stem) and repeated `--artifact CLASS=PATH` flags. The class-specific JSON shape validators are sound, but nothing binds the *set itself* to a digested, optionally signed manifest, and nothing rejects duplicate or conflicting class entries across the two input modes. This is a real tamper-evidence and provenance gap on the boundary that the paper-fidelity reproduction claim depends on, so it is in-scope for "production-ready without prematurely claiming reproduction."

## Critique

Inference (no repository fact contradicts this):

1. **Right scope, low blast radius.** The slice touches only the reproduction-readiness input boundary and the hard `release-candidate-evidence-reproduction` gate. Default release path, audit schema, corpus schema, manifest schema, and canonical readiness hash should be untouched, mirroring the discipline of P45–P53.
2. **The two existing input modes are the core weakness.** Stem-based `--artifact-dir` indexing plus repeated `--artifact` flags lets the same artifact class be supplied twice (e.g., once via dir, once via flag) with different content; the current evaluator would just take the union and validate each independently, never comparing them. A bundle manifest with content addressing closes this.
3. **Signing pattern already exists.** P26 (release provenance sidecars), P40 (operator promotion), P15–P17 (corpus signing) establish the Ed25519 sidecar convention. P54 should reuse the same detached-sidecar verification seam rather than introducing new crypto.
4. **Conflict semantics must be sharp and fail-closed.** The natural rule is "exactly one entry per required artifact class within a single bundle." Allowing duplicates invites operator error; requiring uniqueness is auditable.
5. **Backward compatibility is achievable without weakening the gate.** Keep `--artifact-dir`/`--artifact` for the *advisory* path (default reproduction-readiness-check) and require a verified bundle only on the *hard* path (`release-candidate-evidence-reproduction`).
6. **Fixture hash rotation is contained.** The committed `reproduction_readiness_result.json` fixture used by `test_reproduction_readiness_report_hash_matches_committed_fixture` exercises the advisory path; it should not rotate unless we change the default path's required input set. The hard-gate fixture (if any) rotates.
7. **Source metadata must be constrained, not free-form.** Follow the operator-bundle discipline in P36/P40: schema version, provider/url/captured_at/operator_label, with unknown-field rejection.

## Required Changes

The plan as proposed must be tightened to:

- Require *exactly one* bundle entry per artifact class declared in `benchmark_reproduction_requirements.json`; reject duplicates and reject bundles that declare classes not present in the requirements catalog.
- Verify each entry's `sha256` and `byte_size` against the referenced file on disk *before* invoking the existing class-specific shape validators; mismatch is fail-closed.
- Reuse the existing Ed25519 sidecar verification seam (P26/P40 style), not a new crypto path.
- Preserve `reproduction_claimed=false` semantics at both the bundle manifest level and every referenced artifact (already enforced by class validators).
- Keep `make release-candidate-evidence` (default, non-reproduction) unaffected; only `make release-candidate-evidence-reproduction` adds the bundle requirement.
- Add `make reproduction-readiness-bundle-verify` as a standalone offline gate and include its report hash in the reproduction-readiness report metadata.
- Rotate only the hard-gate fixture hash if a committed fixture exists for it; do not rotate `tests/fixtures/release_candidate/reproduction_readiness_result.json` unless the advisory path's input contract changes.

## Revised Plan

**P54: Operator-supplied benchmark reproduction evidence bundle manifest**

1. **Schema.** New operator-owned bundle schema `1.0`:
   - `schema_version: "1.0"`
   - `bundle_id: str` (operator-chosen stable id)
   - `created_at: str` (ISO 8601)
   - `operator_label: str`
   - `entries: [ { required_artifact_class, path, sha256, byte_size, source: { provider?, url?, captured_at?, operator_label? }, notes? } ]`
   - `reproduction_claimed: false`
   - Strict field set; unknown fields fail closed (consistent with P36/P40 discipline).

2. **Core module.** `src/self_harness/reproduction_bundle.py`:
   - `ReproductionBundle`, `ReproductionBundleEntry`, `ReproductionBundleError`.
   - `load_reproduction_bundle(path)`: schema validation, unknown-field rejection, `reproduction_claimed=false` enforcement.
   - `verify_reproduction_bundle(bundle, requirements, *, signature_path=None, public_key=None)`: returns a deterministic `ReproductionBundleReport` with `report_hash`, `reproduction_claimed=false`, and an explicit non-reproduction boundary string.
   - Per-class uniqueness: any class appearing more than once in entries, or appearing with differing `(path, sha256, byte_size)`, is a hard failure.
   - Class coverage: every `required_artifact_class` in the loaded requirements catalog must have exactly one entry; extra entries for unknown classes fail closed.
   - File integrity: each referenced file must exist, be non-empty, have matching `byte_size`, and matching `sha256` (computed over exact file bytes).
   - After integrity passes, delegate to the existing `artifact_shape_error` validators for class-specific shape checks.
   - Optional detached Ed25519 signature sidecar over exact bundle file bytes, verified through the same helper used by release provenance (P26) and operator promotion (P40); absence is permitted on the advisory path, required on the hard path.

3. **CLI surfaces.**
   - `scripts/reproduction_bundle_verify.py` with `--bundle`, optional `--signature`, `--public-key`, `--requirements`, `--out`. Exits `0` clean, `2` valid-not-clean, `3` corrupt.
   - Extend `scripts/reproduction_readiness_report.py` and `scripts/reproduction_readiness_artifact_shape_lint.py` with optional `--reproduction-bundle`. When supplied, the bundle becomes the sole source of the artifact index; `--artifact-dir`/`--artifact` are rejected on the same invocation to prevent silent conflicts.
   - `make reproduction-readiness-bundle-verify` produces `dist/self-harness-reproduction-bundle.json`.
   - `make release-candidate-evidence-reproduction` adds `reproduction_bundle` as a required gate; the default `make release-candidate-evidence` path is unchanged.

4. **Fail-closed semantics.**
   - Duplicate class entries: reject.
   - Same class, conflicting `(path, sha256, byte_size)`: reject.
   - Missing file, empty file, byte-size mismatch, sha mismatch: reject.
   - Bundle declaring a class not in the requirements catalog: reject.
   - Requirements class with no bundle entry on the hard path: reject.
   - Optional signature sidecar present but malformed or failing verification: reject.
   - Required signature sidecar absent on the hard path: reject.
   - Any `reproduction_claimed: true` anywhere in bundle or referenced artifacts: reject.
   - No live contact, no Docker/Harbor/PyPI/Sigstore/model/registry/scanner contact.

5. **Tests.**
   - Fixture bundle with one entry per required class, all class-shaped payloads from the existing `_class_shaped_payloads()` test helper: passes.
   - Duplicate class entry: fails closed with explicit reason.
   - Conflicting `(path, sha256, byte_size)` for same class: fails closed.
   - Unknown class in bundle: fails closed.
   - Missing file, empty file, byte-size mismatch, sha mismatch: each fails closed.
   - Missing required class on hard path: fails closed.
   - `reproduction_claimed: true` in bundle or in a referenced artifact: fails closed.
   - Optional signature absent on advisory path: passes.
   - Signature present and valid: passes.
   - Signature present but invalid: fails closed.
   - Hard-gate release-candidate evidence blocks when bundle verification fails or is absent.
   - Deterministic `report_hash` matches committed fixture (committed under `tests/fixtures/release_candidate/reproduction_bundle_result.json`).
   - CLI rejects simultaneous `--reproduction-bundle` and `--artifact-dir`/`--artifact`.

6. **Docs.**
   - Extend `docs/operations/benchmark_reproduction_readiness.md` with the bundle contract, required fields, conflict rules, signing expectations, fixture-rotation policy, and explicit non-reproduction boundary language.
   - Add `make reproduction-readiness-bundle-verify` to the release-gate list in `RELEASE.md` under the hard-gate subsection only.

7. **Stop conditions.**
   - Bundle schema, verifier, CLI, hard-gate integration, tests, and docs land.
   - Advisory reproduction-readiness fixture hash does not rotate.
   - Hard-gate fixture hash rotates only if a committed hard-gate fixture already exists; otherwise this slice introduces the first such fixture.
   - No audit schema, corpus schema, manifest schema, canonical readiness hash, default release path, or reproduction-claim change.

## Remaining Open Questions

Non-blocking; resolvable during implementation without re-architecting:

1. **Signing key custody.** Recommend documenting the bundle signature as operator material with the same external-signer seam used by corpus signing (P21/P40). Decision: do not require a dedicated bundle key; allow any operator-supplied Ed25519 public key. Confirm during implementation by reusing `scripts/verify_provenance_signature.py`-style verification.
2. **Whether to allow multiple content-identical entries for the same class.** Recommendation: no; require exactly one entry per class for auditability. Confirm by rejecting the lenient variant in tests.
3. **Whether `created_at` validation should enforce a freshness window.** Recommendation: out of scope for P54; the operator bundle freshness policy in P36 already covers release material. Confirm by *not* adding freshness logic to the bundle verifier.
4. **Whether the bundle should record which readiness-matrix dependency state each artifact corresponds to.** Recommendation: no; that mapping lives in the requirements catalog. The bundle only binds class → file integrity, keeping the slice boundary tight.
