# Round 1

CONVERGED: NO

## Verdict
Round 1 proposes P94 = "proposal candidate changed-surface name grounding to proposer context editable surfaces." This closes a real paper-fidelity gap left open by P93: candidates are currently bound to proposer-context surfaces only by `edited_surface_sha256`, leaving them free to declare arbitrary `changed_surfaces` *names* that never appear in the bounded editable-surface set the proposer actually saw. Section 3.3 requires proposals to be "mapped to a concrete editable surface," so a candidate touching an undeclared surface is not grounded in $B_t$ and should fail closed. The plan is executable and low-risk, but it should be refined in round 2 to (a) document the implicit surface-hash convention it relies on, and (b) decide whether the new check also belongs in `capture_manifest_diff.proposal-validation-derivation` so plan-vs-realized drift is symmetric.

## Critique
- **Evidence (repo):** `_cross_artifact_proposal_validation_binding` in `src/self_harness/reproduction_bundle.py` already iterates over `context_by_round[round_index]`, already computes `allowed_mechanisms` and `allowed_surfaces`, and already records `candidate_mechanism_violations` and `candidate_surface_violations`. The proposer-context `editable_surfaces.surfaces[]` records expose `name`, so the data needed for a name grounding check is already loaded.
- **Evidence (repo):** `_proposal_validation_candidate` in `src/self_harness/capture_extract.py` stamps `edited_surface_sha256 = _stable_payload_sha256({"changed_surfaces": changed_surfaces})`, i.e. the hash binds the surface *names* the candidate touched. But `_artifact_shapes._editable_surfaces_block` does not constrain `surface.sha256` to any function of `surface.name`; it is operator-supplied. Therefore the existing hash-only check accepts `changed_surfaces: ["undeclared"]` as long as the operator-supplied surface hash collides with the candidate hash.
- **Evidence (repo):** `_class_shaped_payloads` in `tests/test_reproduction_readiness.py` keeps candidate `changed_surfaces` (`["system_prompt"]`) aligned with the proposer-context surface `name` (`"system_prompt"`), so the happy path is preserved by the proposed invariant.
- **Inference (paper):** Section 3.3 ("A proposal must be grounded in a primary failure mechanism and mapped to a concrete editable surface") and Section 3.3 ("each individual edit is constrained to modify only the surface needed to address its selected mechanism") imply that the candidate surface set must be a subset of the editable surface set the proposer was shown. Name drift across the hash-binding check violates that contract.

## Required Changes
1. In `_cross_artifact_proposal_validation_binding`, for each round with `context_round`, derive `allowed_surface_names = {_required_row_str(s, "name", ...) for s in _context_editable_surfaces(context_round)}`. For each candidate with non-empty `changed_surfaces`, require `set(changed_surfaces) <= allowed_surface_names`. Append mismatches to a new `candidate_surface_name_violations` metadata bucket and to `failures`.
2. The existing `candidate_surface_violations` (hash check) stays in place; the new check is strictly tighter and the two are independent (hash matches a declared surface, names match declared surface names).
3. Extend `capture_manifest_diff._proposal_validation_round_summary` to also report per-round `candidate_changed_surface_names_by_candidate` and `editable_surface_names`, then add a `candidate_changed_surface_name_drift` sub-field under `round_violations` so plan-vs-realized rehearsal catches a planner that lists different surface names than the realized candidate.
4. Tests in `tests/test_reproduction_readiness.py`:
   - rewrite a candidate's `changed_surfaces` to `["undeclared-surface"]` while keeping `edited_surface_sha256` valid → bundle verification fails with a name-grounding message.
   - keep the current happy-path fixture unchanged (it already aligns names).
   - add a `capture_manifest_diff` rehearsal drift test where the planned `proposal_validation_manifest` declares a different `changed_surfaces` name set than the realized bundle.
5. Docs: append P94 to `docs/architecture/productionization_brief.md` and add a note in `docs/operations/benchmark_reproduction_readiness.md` describing the surface-hash convention (`surface.sha256 == sha256(stable_json({"changed_surfaces": [surface.name]}) + "\n")`) that makes hash grounding and name grounding coincide for faithful proposer logs.

## Revised Plan
**P94 — Proposal candidate changed-surface name grounding**

Files:
- `src/self_harness/reproduction_bundle.py`
  - In `_cross_artifact_proposal_validation_binding`, compute `allowed_surface_names` per round from `_context_editable_surface_rows(context_round)`.
  - For each candidate, when `changed_surfaces` is non-empty, compute `unknown_surface_names = sorted(set(changed_surfaces) - allowed_surface_names)`; if non-empty, append to `candidate_surface_name_violations` and to `failures`.
  - Surface the new bucket in the check metadata alongside `candidate_surface_violations` and `candidate_distinctness_violations`.
- `src/self_harness/capture_manifest_diff.py`
  - Extend `_proposal_validation_round_summary` to record per-round `editable_surface_names` and per-candidate `changed_surface_names`.
  - Add a `candidate_changed_surface_names` drift field inside `round_violations` entries so the rehearsal catches planner-vs-realized name drift.
- `tests/test_reproduction_readiness.py`
  - Add `test_reproduction_bundle_rejects_proposal_validation_unknown_current_surface_name`: rewrite candidate 0 in round 0 so `changed_surfaces = ["undeclared-surface"]` while preserving `edited_surface_sha256`; assert bundle verification fails on `cross_artifact_proposal_validation_binding` with `candidate_surface_name_violations`.
  - Extend an existing happy-path test to assert `candidate_surface_name_violations == []`.
