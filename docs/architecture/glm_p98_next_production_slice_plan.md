CONVERGED: YES

## Verdict

P98 is the smallest remaining high-value paper-faithfulness gap implementable offline: Section 3.2 mandates clusters be "ordered by their support and estimated actionability." The current `proposer_context_manifest` schema captures `size` (support) and `mechanism_sha256`/`causal_status_sha256`/`shared_symptoms_sha256`/`verifier_evidence_sha256` but has no machine-checkable presentation order or actionability attestation. The slice is additive, optional, requires no schema-version bump, no new artifact class, no canonical readiness hash rotation, no live contact. Both blocking design questions are resolvable from existing repo evidence and prior P95–P97 conventions; no further reviewer is required.

## Critique

Evidence (validated against supplied repo context):
- `src/self_harness/_artifact_shapes.py` `_HELD_IN_FAILURE_PATTERN_FIELDS` is the exact field set to extend; it already hosts the P95–P97 optional nullable hash convention.
- `src/self_harness/capture_extract.py::_normalize_failure_pattern_evidence_row` is the normalization seam for raw evidence → stable hash; it already handles `causal_status`, `shared_symptoms`, `verifier_evidence` with mismatch rejection.
- `src/self_harness/capture_manifest_build.py::_planned_artifact_stub` emits deterministic `proposer_context_manifest` stubs including per-cluster hashes; it is where rehearsal stubs would gain `presentation_order`.
- `src/self_harness/reproduction_bundle.py::_held_in_failure_patterns_block` and `_cross_artifact_proposer_context_evidence_binding` are where contiguous-permutation invariants and per-cluster evidence recording live.
- `src/self_harness/capture_manifest_diff.py::_proposer_context_failure_category_summary` is the established per-cluster drift comparison seam.
- Paper p.6 states ordering requirement verbatim.
- P93 already enforces per-round `(mechanism_sha256, edited_surface_sha256)` distinctness, proving within-round invariants are an accepted enforcement surface.

Inference (architecture decisions, labeled as inference):
- `presentation_order` should be machine-checkable contiguous-permutation evidence rather than an opaque hash because the paper treats exposure ordering as deterministic operator evidence, not as free-form proposer judgment. Mirrors the operator-declared-shape pattern from P89/P94 rather than the opaque-attestation pattern from P96/P97.
- `actionability_hint` should remain an opaque attestation (`actionability_hint_sha256`) because the paper calls it "estimated" — proposer judgment, not machine-recoverable. Matches P96/P97.
- `size` already implies `support_rank` deterministically (larger cluster = higher support). Treating it as a derived invariant avoids a redundant field and keeps the schema minimal.

Rejected alternatives:
- Single opaque `ordering_evidence_sha256` blob — loses deterministic rehearsal drift detection on the concrete ordering, which is the paper's actual exposure contract.
- Introducing a closed actionability vocabulary — explicitly deferred in P96/P97; no new evidence changes that.
- Binding `presentation_order` to proposer LLM request-log prompt order — request logs are opaque hashes (`request_sha256`), not prompt text; out of scope per P78/P91.

## Required Changes

Decisions (resolving round-1 blockers):
1. **Field shape: alternative (a).** Add optional nullable `presentation_order: non-negative int` and optional nullable `actionability_hint_sha256: 64-lowercase-hex`. When any pattern in a round declares `presentation_order`, all patterns in that round must declare it and the values must form a contiguous permutation of `0..(pattern_count-1)`.
2. **Drift scope.** `support_rank` is not a stored field; it is a derived invariant from `size` (document the tie-breaker as cluster-id ascending, deterministic). `presentation_order` and `actionability_hint_sha256` are independent operator-declared evidence; `proposer-context-evidence-derivation` MUST compare both planned vs realized per cluster when either side declares them.

No further blocking decisions remain.

## Revised Plan

**P98 — Failure-pattern presentation-order and actionability binding (Section 3.2 cluster ordering)**

