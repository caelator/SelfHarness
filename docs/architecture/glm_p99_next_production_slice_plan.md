CONVERGED: YES

## Verdict

P99 (accepted/merged candidate editable-surface distinctness for MERGEACCEPTED compatibility, Algorithm 1 line 21) is the smallest remaining offline-implementable paper-fidelity slice. Both round-1 blocking decisions are resolvable from existing repo evidence and the P94 surface-hash convention. No external services, schema bumps, new artifact classes, canonical readiness hash rotation, or reproduction-claim changes are required. Fixture inspection confirms the current reproduction-readiness fixture does not collide same-surface accepted candidates, so no fixture rotation is needed.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` already iterates per-round candidates, computes `(targeted_mechanism_sha256, edited_surface_sha256)` signatures, and records `candidate_distinctness_violations` (P93). The new check is a strict superset over accepted/merged candidates keyed by `edited_surface_sha256` alone.
- `src/self_harness/_artifact_shapes.py::_proposal_validation_candidate` validates `changed_surfaces` as a non-empty unique string list for non-`no_editable_surface` candidates and forbids empty lists for accepted/rejected/superseded/merged/execution-failure candidates.
- P94 establishes the binding convention `edited_surface_sha256 = sha256(stable_json({"changed_surfaces":[name]}))`, captured in `capture_extract.py::_proposal_validation_candidate` via `_stable_payload_sha256({"changed_surfaces": changed_surfaces})`. The hash deterministically encodes the surface-name set.
- Paper p.4 Algorithm 1 line 21 uses "MERGEACCEPTED" over "compatible" candidates; p.6 Section 3.3 requires each edit to "modify only the surface needed"; Figures 5b/6b/10b all show merged accepted candidates each targeting a distinct surface.
- `tests/test_reproduction_readiness.py::_class_shaped_payloads::_proposal_validation_rounds` produces exactly one accepted and one no-surface-invalid candidate per round (accepted edited_surface_sha256 = "7"*64; invalid has its own `sha256({"changed_surfaces": []})` hash). No round has two accepted/merged candidates sharing an `edited_surface_sha256`.

Inference (architecture decisions):
- Blocking decision 1 (fail vs advisory): **fail.** MERGEACCEPTED producing two edits to the same opaque surface is a genuine paper-contract violation, not a stylistic concern. The fixture is clean, so the readiness hash does not rotate.
- Blocking decision 2 (hash-only vs hash+name union): **hash-only.** P94's convention makes `edited_surface_sha256` a deterministic function of the changed-surface name set, so name-union drift is already encoded in the hash. A separate name-union check would be redundant.
- The invariant applies whenever ≥2 accepted/merged candidates exist in a round, regardless of `merge_decision`. `merge_decision` is derived and should not gate a structural invariant.

Rejected alternatives:
- Single-surface minimality per candidate — explicitly deferred in P94; out of scope.
- Closed merge-compatibility vocabulary — paper does not specify one; defer.
- Raw-edit semantic conflict detection — out of scope (patches are opaque).
- Binding `merge_decision` semantics — paper does not specify MERGEACCEPTED concretely; over-reach.

## Required Changes

None blocking. Decisions resolved:
1. `merge_surface_conflict_violations` fails the bundle (`status: fail`) when non-empty.
2. Invariant keys off `edited_surface_sha256` only; name-union drift is already encoded by the P94 hash convention.

## Revised Plan

**P99 — Accepted-candidate editable-surface distinctness for MERGEACCEPTED compatibility (Algorithm 1 line 21)**

Code (no schema-version bump, no new artifact class):
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding`:
  - While iterating candidates in each round, collect `(proposal_id, edited_surface_sha256)` for candidates with `audit_decision` in `{accepted, merged}`.
  - If any `edited_surface_sha256` value appears more than once among accepted/merged candidates in the same round, record a `merge_surface_conflict_violations` entry with `round_index`, conflicting surface hash, and offending `proposal_ids`.
  - Add `merge_surface_conflict_violations` (default `[]`) to the check metadata.
  - Fail the check (`status: fail`) when `merge_surface_conflict_violations` is non-empty. Failure detail: "accepted or merged proposal validation candidates must target pairwise-distinct editable surfaces within a round".
  - Update the `acceptance_rule_boundary`/boundary metadata string to note that MERGEACCEPTED same-surface conflicts are now machine-checked.
- `src/self_harness/capture_manifest_diff.py::_proposal_validation_round_summary`:
  - Add `accepted_merged_surface_sha256s: dict[str, list[str]]` (surface hash → proposal_ids) to the per-round summary.
  - In `_proposal_validation_findings`, compare planned vs realized `accepted_merged_surface_sha256s` per round when either side declares them; absent on both sides for a round remains a pass.

Tests:
- `tests/test_reproduction_readiness.py`:
  - Audit `_class_shaped_payloads` / `_proposal_validation_rounds` to confirm no round has two accepted/merged candidates sharing `edited_surface_sha256`. (Fixture already satisfies this; add an explicit assertion test.)
  - Add a test that rewrites a second accepted candidate in round 0 to share `edited_surface_sha256` (`"7"*64`) with the existing accepted candidate and asserts `cross_artifact_proposal_validation_binding` fails with a non-empty `merge_surface_conflict_violations` entry naming both proposal ids.
  - Add a test confirming rejected/invalid candidates sharing a surface hash with an accepted candidate do *not* trigger the violation.
- `tests/test_capture_manifest.py`:
  - Extend `proposal-validation-derivation` drift coverage to assert planned-vs-realized `accepted_merged_surface_sha256s` comparison. Add a drift test that flips a planned accepted candidate's `edited_surface_sha256` and asserts the diff fails.

Docs:
- `docs/architecture/schema_changelog.md`: P99 entry under proposal-validation manifest notes, explicitly stating this is a behavioral tightening of `cross_artifact_proposal_validation_binding`, not a schema bump; reference Algorithm 1 line 21 and Section 3.3.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposal_validation_records.notes` to reference the accepted-candidate surface-distinctness invariant and its Algorithm 1 line 21 grounding.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposal_validation_manifest` row description and the `cross_artifact_proposal_validation_binding` rejection list.
- `docs/architecture/productionization_brief.md`: append P99 entry in established style.

Non-goals:
- No single-surface minimality per candidate (P94 deferral stands).
- No closed merge-compatibility vocabulary.
- No raw-edit semantic conflict detection.
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No change to P93 candidate-distinctness signatures.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-extract-check`, `make reproduction-bundle-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- Release-candidate evidence fixture hash unchanged (default release path does not exercise proposer artifacts).
- Schema changelog entry present.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether a future slice should extend surface-distinctness into a full cross-round mechanism/surface compatibility graph — defer until operators report concrete merge incompatibilities.
2. Whether `merge_decision: none` rounds with exactly one accepted candidate should be exempt by definition — trivially satisfied under the current rule (single candidate cannot conflict); no special-casing needed.
