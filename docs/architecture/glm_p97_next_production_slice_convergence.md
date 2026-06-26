# Round 1

CONVERGED: YES

## Verdict

P97 should add `shared_symptoms_sha256` and `verifier_evidence_sha256` as optional opaque-hash fields on `proposer_context_manifest` held-in failure patterns, closing the Section 3.2 cluster-composition gap (size, representative task instances, shared trace symptoms, verifier evidence, inferred agent mechanism). The slice is purely additive, requires no live infrastructure, no schema-version bump, no new artifact classes, and no canonical readiness hash rotation. Plan is concrete, low-risk, and faithful to the paper.

## Critique

Initial mental candidates considered but rejected:
- **Single-surface minimality enforcement** (Section 3.3): paper actually permits multi-surface edits when the mechanism demands it; enforcing single-surface would be over-restrictive and is explicitly listed as out-of-scope in P94.
- **Addressability declaration per pattern**: useful but overlaps heavily with the existing P93/P94 surface-hash and surface-name grounding. Marginal paper-faithfulness gain.
- **Per-candidate raw trace binding / separate baseline evaluation artifact**: explicitly listed as future work in P84/P85/P86 because it requires new live evidence shapes — disqualified by the no-live-infra constraint.
- **Semantic rejection-reason parsing**: explicitly out of scope across P84-P95.

The chosen slice (symptoms + verifier evidence) is the cleanest because:
1. Section 3.2 enumerates five cluster fields; current schema captures three plus the signature components. Two are missing.
2. Mirrors the established `mechanism_sha256` / `causal_status_sha256` opaque-hash pattern — no new vocabulary risk.
3. Optional/nullable — preserves all existing reduced bundles and avoids forcing operators to disclose raw trace text.
4. Bundles the cross-artifact binding inside the existing `cross_artifact_proposer_context_evidence_binding` check, so no new invariant name proliferation.

## Required Changes

The plan must, before execution, satisfy:
1. New fields are nullable; absence must not fail shape validation (reduced bundles).
2. Hash convention is `sha256(stable_json({"shared_symptoms": <list[str]>}) + "\n")` and analogously for `verifier_evidence`, matching P96's causal-status convention.
3. Capture-extract accepts either the raw nested list/string or only the hash; if both supplied, hash must agree (mirrors P96 causal_status handling).
4. No rotation of `tests/fixtures/canonical_audit_hash.txt` — the engine's default audit output is unchanged.
5. No new required artifact class, no `reproduction_claimed` change, no audit/corpus schema bump.
6. Schema changelog and benchmark reproduction docs updated.
7. Stop condition: all existing tests still pass; new tests cover (a) present-and-matching, (b) present-and-mismatch, (c) absent-and-skipped, (d) raw-then-hash normalization in capture-extract.

## Revised Plan

**P97: Failure-pattern shared-symptom and verifier-evidence binding**

Artifacts / schemas:
- `src/self_harness/_artifact_shapes.py`:
  - Add `shared_symptoms_sha256` and `verifier_evidence_sha256` to `_HELD_IN_FAILURE_PATTERN_FIELDS`.
  - Extend `_held_in_failure_patterns_block` to validate them as optional 64-lowercase-hex-or-null, mirroring `causal_status_sha256`.
- `src/self_harness/capture_extract.py`:
  - Extend `_normalize_causal_status_row` (or sibling helper) to also normalize `shared_symptoms` (list[str]) and `verifier_evidence` (list[str]) into their stable hashes; reject malformed types and supplied-hash mismatches.
- `src/self_harness/capture_manifest_build.py`:
  - Extend `_planned_artifact_stub` proposer-context pattern stub with the two new hashes derived from deterministic stub lists.
- `src/self_harness/reproduction_bundle.py`:
  - Inside `cross_artifact_proposer_context_evidence_binding`, when the realized pattern declares either hash, record it in metadata; do not fail when absent.
  - Future hook only: no cross-artifact comparison partner artifact carries these hashes today, so the binding is currently a captured-attestation record, not a multi-artifact invariant. Document that boundary.

Tests:
- `tests/test_capture_extract.py`: add cases for raw→hash normalization, mismatch rejection, malformed input rejection, and absence passthrough.
- `tests/test_reproduction_readiness.py` / fixture payloads in `_class_shaped_payloads`: include the two new hashes on round-0 cluster-0; ensure bundle still verifies.
- `tests/test_capture_manifest.py`: extend `_proposer_context_rounds` to emit the new hashes; verify diff still passes; add a drift test for `shared_symptoms_sha256`.
- Add a test proving reduced bundles (no symptoms/evidence hashes) still verify clean.

Docs:
- `docs/architecture/schema_changelog.md`: add P97 entry under proposer context manifest notes.
- `docs/operations/benchmark_reproduction_readiness.md`: extend the `proposer_context_manifest` row description and the failure-pattern block description to mention the two new optional hashes.
- `docs/operations/benchmark_reproduction_requirements.json`: update the `proposer_context_ingredients` notes to reference symptom/evidence binding.
- `docs/architecture/productionization_brief.md`: append P97 summary mirroring prior entries' style.

Non-goals:
- No closed vocabulary for symptom text or verifier evidence text.
- No new artifact class.
- No primary audit schema, corpus schema, or reproduction-claim change.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.
- No enforcement that every paper-faithful bundle must disclose the hashes (they remain optional).
- No new cross-artifact invariant beyond recording the attestation.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-extract-check`, `make reproduction-bundle-check` all green.
- Canonical readiness hash unchanged.
- Release-candidate evidence fixture hash unchanged unless the operator-evidence gate metadata legitimately rotates (it should not, because default release path doesn't touch proposer context).
- Schema changelog entry present.

## Remaining Open Questions

None blocking. Two advisory items for future slices (not P97):
1. Whether `verifier_evidence_sha256` should eventually bind to a per-task verifier-evidence artifact class (would require a new live evidence shape — out of scope here).
2. Whether the capture-manifest `proposer-context-evidence-derivation` diff should compare planned-vs-realized symptom/evidence hashes even when the planned stub uses deterministic placeholder lists — answer for P97: yes, include them in the diff comparison so rehearsals catch drift, but only when both sides declare the hashes.

[usage] {"completion_tokens": 3598, "completion_tokens_details": {"reasoning_tokens": 2152}, "prompt_tokens": 176303, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 179901}
