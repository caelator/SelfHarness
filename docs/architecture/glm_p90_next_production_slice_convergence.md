# Round 1

CONVERGED: NO

## Verdict

The most valuable next locally-implementable slice is **P90: content digest binding for proposal-validation task_outcomes in capture-manifest diff**. P89 added optional `task_outcomes` and checks only presence counts in the plan-vs-realized rehearsal diff. A content digest closes the gap where planned and realized task_outcomes have matching presence but different pass/fail content, without contradicting the paper's aggregate pass-count acceptance rule (Section 3.4) or introducing a new artifact class, raw trace binding, or semantic parsing. The initial plan below is sound but needs one tightening before execution: the digest must normalize `attempt_index` absence and be explicitly documented as a rehearsal-only drift signal, not a bundle-verification acceptance check.

## Critique

- **Evidence (repo):** `capture_manifest_diff._proposal_validation_round_summary` records `task_outcomes_present_count` only. Two candidates with the same presence but different `{task_id, split, pass}` content produce identical summaries, so the diff cannot detect content drift in rehearsal.
- **Evidence (repo):** `reproduction_bundle._cross_artifact_proposal_validation_binding` already compares baseline task outcomes against proposer context at the bundle level, but capture-manifest diffing is a separate rehearsal gate with no content binding.
- **Evidence (repo):** `capture_manifest_build._planned_task_outcomes` generates synthetic planned task_outcomes. These are deterministic, so a content digest is computable and stable across runs.
- **Inference (paper):** Section 3.4 requires each candidate to record "split-wise outcomes." P89 disclosed task-level outcomes; P90 makes rehearsal diffing content-aware without changing the aggregate acceptance rule. The paper's stochastic repeat handling uses aggregate pass counts for promotion, not for evidence binding—so a content digest in the rehearsal diff does not conflict.
- **Risk (low):** The digest is additive metadata on an existing finding (`proposal-validation-derivation`). No schema bump, no new artifact class, no readiness hash rotation on canonical audit path. Capture-rehearsal fixture hash rotates because the finding metadata changes; this is expected and contained.
- **Risk (medium, mitigated):** If the digest includes `attempt_index` and some task_outcomes rows omit it while others include it, the digest could be ambiguous. The digest must normalize: treat absent `attempt_index` as a sentinel (e.g., `None`) and include it in the sorted tuple key, so `(task_id, split, pass, None)` is distinct from `(task_id, split, pass, 0)`.

## Required Changes

1. The content digest must cover the full closed shape `{task_id, split, pass, attempt_index}` with deterministic sorting, not just `{task_id, pass}`.
2. The digest must be computed per candidate and per baseline split_outcomes block, not aggregated across the whole round.
3. The diff finding must report per-candidate digest drift with `expected` and `actual` digests, mirroring the existing `task_outcomes_present_count` drift structure.
4. Document explicitly that this is a rehearsal-only content-drift signal; bundle verification remains the authoritative cross-artifact acceptance check. The digest does not replace or override the aggregate pass-count promotion criterion.
5. Non-goals (confirmed): no per-task candidate-vs-baseline acceptance rule (deferred, risks false positives), no raw trace binding, no separate baseline artifact class, no semantic rejection-reason parsing, no schema bump, no live service contact, no reproduction-claim change.

## Revised Plan

**P90 — proposal-validation task_outcome content digest binding in capture-manifest diff**

Files:
- `src/self_harness/capture_manifest_diff.py`
  - Add a helper `_task_outcomes_digest(rows: Sequence[Mapping[str, object]]) -> str` that sorts rows by `(task_id, split, attempt_index_or_sentinel, pass)` and computes `sha256(stable_json_dumps({"outcomes": sorted_tuples}) + "\n")`.
  - Extend `_proposal_validation_round_summary` to compute `baseline_task_outcomes_digest` (when `task_outcomes` present, else `None`) and `candidate_task_outcomes_digests` keyed by `proposal_id`.
  - Extend the per-round drift comparison in `_proposal_validation_findings` to compare these digests and record drift under the existing `proposal-validation-derivation` finding with `baseline_task_outcome_digest_drift` and `candidate_task_outcome_digest_drifts` metadata.
- `tests/test_capture_manifest.py`
  - Add a test where realized `task_outcomes` content differs from planned (same presence count, different pass value for one task); assert `proposal-validation-derivation` fails with digest drift.
  - Add a test where planned and realized task_outcomes match exactly; assert no digest drift.
  - Add a test where planned has task_outcomes but realized omits them; assert only presence-count drift fires (digest comparison skipped when realized is absent).
- `docs/architecture/productionization_brief.md`
  - Append P90 entry using the P84–P89 template, citing P89 and paper Section 3.4, noting that content digest binding is rehearsal-only, schema-unchanged, and does not alter the aggregate acceptance rule.
- `docs/operations/benchmark_reproduction_readiness.md`
  - Update the `proposal_validation_manifest` row to mention task_outcome content digest binding in capture-manifest diff.

Non-goals (explicit):
- No `proposal_validation_manifest.schema_version` bump.
- No per-task candidate-vs-baseline acceptance rule in bundle verification.
- No raw trace binding.
- No separate baseline validation artifact class.
- No semantic rejection-reason parsing.
- No new artifact class, no readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, no reproduction-claim change.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.
- Capture-rehearsal fixture hash rotates because the diff finding metadata gains digest fields; canonical paper-fidelity audit hash is unchanged.

## Remaining Open Questions

