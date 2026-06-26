# Round 1

CONVERGED: NO

## Verdict

Initial candidate plan: **P98 — Failure-pattern ordering / actionability ordering binding for Section 3.2 cluster presentation order.** The paper explicitly states clusters are "ordered by their support and estimated actionability, so that the proposer is exposed first to recurring mechanisms that are more likely to map to a high-value harness modification." The current `proposer_context_manifest` schema accepts `patterns[]` as an unordered list with no `support_rank`, `actionability_hint`, or `presentation_order` evidence. This is a genuine paper-faithfulness gap that can be closed offline, additively, without live infrastructure, schema-version bumps, or canonical hash rotation. The slice is consistent with the P95/P96/P97 opaque-attestation pattern.

The plan is not yet CONVERGED because two material design choices need to be pinned before execution; both can be resolved in round 2.

## Critique

Evidence (from supplied repo context):
- P97 just shipped `shared_symptoms_sha256` / `verifier_evidence_sha256` on held-in failure patterns as optional opaque hashes — establishes the additive-optional pattern.
- `src/self_harness/_artifact_shapes.py` `_HELD_IN_FAILURE_PATTERN_FIELDS` is the field set that P98 would extend.
- `docs/source/self_harness_2606_09498.txt` page 5–6 specifies the ordering requirement verbatim.
- P93 enforces candidate distinctness by `(targeted_mechanism_sha256, edited_surface_sha256)` signature but does not capture within-round cluster ordering.
- Capture-extract `_normalize_failure_pattern_evidence_row` is the natural place to normalize raw ordering hints into a stable hash/field.

Inference:
- Paper treats "support" (cluster size) as a deterministic, machine-checkable signal; "estimated actionability" is the proposer's judgment and therefore better captured as an opaque attestation rather than a closed vocabulary (consistent with how P96/P97 treat causal-status and symptom text).
- Reusing the `*_sha256` opaque-attestation convention avoids introducing a closed actionability vocabulary, which the project has repeatedly deferred.

Rejected alternatives:
- **Rationale / expected_effect / regression_risks content binding** — already captured as `summary_sha256`; binding to proposer-context prior edits is interesting but more invasive and risks duplicating P93's mechanism-surface distinctness.
- **Merge strategy binding (Algorithm 1 line 21 MERGEACCEPTED)** — paper does not specify a concrete merge function; adding one would over-reach beyond paper fidelity.
- **Per-task candidate-vs-baseline acceptance rule** — explicitly listed as out-of-scope across P84–P90 because it requires new live shapes.
- **Closed symptom / causal-status vocabularies** — explicitly deferred in P96/P97.

## Required Changes

Before P98 can be CONVERGED: YES, the plan must decide:
1. **Field shape for ordering evidence.** Pick one and document it:
   - (a) optional integer `presentation_order` (0-indexed, contiguous, required-when-any-pattern-declares-it), plus optional opaque `actionability_hint_sha256`; OR
   - (b) optional opaque `ordering_evidence_sha256` over `{support_rank: int, actionability_hint: str}` only.
   Recommendation: (a) — `presentation_order` is machine-checkable and gives operators deterministic rehearsal drift detection; `actionability_hint_sha256` mirrors the established opaque-attestation pattern.
2. **Drift-detection scope in capture-manifest diff.** Decide whether `proposer-context-evidence-derivation` must compare planned vs realized `presentation_order` and `actionability_hint_sha256` per cluster, and whether `size` already implies a `support_rank` tie-breaker rule (recommend: yes, derive `support_rank` from `size` as a free invariant; `presentation_order` is independent operator evidence).

Both decisions are plannable, not evidentiary — round 2 can resolve them.

## Revised Plan

**P98 — Failure-pattern presentation-order and actionability binding (Section 3.2 cluster ordering)**