Artifacts / schemas (all additive, optional, no schema-version bump):
- `src/self_harness/_artifact_shapes.py`:
  - Extend `_HELD_IN_FAILURE_PATTERN_FIELDS` with `presentation_order` (nullable non-negative int) and `actionability_hint_sha256` (nullable 64-lowercase-hex).
  - In `_held_in_failure_patterns_block`: validate each field as nullable with the established pattern; add round-level invariant — if any pattern declares `presentation_order`, all must, and values must be a contiguous permutation of `0..(len(patterns)-1)`.
- `src/self_harness/capture_extract.py`:
  - Extend `_normalize_failure_pattern_evidence_row` to accept raw `actionability_hint` (str) and emit `actionability_hint_sha256 = sha256(stable_json({"actionability_hint": value}) + "\n")`; reject supplied-hash mismatch (mirror P96 causal-status convention).
  - Pass `presentation_order` through verbatim when supplied (do not synthesize). Preserve absence.
- `src/self_harness/capture_manifest_build.py`:
  - Extend `_planned_artifact_stub` proposer-context pattern stubs to emit `presentation_order = pattern_index` and a deterministic `actionability_hint_sha256` per cluster so rehearsals exercise the new fields and the contiguous-permutation invariant.
- `src/self_harness/reproduction_bundle.py`:
  - In `_held_in_failure_patterns_block`: enforce contiguous-permutation invariant at shape-validation time (single source of truth).
  - In `_cross_artifact_proposer_context_evidence_binding`: record `presentation_order_declared_count`, `actionability_hint_sha256_count`, and any `presentation_order_violations` in metadata. Do not fail when absent (reduced bundles). Do not derive `support_rank` as a stored field — record the support-ordering rule in metadata for audit only.
- `src/self_harness/capture_manifest_diff.py`:
  - Extend `_proposer_context_failure_category_summary` (or sibling) to capture per-cluster `presentation_order` and `actionability_hint_sha256`.
  - Add drift comparisons to `_proposer_context_evidence_findings`: planned vs realized per-cluster `presentation_order` and `actionability_hint_sha256`, only when either side declares them. Absent on both sides for a cluster remains a pass.

Tests:
- `tests/test_capture_extract.py`:
  - Raw `actionability_hint` → hash normalization; mismatch rejection; malformed rejection; absence passthrough.
  - `presentation_order` passthrough; absence passthrough.
- `tests/test_reproduction_readiness.py::_class_shaped_payloads` / `_proposer_context_rounds`:
  - Add `presentation_order: index` and one `actionability_hint_sha256` to round-0 patterns; assert bundle still verifies.
  - Add a reduced-bundle variant proving absence of both fields still passes.
- `tests/test_capture_manifest.py`:
  - Extend planned-stub assertions for new fields.
  - Add drift test for `presentation_order` mismatch and a drift test for `actionability_hint_sha256` mismatch.

Docs:
- `docs/architecture/schema_changelog.md`: P98 entry under proposer-context manifest notes, including the contiguous-permutation invariant and the derived-from-size `support_rank` tie-breaker rule.
- `docs/operations/benchmark_reproduction_readiness.md`: extend `proposer_context_manifest` row and failure-pattern block description to mention optional `presentation_order` and `actionability_hint_sha256`; note that `support_rank` is derived from `size` (cluster-id ascending tie-breaker) and is not a stored field.
- `docs/operations/benchmark_reproduction_requirements.json`: extend `proposer_context_ingredients.notes` to reference ordering/actionability binding.
- `docs/architecture/productionization_brief.md`: append P98 entry in established style.

Non-goals:
- No closed actionability vocabulary.
- No stored `support_rank` field (derived invariant only).
- No requirement that every paper-faithful bundle disclose ordering evidence (remains optional/additive).
- No change to P93 candidate-distinctness signatures.
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, reproduction-claim change, or live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.
- No binding to proposer prompt text (out of scope; request log is opaque hashes).

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-extract-check`, `make reproduction-bundle-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- Release-candidate evidence fixture hash unchanged (default release path does not touch proposer context).
- Schema changelog entry present.

## Remaining Open Questions

Non-blocking advisory (future slices, not P98):
1. Whether a future slice should add an explicit `support_rank` stored field once multiple operators report divergent size-vs-rank conventions — defer until evidence emerges.
2. Whether `presentation_order` should eventually cross-bind to the proposer LLM request log's per-round prompt ordering — blocked on prompt-text artifacts, which are out of scope.