- Should the digest eventually cover `terminal_cause` or `failure_category` if those fields are added to task_outcomes in a future slice? **Non-blocking for P90**: the current closed shape is `{task_id, split, pass, attempt_index}`; adding fields later would require a digest version marker, which can be added without schema bump.
- Should bundle verification also compute a content digest for cross-artifact consistency? **Non-blocking for P90**: bundle verification already compares baseline task outcomes against proposer context at the task-id level; adding a digest there is redundant for the current evidence shape and can be revisited if task_outcomes grows additional fields.
- Should the digest be exposed in the reproduction bundle report metadata for operator triage? **Non-blocking for P90**: the digest is a rehearsal diff signal; bundle verification metadata already carries the authoritative task-level checks.

[usage] {"completion_tokens": 2583, "completion_tokens_details": {"reasoning_tokens": 1115}, "prompt_tokens": 146924, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 149507}

---

# Round 2

CONVERGED: YES

## Verdict

P90 (content digest binding for proposal-validation task_outcomes in capture-manifest diff) is ready to execute. All round-1 required changes have been incorporated: the digest covers the full closed shape including `attempt_index` normalization, is computed per-candidate and per-baseline, reports per-candidate digest drift with expected/actual structure, and is explicitly scoped as a rehearsal-only content-drift signal that does not override the paper's aggregate pass-count acceptance rule (Section 3.4) or bundle-verification acceptance checks. No blocking decisions remain.

## Critique

- **Evidence (repo):** `_proposal_validation_round_summary` in `capture_manifest_diff.py` only records `task_outcomes_present_count`; two candidates with matching presence but different `(task_id, split, pass, attempt_index)` content are indistinguishable in rehearsal.
- **Evidence (repo):** `_cross_artifact_proposal_validation_binding` in `reproduction_bundle.py` already compares baseline task outcomes against proposer context at bundle level, but capture-manifest diff is a separate rehearsal gate with no content binding today.
- **Evidence (repo):** `_planned_task_outcomes` in `capture_manifest_build.py` is deterministic, so a content digest is computable and stable across runs for the planned artifact side.
- **Inference (paper):** Section 3.4 requires "split-wise outcomes" recorded per candidate; P89 made those outcomes optional and visible, and P90 makes rehearsal diffing content-aware without changing the aggregate acceptance rule.
- **Risk (low):** Additive metadata on the existing `proposal-validation-derivation` finding. No schema bump, no new artifact class, no canonical audit/readiness hash rotation. Capture-rehearsal fixture hash rotates because finding metadata changes; this is expected and contained.
- **Risk (medium, mitigated):** `attempt_index` absence is normalized via a sentinel tuple key so `(task_id, split, pass, None)` is distinct from `(task_id, split, pass, 0)`.

## Required Changes

All round-1 required changes remain incorporated and valid:

1. Digest covers full closed shape `{task_id, split, pass, attempt_index}` with deterministic sort — confirmed.
2. Digest computed per candidate and per baseline `split_outcomes` block — confirmed.
3. Per-candidate digest drift reported with `expected`/`actual` structure under existing finding — confirmed.
4. Documentation marks this as rehearsal-only drift signal; bundle verification remains authoritative — confirmed.
5. Non-goals confirmed: no schema bump, no per-task acceptance rule, no raw trace binding, no separate baseline artifact class, no semantic rejection parsing, no live service contact, no reproduction claim change — confirmed.

## Revised Plan

**P90 — proposal-validation task_outcome content digest binding in capture-manifest diff**

Files:
- `src/self_harness/capture_manifest_diff.py`
  - Add `_task_outcomes_digest(rows)` helper: sort rows by `(task_id, split, attempt_index_or_sentinel, pass)` and compute `sha256(stable_json_dumps({"outcomes": sorted_tuples}) + "\n")`.
  - Extend `_proposal_validation_round_summary` to compute `baseline_task_outcomes_digest` (when present, else `None`) and `candidate_task_outcomes_digests` keyed by `proposal_id`.
  - Extend `_proposal_validation_findings` to compare digests per round, recording drift under existing `proposal-validation-derivation` finding with `baseline_task_outcome_digest_drift` and `candidate_task_outcome_digest_drifts` metadata.
- `tests/test_capture_manifest.py`
  - Test realized `task_outcomes` content differs from planned (same presence count, different pass value); assert `proposal-validation-derivation` fails with digest drift.
  - Test planned and realized task_outcomes match exactly; assert no digest drift.
  - Test planned has task_outcomes but realized omits them; assert only presence-count drift fires (digest comparison skipped when realized is absent).
- `docs/architecture/productionization_brief.md`
  - Append P90 entry using P84–P89 template, citing P89 and paper Section 3.4; note content digest binding is rehearsal-only, schema-unchanged, and does not alter aggregate acceptance rule.
- `docs/operations/benchmark_reproduction_readiness.md`
  - Update `proposal_validation_manifest` row to mention task_outcome content digest binding in capture-manifest diff.

Non-goals (explicit):
- No `proposal_validation_manifest.schema_version` bump.
- No per-task candidate-vs-baseline acceptance rule in bundle verification.
- No raw trace binding.
- No separate baseline validation artifact class.
- No semantic rejection-reason parsing.
- No new artifact class, no readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, no reproduction-claim change.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.
- Capture-rehearsal fixture hash rotates because diff finding metadata gains digest fields; canonical paper-fidelity audit hash unchanged.

## Remaining Open Questions

All confirmed non-blocking:
- Whether the digest eventually covers `terminal_cause`/`failure_category` for future task_outcomes fields — defer with a digest version marker when needed.
- Whether bundle verification should compute a parallel content digest for cross-artifact consistency — redundant given existing task-id-level checks; revisit if task_outcomes grows additional fields.
- Whether digest should surface in bundle report metadata for operator triage — bundle verification metadata already carries authoritative task-level checks; P90 is a rehearsal-only signal.

CONVERGED: YES

[usage] {"completion_tokens": 1534, "completion_tokens_details": {"reasoning_tokens": 344}, "prompt_tokens": 149889, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 151423}