Artifacts / schemas (all additive, optional, no schema-version bump):
- `src/self_harness/_artifact_shapes.py`:
  - Extend `_HELD_IN_FAILURE_PATTERN_FIELDS` with optional nullable `presentation_order` (non-negative int) and `actionability_hint_sha256` (64 lowercase hex or null).
  - Add validation: if any pattern in a round declares `presentation_order`, all patterns in that round must declare it, values must be a contiguous permutation of `0..(pattern_count-1)`.
- `src/self_harness/capture_extract.py`:
  - Extend `_normalize_failure_pattern_evidence_row` to accept raw `actionability_hint` (str) and emit `actionability_hint_sha256 = sha256(stable_json({"actionability_hint": value}) + "\n")`; reject mismatched supplied hashes (mirror P96 causal-status convention).
  - Pass through `presentation_order` verbatim when supplied; do not synthesize it.
- `src/self_harness/capture_manifest_build.py`:
  - Extend `_planned_artifact_stub` proposer-context pattern stub to emit `presentation_order = pattern_index` and a deterministic `actionability_hint_sha256` so rehearsals exercise the new fields.
- `src/self_harness/reproduction_bundle.py`:
  - Inside `cross_artifact_proposer_context_evidence_binding`, when any pattern declares `presentation_order`, record and enforce the contiguous-permutation invariant; record `presentation_order_declared_count` and `actionability_hint_sha256_count` in metadata. Absence remains valid for reduced bundles.
- `src/self_harness/capture_manifest_diff.py`:
  - Extend `_proposer_context_failure_category_summary` (or sibling) to capture per-cluster `presentation_order` and `actionability_hint_sha256`; emit drift rows in `proposer-context-evidence-derivation` when planned and realized values differ.

Tests:
- `tests/test_capture_extract.py`: raw→hash normalization for `actionability_hint`; mismatch rejection; `presentation_order` passthrough; absence passthrough.
- `tests/test_reproduction_readiness.py`: extend `_class_shaped_payloads` round-0 patterns with `presentation_order` 0/1 and one `actionability_hint_sha256`; assert bundle still verifies; add a reduced-bundle variant proving absence still passes.
- `tests/test_capture_manifest.py`: extend planned-stub assertions for new fields; add a drift test for `presentation_order` and a drift test for `actionability_hint_sha256`.

Docs:
- `docs/architecture/schema_changelog.md`: P98 entry under proposer-context manifest notes.
- `docs/operations/benchmark_reproduction_readiness.md`: extend `proposer_context_manifest` row description to mention optional `presentation_order` and `actionability_hint_sha256`.
- `docs/operations/benchmark_reproduction_requirements.json`: update `proposer_context_ingredients` notes.
- `docs/architecture/productionization_brief.md`: append P98 entry.

Non-goals:
- No closed actionability vocabulary.
- No requirement that every paper-faithful bundle disclose ordering evidence (remains optional).
- No change to candidate-distinctness invariants (P93) — `presentation_order` is independent of mechanism/surface signatures.
- No new artifact class, audit/corpus schema bump, canonical readiness hash rotation, or reproduction-claim change.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.

Stop conditions:
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-extract-check`, `make reproduction-bundle-check` all green.
- `tests/fixtures/canonical_audit_hash.txt` unchanged.
- Release-candidate evidence fixture hash unchanged (default release path does not touch proposer context).
- Schema changelog entry present.

## Remaining Open Questions

Blocking (resolve in round 2):
1. Field-shape decision (a) vs (b) above.
2. Whether `proposer-context-evidence-derivation` must enforce `support_rank` (derived from `size`) as the primary sort and treat `presentation_order` as operator-declared override evidence — recommend yes, document the boundary.

Non-blocking advisory:
3. Whether a future slice should bind `presentation_order` to the proposer LLM request log's per-round prompt ordering — out of scope for P98 because request-log payloads are opaque hashes, not prompt text.

[usage] {"completion_tokens": 2605, "completion_tokens_details": {"reasoning_tokens": 816}, "prompt_tokens": 181496, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 184101}

---

# Round 2

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

[usage] {"completion_tokens": 2066, "completion_tokens_details": {"reasoning_tokens": 90}, "prompt_tokens": 185099, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 187165}
