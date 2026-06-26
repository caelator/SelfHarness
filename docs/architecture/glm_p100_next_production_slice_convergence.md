# Round 1

CONVERGED: YES

## Verdict

P100 — Per-candidate single-surface minimality (paper Section 3.3: "each individual edit is constrained to modify only the surface needed to address its selected mechanism") is the smallest remaining offline-implementable paper-fidelity slice. P94 explicitly deferred this invariant. The existing test fixtures already comply (every non-`no_editable_surface` candidate declares exactly one changed surface), so the slice tightens the shape validator and capture-manifest diff without rotating canonical hashes, bumping artifact schemas, adding artifact classes, or contacting live services. Both candidate blocking decisions (fail-vs-advisory, exemption scope) are resolvable from the supplied repo evidence with clear recommendations, so no further review round is required.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py::_proposal_validation_candidate` currently validates `changed_surfaces` as a non-empty unique string list for non-`no_editable_surface` candidates, but permits `len > 1`. The `no_editable_surface` invalid path is the only case allowed to have an empty list.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_proposal_validation_binding` already records per-candidate `changed_surfaces` for surface-name grounding (P94) and accepted/merged surface-distinctness (P99). Single-surface minimality is the per-candidate analog that P94 explicitly deferred: "single-surface minimality per candidate (len(changed_surfaces) == 1) — explicitly deferred in P94; more invasive; rejects multi-surface candidates that may be legitimate in reduced fixtures."
- Paper p.6 Section 3.3: "each individual edit is constrained to modify only the surface needed to address its selected mechanism, preserve unrelated harness behavior, and avoid broad rewrites of the agent control architecture." Singular "surface" + "preserve unrelated harness behavior" only makes sense under single-surface scoping.
- Paper Figures 5b, 6b, 10b retained-edit rows each map to a single surface (bootstrap instruction, runtime policy, middleware, subagent, skill, precheck, session tools, external compute, artifacts, implementation nudge). No figure shows a multi-surface candidate.
- `tests/test_reproduction_readiness.py::_proposal_validation_candidate` fixture: `changed_surfaces = [] if invalid_no_surface else ["system_prompt"]`. Every non-`no_editable_surface` fixture candidate has exactly one surface.
- `tests/test_capture_extract.py::_audit_proposal` fixture: `"changed_surfaces": [surface]` — single surface per audit proposal row.
- The toy audit path feeding `tests/fixtures/canonical_audit_hash.txt` uses single-surface proposals, so tightening the shape validator does not rotate the canonical audit hash.

Inference (architecture decisions, labeled as inference):
- **Fail-vs-advisory (blocking decision 1): fail.** A multi-surface candidate is a direct paper-contract violation, not a stylistic concern. The shape validator is the correct enforcement point because minimality is intrinsic to the candidate, not a cross-artifact property.
- **Exemption scope (blocking decision 2):** The invariant applies to every candidate whose `validation_failure_category != "no_editable_surface"`. `execution_failure` invalid candidates retain a non-empty `changed_surfaces` list and must still be single-surface (they attempted a minimal edit that failed at execution time). Only `no_editable_surface` invalid candidates keep the empty-list exemption already encoded by P87.
- **Enforcement layer:** shape validator is authoritative. The cross-artifact binding does not need a redundant check because shape-invalid artifacts never reach cross-artifact binding (they fail the `artifact_proposal_validation_manifest` check first). Capture-manifest diff records a per-round `single_surface_violation_count` for plan-vs-realized drift visibility, mirroring the P99 `accepted_merged_surface_sha256s` approach.
- **Hash rotation:** none. Canonical audit hash unchanged (toy runner produces single-surface proposals). Reproduction-readiness fixture unchanged (`_proposal_validation_candidate` fixture already uses one surface). Release-candidate evidence fixture unchanged.

Rejected alternatives:
- **Cross-artifact-only enforcement (no shape change):** rejected because minimality is intrinsic to the candidate shape, not a binding property. Reduced non-paper bundles would also escape enforcement, which is inconsistent with the paper contract.
- **Banning multi-surface proposals at the proposer layer:** rejected as out of scope; the reproduction bundle is the paper-fidelity enforcement surface, not proposer internals.
- **Closed editable-surface vocabulary:** rejected; paper explicitly allows "broader structural mechanisms, such as subagent-based decomposition and middleware creation," so the surface vocabulary is open by design.
- **Requiring the single surface name to match `edited_surface_sha256` payload:** already enforced by P94's hash convention; no new work.

