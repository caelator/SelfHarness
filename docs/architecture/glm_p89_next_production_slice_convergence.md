# Round 1

CONVERGED: NO

## Verdict

The next most valuable locally-implementable slice is **P89: per-candidate task outcome disclosure in proposal validation**. The paper (Section 3.4) requires recording "split-wise outcomes" for each evaluated candidate; the current `proposal_validation_manifest` only carries aggregate pass counts per split. Per-task outcomes would (a) make the acceptance rule verifiable at task granularity rather than count granularity, (b) enable cross-checking that proposer held-in failure patterns are a subset of baseline failing tasks, and (c) close a real auditability gap. The plan below is the initial draft for round 1; it is not yet converged because the schema-bump decision and the scope of new bundle cross-checks need tightening before execution.

## Critique

- **Evidence (repo):** `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS` in `src/self_harness/_artifact_shapes.py` currently lacks any per-task outcome surface; `split_outcomes` only records `held_in_passed/total` and `held_out_passed/total`.
- **Evidence (repo):** `reproduction_bundle._cross_artifact_proposal_validation_binding` only compares aggregate pass counts against the round baseline; it cannot detect a candidate that matches the baseline pass count but flips which specific tasks passed.
- **Evidence (repo):** `capture_extract._proposal_validation_candidate` derives from audit rows but the toy audit fixture (`_audit_split_total_rows`) only emits `__split_total__` rows, so per-task outcomes are not currently authored anywhere.
- **Inference (paper):** Section 3.4 explicitly says "split-wise outcomes" are recorded per candidate; aggregate counts are a lossy compression. The paper's acceptance rule (improve at least one split, degrade neither) is count-based, but the *auditability* requirement ("making each transition in the harness lineage auditable") implies per-task evidence should be available.
- **Risk:** This is a `proposal_validation_manifest` schema bump (`schema_version` 1.0 → 1.1) unless we add `task_outcomes` as an optional additive field and keep 1.0. Optional-additive-without-bump is the safer path and matches how P87 added `validation_failure_category` without bumping.
- **Risk:** Adding a new `cross_artifact_proposal_validation_task_outcomes` bundle check that compares candidate task outcomes against proposer context held-in failure patterns could be too strict if the toy audit fixture's failing tasks don't line up. The check should be scoped to: when both proposer context and candidate task outcomes are present, the baseline's failing held-in task set must be a superset of the proposer's held-in failure pattern task ids.
- **Scope concern:** Full per-task outcome extraction from real audit directories requires the audit schema to carry per-task verifier rows for candidate arms, which the current `_audit_split_total_rows` fixture does not. The slice should support `task_outcomes` as an optional field populated when available, and the toy fixture should be extended rather than requiring all audit sources to emit it.

## Required Changes

