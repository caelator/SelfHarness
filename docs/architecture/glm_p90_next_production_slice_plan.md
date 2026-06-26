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
