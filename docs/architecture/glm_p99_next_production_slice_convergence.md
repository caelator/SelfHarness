# Round 1

CONVERGED: NO

## Verdict

**Initial candidate: P99 — Accepted-candidate editable-surface distinctness for MERGEACCEPTED compatibility (Algorithm 1 line 21).**

Algorithm 1 line 21 merges "compatible" accepted candidates into h_{t+1}. The paper's word "compatible" is a contract that is not currently machine-checked. P93 enforces per-round candidate distinctness by `(targeted_mechanism_sha256, edited_surface_sha256)` signature, which permits two accepted candidates to target the *same* editable surface via *different* mechanisms. When such candidates are both accepted and merged, the merge is only safe if the two edits to the same surface are semantically compatible — a property that cannot be verified from the current opaque `summary_sha256` / `edited_surface_sha256` evidence.

The paper's own retained-edit examples (Figures 5b, 6b, 10b) show merged candidates each targeting a distinct surface (e.g., MiniMax: bootstrap instruction + runtime policy; Qwen: middleware + subagent + skill + precheck; GLM: session tools + external compute + artifacts + implementation nudge). No figure shows two merged accepted edits hitting the same surface. Combined with Section 3.3's minimality language ("modify only the surface needed to address its selected mechanism"), this supports a conservative offline invariant: within a validation round, accepted/merged candidates must target pairwise-distinct editable surfaces.

The slice is narrow, offline, additive, requires no schema-version bump, no new artifact class, no canonical readiness hash rotation, and no live contact. It tightens an unenforced paper contract rather than adding new evidence shapes.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_proposal_validation_candidate` already validates `changed_surfaces` as a non-empty unique string list for non-`no_editable_surface` candidates.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` already iterates per-round candidates and records `candidate_distinctness_violations` for duplicate `(targeted_mechanism_sha256, edited_surface_sha256)` signatures (P93).
- `proposal_validation_manifest` candidate rows already carry `audit_decision` closed to `{accepted, rejected, superseded, merged, invalid}` and `edited_surface_sha256`.
- Paper p.4 Algorithm 1 line 21 uses "MERGEACCEPTED" over "compatible" candidates; p.6 Section 3.3 states each edit is "constrained to modify only the surface needed."
- P94 changelog explicitly notes single-surface minimality is *not* enforced; P99 is a weaker, merge-time surface-distinctness check that does not require single-surface minimality per candidate.

Inference (architecture decisions, labeled as inference):
- Surface-distinctness among *accepted* candidates is a conservative proxy for merge compatibility. It is stricter than the paper's vague "compatible" but looser than forbidding same-surface candidates entirely (rejected/invalid candidates may still share a surface).
- The invariant should apply only to `accepted` and `merged` candidates within the same round; `rejected`, `superseded`, and `invalid` candidates are exempt because they do not contribute to the merged harness.
- This is a behavioral tightening of an existing cross-artifact check, not a schema change. Existing reduced bundles that do not trigger the new violation remain valid; only bundles that previously passed with conflicting same-surface accepted candidates will now fail. This is the intended paper-fidelity improvement.

Rejected alternatives:
- **Single-surface minimality per candidate (len(changed_surfaces) == 1)** — explicitly deferred in P94; more invasive; rejects multi-surface candidates that may be legitimate in reduced fixtures. Too broad for one slice.
- **Closed merge-compatibility vocabulary** — no paper basis for a specific vocabulary; "compatible" is underspecified. Defer.
- **Raw-edit semantic conflict detection** — requires raw patch bytes in the bundle, which the project deliberately keeps opaque (`summary_sha256`). Out of scope.
- **Binding `merge_decision` to a paper-specified merge function** — paper does not specify MERGEACCEPTED concretely; over-reach.

## Required Changes

Before P99 can be CONVERGED: YES, the plan must decide:

1. **Violation scope.** Should the surface-distinctness invariant fail the bundle (`status: fail` in `cross_artifact_proposal_validation_binding`), or should it be recorded as advisory metadata only? Recommendation: fail, because MERGEACCEPTED producing a conflicting harness state is a genuine paper-contract violation, not a stylistic concern. But this is a behavioral change that could rotate the committed reproduction-readiness fixture hash if the fixture happens to contain same-surface accepted candidates. The fixture must be audited.