- `tests/test_capture_manifest.py`
  - Add a rehearsal drift test where the planned `proposal_validation_manifest` round 0 candidate 0 declares `changed_surfaces: ["alt_surface"]` but the realized bundle keeps `["system_prompt"]`; assert the diff reports the drift.
- `docs/architecture/productionization_brief.md`
  - Append P94 entry citing paper Section 3.3 ("mapped to a concrete editable surface").
- `docs/operations/benchmark_reproduction_readiness.md`
  - Add a sub-section "Editable surface hash convention" stating the recommended hash so operator-supplied `surface.sha256` and capture-extract `edited_surface_sha256` coincide; note the P94 name check is the authoritative invariant and the hash convention exists for evidence-compactness only.

Non-goals (explicit):
- No schema version bumps for `proposal_validation_manifest` or `proposer_context_manifest`.
- No enforcement of single-surface minimality (`len(changed_surfaces) == 1`); the paper's "surface needed" wording permits multiple coordinated surfaces and the test fixture already stays at one.
- No semantic parsing of `decision_reason` or `rejection_reason`.
- No changes to P86 aggregate pass-count acceptance rule.
- No live Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud contact.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-diff-check`.

Stop conditions:
- All new tests pass and existing tests stay green.
- `make reproduction-readiness-check` still reports `reproduction_ready: false`.
- `tests/fixtures/canonical_audit_hash.txt` is unchanged.

## Remaining Open Questions
- Should the name-grounding invariant also be enforced inside `_artifact_shapes._proposal_validation_candidate` so that capture-extract rejects malformed audit rows pre-bundle? **Non-blocking for P94**: bundling is the canonical paper-reproduction gate; artifact-shape tightening can be a follow-up slice.
- Should the surface-hash convention be elevated to a hard shape rule (`surface.sha256 == sha256({"changed_surfaces":[name]})`)? **Non-blocking for P94**: documented as a convention; enforcement would invalidate operator logs that legitimately hash richer surface content.

[usage] {"completion_tokens": 5376, "completion_tokens_details": {"reasoning_tokens": 3619}, "prompt_tokens": 160228, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 165604}

---

# Round 2

CONVERGED: YES

## Verdict
P94 (proposal candidate changed-surface name grounding to proposer context editable surfaces) is ready to execute. Round 1 identified a genuine paper-fidelity gap: P93 binds candidates by `edited_surface_sha256` only, which permits candidates to declare arbitrary `changed_surfaces` *names* as long as an operator-supplied surface hash collides. The plan is concrete, low-risk, scoped, and has explicit non-goals and stop conditions. Material risks identified in Round 1 (implicit hash convention, asymmetric plan-vs-realized drift) have been addressed by adding the `capture_manifest_diff` rehearsal check and a documented surface-hash convention note.

## Critique
- **Evidence (repo):** `_cross_artifact_proposal_validation_binding` in `src/self_harness/reproduction_bundle.py` already loads `context_by_round[round_index]`, computes `allowed_mechanisms` and `allowed_surfaces`, and emits `candidate_mechanism_violations` / `candidate_surface_violations`. The editable-surface rows expose `name`, so the data needed for a name-subset check is already loaded — no new parsing is required.
- **Evidence (repo):** `_proposal_validation_candidate` in `src/self_harness/capture_extract.py` stamps `edited_surface_sha256` from `changed_surfaces` names, but `_editable_surfaces_block` in `_artifact_shapes.py` does not constrain `surface.sha256` to be any function of `surface.name`. The hash-only check can therefore pass while names drift.
- **Evidence (repo):** `_proposal_validation_round_summary` in `capture_manifest_diff.py` already produces per-round candidate-level metadata; extending it with `changed_surface_names` and `editable_surface_names` is additive and does not require a schema bump.
- **Inference (paper):** Section 3.3 ("mapped to a concrete editable surface", "modify only the surface needed") implies the candidate surface set must be a subset of the editable surface set the proposer saw. Name drift violates this contract even when hashes collide.

## Required Changes
None beyond the Round 1 revised plan. The plan already covers:
1. New `candidate_surface_name_violations` bucket and `failures` entry in `_cross_artifact_proposal_validation_binding`.
2. Preservation of the existing hash-based `candidate_surface_violations` (checks are independent).
3. Additive `capture_manifest_diff` rehearsal drift signal (`candidate_changed_surface_names` per round).
4. Two tests (negative name-grounding bundle failure, happy-path unchanged) plus one rehearsal drift test.
5. Documentation of the recommended `surface.sha256` convention so operator logs and capture-extract stay coherent.

## Revised Plan
Execute the P94 plan as written in Round 1. Files to modify:
- `src/self_harness/reproduction_bundle.py`
- `src/self_harness/capture_manifest_diff.py`
- `tests/test_reproduction_readiness.py`
- `tests/test_capture_manifest.py`
- `docs/architecture/productionization_brief.md` (append P94 entry)
- `docs/operations/benchmark_reproduction_readiness.md` (add editable-surface hash convention subsection)

Gates: `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-diff-check`.

Stop conditions: all listed tests pass; `make reproduction-readiness-check` still reports `reproduction_ready: false`; `tests/fixtures/canonical_audit_hash.txt` unchanged.

## Remaining Open Questions
Both Round 1 open questions are explicitly non-blocking and correctly deferred:
- Tightening `_artifact_shapes._proposal_validation_candidate` to reject name drift pre-bundle — follow-up slice.
- Elevating the surface-hash convention to a hard shape rule — intentionally not enforced because legitimate operator surface hashes may carry richer content.

No additional evidence or user decision is required to proceed.

[usage] {"completion_tokens": 954, "completion_tokens_details": {"reasoning_tokens": 146}, "prompt_tokens": 163769, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 164723}
