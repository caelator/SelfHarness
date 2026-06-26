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