2. **Interaction with `merge_decision`.** The round-level `merge_decision` field is closed to `{accepted, rejected, none}`. Should a round with `merge_decision: accepted` be required to have pairwise-surface-distinct accepted/merged candidates, while `merge_decision: rejected`/`none` rounds are exempt? Or should the invariant apply whenever ≥2 accepted/merged candidates exist regardless of `merge_decision`? Recommendation: apply whenever ≥2 accepted/merged candidates exist in a round; `merge_decision` is a derived label and should not gate a structural invariant.

Both decisions are plannable from existing repo evidence; round 2 can resolve them without new external input.

## Revised Plan

**P99 — Accepted-candidate editable-surface distinctness for MERGEACCEPTED compatibility (Algorithm 1 line 21)**

Artifacts / schemas (no schema-version bump, no new artifact class):
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding`:
  - While iterating candidates in each round, collect `(proposal_id, edited_surface_sha256)` for candidates with `audit_decision` in `{accepted, merged}`.
  - If any `edited_surface_sha256` value appears more than once among accepted/merged candidates in the same round, record a `merge_surface_conflict_violations` entry with round_index, the conflicting surface hash, and the offending proposal_ids.
  - Add `merge_surface_conflict_violations` to the check metadata.
  - Fail the check (`status: fail`) when violations are non-empty.
- `src/self_harness/capture_manifest_diff.py::_proposal_validation_round_summary`:
  - Add `accepted_merged_surface_sha256s: dict[str, list[str]]` (surface hash → proposal_ids) to the per-round summary so plan-vs-realized rehearsals detect drift in the accepted-surface set.
  - Include a drift comparison in `proposal-validation-derivation` when planned and realized accepted-surface sets differ.

Tests:
- `tests/test_reproduction_readiness.py`:
  - Audit `_class_shaped_payloads` / `_proposal_validation_rounds` to confirm no round has two accepted/merged candidates sharing `edited_surface_sha256`. If any round does, adjust the fixture so accepted candidates use distinct surfaces (this is the likely fixture-rotation trigger).
  - Add a test that rewrites a second accepted candidate in a round to share `edited_surface_sha256` with the first and asserts `cross_artifact_proposal_validation_binding` fails with `merge_surface_conflict_violations`.
  - Add a test confirming rejected/invalid candidates sharing a surface with an accepted candidate do *not* trigger the violation.
- `tests/test_capture_manifest.py`:
  - Extend `proposal-validation-derivation` drift coverage to assert planned-vs-realized accepted-surface set comparison.

Docs:
- `docs/architecture/schema_changelog.md`: P99 entry under proposal-validation manifest notes, explicitly stating this is a behavioral tightening of `cross_artifact_proposal_validation_binding`, not a schema bump.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposal_validation_records.notes` to reference the accepted-candidate surface-distinctness invariant and its Algorithm 1 line 21 grounding.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposal_validation_manifest` row description.
- `docs/architecture/productionization_brief.md`: append P99 entry.

Non-goals:
- No single-surface minimality per candidate (P94 deferral stands).
- No closed merge-compatibility vocabulary.
- No raw-edit semantic conflict detection.
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation (unless the fixture itself contains a conflict, in which case the fixture rotates but `tests/fixtures/canonical_audit_hash.txt` stays unchanged because the toy audit does not flow through proposal_validation_manifest).
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.
- No change to P93 candidate-distinctness signatures.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-extract-check`, `make reproduction-bundle-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- Release-candidate evidence fixture hash unchanged unless the release-candidate fixture path exercises proposer artifacts (audit; likely unchanged because the default release path omits proposer context).
- Schema changelog entry present.

## Remaining Open Questions

Blocking (resolve in round 2):
1. Fail-versus-advisory decision for `merge_surface_conflict_violations` (recommendation: fail).
2. Whether the invariant keys off `edited_surface_sha256` only, or also off `changed_surfaces` name union, to catch two accepted candidates that bind the same surface hash but declare different surface names (edge case; likely impossible given P94 name-grounding, but worth confirming).

Non-blocking advisory:
3. Whether a future slice should extend this to a full single-surface-per-candidate minimality invariant (P94 territory); defer.
4. Whether `merge_decision: none` rounds with exactly one accepted candidate should be exempt by definition (trivially satisfied; no special-casing needed).

[usage] {"completion_tokens": 7148, "completion_tokens_details": {"reasoning_tokens": 5038}, "prompt_tokens": 188222, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 195370}

---

# Round 2

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

[usage] {"completion_tokens": 2149, "completion_tokens_details": {"reasoning_tokens": 417}, "prompt_tokens": 192468, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 194617}