## Required Changes

None blocking. Decisions resolved:
1. `changed_surfaces` for non-`no_editable_surface` candidates must contain exactly one entry. Shape validator fails closed on violation.
2. `no_editable_surface` invalid candidates keep the P87 empty-list exemption; `execution_failure` invalid candidates must be single-surface.

## Revised Plan

**P100 — Per-candidate single-surface minimality (paper Section 3.3)**

Code (no schema-version bump, no new artifact class, no canonical hash rotation):
- `src/self_harness/_artifact_shapes.py::_proposal_validation_candidate`:
  - After the existing `changed_surfaces` non-empty/non-duplicate validation, add: when `validation_failure_category != "no_editable_surface"`, require `len(changed_surfaces) == 1`; otherwise return `f"{label}.changed_surfaces must contain exactly one surface for non-no_editable_surface candidates"`.
  - Update the inline boundary comment to reference paper Section 3.3 minimality.
- `src/self_harness/capture_manifest_diff.py::_proposal_validation_round_summary`:
  - Add `single_surface_violation_count: int` to each round summary, counting candidates with `validation_failure_category != "no_editable_surface"` and `len(changed_surfaces) != 1`.
  - Include `single_surface_violation_count` in the `proposal-validation-derivation` per-round drift keys so planned-vs-realized divergence is visible.

Tests:
- `tests/test_reproduction_readiness.py`:
  - Add an explicit assertion that every non-`no_editable_surface` candidate in `_class_shaped_payloads()._proposal_validation_rounds` has exactly one entry in `changed_surfaces`. (Fixture already satisfies this; pin it.)
  - Add a test that loads the class-shaped `proposal_validation_manifest` artifact, rewrites one accepted candidate's `changed_surfaces` to `["system_prompt", "tool_manifest"]`, recomputes `edited_surface_sha256` consistently, writes the artifact, and asserts `verify_reproduction_bundle` fails at the `artifact_proposal_validation_manifest` check with "exactly one surface" in the detail.
  - Add a test confirming `execution_failure` invalid candidates with `changed_surfaces` of length 2 are rejected by the shape validator.
  - Add a test confirming `no_editable_surface` invalid candidates with empty `changed_surfaces` still pass.
- `tests/test_capture_manifest.py`:
  - Extend `proposal-validation-derivation` drift coverage to assert `single_surface_violation_count` is compared. Add a drift test that flips a planned candidate to two surfaces and asserts the diff records the drift.

Docs:
- `docs/architecture/schema_changelog.md`: add P100 entry under proposal-validation manifest notes, explicitly stating this is a behavioral tightening of `_proposal_validation_candidate`, not a schema bump; reference Section 3.3 ("modify only the surface needed") and the P94 deferral.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposal_validation_records.notes` to reference the single-surface minimality invariant and its Section 3.3 grounding.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposal_validation_manifest` row description to note that non-`no_editable_surface` candidates must declare exactly one changed surface.
- `docs/architecture/productionization_brief.md`: append P100 entry in established style.

Non-goals:
- No closed editable-surface vocabulary.
- No proposer-layer enforcement of minimality (bundle/shape layer only).
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No change to P87 `no_editable_surface` empty-list exemption.
- No change to P93 candidate-distinctness signatures or P99 accepted-surface distinctness.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-extract-check`, `make reproduction-bundle-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- `tests/fixtures/release_candidate/expected_hash.txt` unchanged (default release path does not exercise the new violation).
- Schema changelog entry present and references Section 3.3 + P94 deferral.

## Remaining Open Questions

Non-blocking advisory (future slices):
1. Whether a future slice should extend minimality into a full "minimal patch" semantic check over raw op payloads — defer until operators report real over-broad proposals; raw patches are deliberately opaque in the current bundle.
2. Whether `proposal_validation_manifest` should optionally disclose per-candidate `primary_edited_surface` as a first-class named field distinct from the list — defer; P94's `edited_surface_sha256` already encodes the single-surface name set deterministically.
3. Whether the closed `_PROPOSAL_VALIDATION_DECISIONS` set should gain a `superseded`-vs-`rejected` distinction grounded in paper language — paper uses only "accepted"/"rejected" semantics; current five-state closure is an audit convenience, not a paper contract; leave as is.

[usage] {"completion_tokens": 7312, "completion_tokens_details": {"reasoning_tokens": 5188}, "prompt_tokens": 190363, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 197675}