1. Make `task_outcomes` an **optional additive field** on proposal validation candidates; do not bump `proposal_validation_manifest.schema_version` (mirror P87's approach).
2. Scope the new bundle cross-check to baseline-only: when `baseline_split_outcomes.task_outcomes` is present, its failing held-in task set must be a superset of every proposer-context round's held-in failure pattern task ids. Do **not** add a candidate-vs-baseline per-task acceptance rule in P89; that is a larger semantic change and belongs in a follow-up.
3. Extend `capture_extract._proposal_validation_candidate` and `_split_outcomes` to read optional per-task verifier rows (`task_id != "__split_total__"`) from audit evaluations when present, and omit `task_outcomes` when absent.
4. Extend the toy audit fixture in `tests/test_capture_extract.py::_write_audit_run` to emit two per-task candidate-arm rows per round so the happy path exercises the new field; keep the existing `__split_total__` rows so aggregate counts still reconcile.
5. Extend `capture_manifest_diff._proposal_validation_round_summary` to count candidates with non-empty `task_outcomes` and emit drift when planned-vs-realized presence differs. Do **not** compare per-task outcome contents in the diff (that belongs to bundle verification); only compare presence counts.
6. Do **not** add semantic rejection-reason parsing, raw trace binding, or a separate baseline artifact class in P89. Those remain explicit non-goals.

## Revised Plan

**P89 — per-candidate task outcome disclosure in proposal validation**

Files:
- `src/self_harness/_artifact_shapes.py`
  - Add `"task_outcomes"` to `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS`.
  - In `_proposal_validation_candidate`: when `task_outcomes` is present, require it to be a list of objects with closed fields `{task_id, split, pass}`; require `task_id` non-empty, `split ∈ {held_in, held_out}`, `pass` boolean; require no duplicate `(task_id, split)` pairs; require the multiset of `pass` values per split to reconcile with `split_outcomes.held_in_passed` / `held_out_passed`.
- `src/self_harness/capture_extract.py`
  - In `_split_outcomes` / `_proposal_validation_candidate`: when the audit round has per-task verifier rows for the given `proposal_id`/`arm`, populate `task_outcomes`; otherwise omit the field.
- `src/self_harness/reproduction_bundle.py`
  - Extend `_cross_artifact_proposal_validation_binding`: when `baseline_split_outcomes.task_outcomes` is present and a `proposer_context_manifest` artifact is bundled, compute the baseline's held-in failing task set and require it to be a superset of each proposer-context round's held-in failure pattern task ids. Record `baseline_task_outcome_violations` in metadata. Skip silently when either side is absent.
- `src/self_harness/capture_manifest_diff.py`
  - Extend `_proposal_validation_round_summary` to add `task_outcomes_present_count`; extend `_proposal_validation_findings` to compare it and report drift under the existing `proposal-validation-derivation` finding.
- `tests/test_capture_extract.py`
  - Extend `_audit_split_total_rows` / `_write_audit_run` to emit two candidate-arm per-task rows per round (one held-in pass, one held-in fail) so `task_outcomes` is populated.
  - Add a test asserting the extracted candidate carries `task_outcomes` reconciling with aggregate counts.
  - Add a test asserting that removing the per-task rows causes `task_outcomes` to be omitted (not an error).
- `tests/test_reproduction_readiness.py`
  - Extend `_proposal_validation_candidate` to populate `task_outcomes` consistent with `split_outcomes`.
  - Add a test where proposer context references a held-in failing task that the baseline `task_outcomes` marks as passing; assert `cross_artifact_proposal_validation_binding` fails with `baseline_task_outcome_violations`.
- `tests/test_capture_manifest.py`
  - Add a test asserting `proposal-validation-derivation` reports `task_outcomes_present_count` drift when planned stubs include `task_outcomes` but the realized bundle omits them.
- `docs/architecture/productionization_brief.md`
  - Append P89 entry using the P84–P88 template, citing paper Section 3.4 and noting that per-task outcomes are optional-additive, schema 1.0 is unchanged, and P89 does not introduce a per-task acceptance rule.
- `docs/operations/benchmark_reproduction_readiness.md`
  - Update the `proposal_validation_manifest` row to mention optional `task_outcomes` and the new baseline-superset-of-proposer-failures cross-check.

Non-goals (explicit):
- No `proposal_validation_manifest.schema_version` bump.
- No per-task candidate-vs-baseline acceptance rule; only baseline-vs-proposer-context superset check.
- No raw trace binding.
- No separate baseline validation artifact class.
- No semantic rejection-reason parsing.
- No new artifact class, no readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, no reproduction-claim change.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.
- Release-candidate evidence fixture hash rotates because the proposal-validation fixture gains `task_outcomes`; canonical paper-fidelity audit hash is unchanged because engine default audit output is unchanged.

## Remaining Open Questions

- Should `task_outcomes` eventually carry `attempt_index` to support the paper's two-repeat evaluation explicitly? **Non-blocking for P89**: the closed shape `{task_id, split, pass}` is the minimal auditable surface; attempt-indexed outcomes can be added later without bumping because the field is a list.
- Should the baseline-superset-of-proposer-failures check also apply to candidate `task_outcomes` (i.e., a candidate that fixes a failure the proposer never observed)? **Non-blocking for P89**: that is a stricter semantic check and risks false positives in the toy fixture; defer to a follow-up.
- Should `capture_manifest_diff` eventually compare per-task outcome *contents* rather than just presence counts? **Non-blocking for P89**: presence-count drift is the minimum needed to keep rehearsal honest; content comparison belongs to bundle verification where cross-artifact context is available.

[usage] {"completion_tokens": 3207, "completion_tokens_details": {"reasoning_tokens": 1195}, "prompt_tokens": 134980, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 138187}

---

# Round 2

CONVERGED: YES

## Verdict

P89 (per-candidate task outcome disclosure) is ready to execute. The slice is well-scoped, additive without schema bump, closes a genuine paper-fidelity auditability gap (Section 3.4 "split-wise outcomes" recorded per candidate), and avoids overreach into per-task acceptance rules or raw trace binding. All material risks identified in round 1 are addressed in the revised plan.

## Critique

- **Evidence (repo):** `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS` in `src/self_harness/_artifact_shapes.py` lacks any per-task surface; `_split_outcomes` only records aggregate counts. The bundle check `_cross_artifact_proposal_validation_binding` compares aggregate counts only.
- **Evidence (repo):** `_audit_split_total_rows` in `tests/test_capture_extract.py` only emits `__split_total__` rows; per-task verifier rows are not authored anywhere in fixtures.
- **Inference (paper):** Section 3.4 says "split-wise outcomes" are recorded per candidate and each transition must be auditable; aggregate counts are a lossy compression that prevents verifying which tasks actually flipped.
- **Architecture risk (low):** Optional additive `task_outcomes` field with closed shape `{task_id, split, pass}` mirrors P87's `validation_failure_category` pattern. No schema bump, no new artifact class, no readiness hash rotation on the canonical audit path. Capture-rehearsal fixture hash and any downstream release-candidate evidence fixture hash will rotate because the fixture gains the new field; this is expected and contained.
- **Bundle check scope (correct):** Baseline-superset-of-proposer-failures is the right conservative check. Candidate-vs-baseline per-task comparison is deliberately deferred as a larger semantic change.

## Required Changes

All round-1 required changes are incorporated and remain valid:
1. Optional additive `task_outcomes` field, no schema bump — confirmed.
2. Bundle check scoped to baseline-vs-proposer-context superset only — confirmed.
3. `capture_extract` reads optional per-task verifier rows, omits field when absent — confirmed.
4. Toy audit fixture extended with per-task candidate rows — confirmed.
5. `capture_manifest_diff` compares presence counts only, not contents — confirmed.
6. Explicit non-goals documented (no semantic rejection parsing, no raw trace binding, no baseline artifact class) — confirmed.

## Revised Plan

Plan from round 1 is final. No further revisions needed. Key points:

- **Files:** `_artifact_shapes.py` (add field + validation), `capture_extract.py` (populate from optional rows), `reproduction_bundle.py` (new `baseline_task_outcome_violations` check), `capture_manifest_diff.py` (presence-count drift), 3 test files, 2 docs files.
- **Non-goals:** No schema bump, no per-task acceptance rule, no raw trace binding, no new artifact class, no live service contact, no reproduction claim change.
- **Verification gates:** `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.

## Remaining Open Questions

All three open questions from round 1 are confirmed non-blocking:
- `attempt_index` support — deferred, list field allows future addition without bump.
- Candidate-vs-baseline per-task check — deferred to follow-up, risks false positives.
- `capture_manifest_diff` content comparison — deferred to bundle verification where cross-artifact context exists.

No blocking decisions remain. The plan is executable.

[usage] {"completion_tokens": 888, "completion_tokens_details": {"reasoning_tokens": 142}, "prompt_tokens": 139029, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 139917}
