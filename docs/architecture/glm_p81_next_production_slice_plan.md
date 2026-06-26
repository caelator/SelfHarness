CONVERGED: YES

## Verdict

The two blocking decisions from round 1 can now be resolved from the paper text and existing artifact shapes, yielding an execution-ready P81 slice. Strict hash re-derivation is impossible for `mechanism_sha256` and `preserved_behavior_sha256` because the paper's failure signature `ϕ(r)=(c,q,m)` includes `m`, "the abstract agent mechanism exposed by the trace" (Section 3.2), which is proposer-inferred free-form text rather than a deterministic function of bundled evidence. The maximum offline-verifiable contract is therefore *coverage binding* plus *strict re-derivation only where the canonical serialization is fixed by the bundle itself*. This decision is forced by the paper, not by missing implementation context, so the slice is ready to execute.

## Critique

Strengths of the converged design:
- Honors the paper's separation between verifier-grounded evidence (`c` = terminal cause) and proposer-inferred mechanism (`m`). Only the verifier-grounded half can be cross-bound offline; the mechanism half remains a proposer attestation by construction.
- Reuses the existing `live_harbor_audit` `attempts[].pass` and `live_two_repeat_evaluation_report.per_task_attempts[].attempts[].pass` fields as the held-in failure/passing task-id source of truth, so no audit schema bump is required.
- Keeps `mechanism_sha256` / `preserved_behavior_sha256` as opaque proposer attestations with a weaker coverage invariant, avoiding a premature and unverifiable "strict derivation" claim.
- Strictly re-derives `passing_behavior_summaries[].task_id_set_sha256` from `sha256(stable_json_dumps(sorted(held_in_passing_task_ids)) + "\n")` because that serialization is fully determined by bundled evidence. This is the one place strict binding is both possible and faithful.

Weaknesses accepted:
- Cannot prove `mechanism_sha256` binds to the proposer's actual mechanism text without storing raw proposer prompts (which the bundle deliberately omits per P78). This is an inherent limit of the compact-evidence design, not a P81 gap.
- Does not add `failure_category` to audit attempts. Rationale: the paper's `c` (terminal verifier cause) is already carried as `terminal_cause`, and the closed `failure_category` enum from P4 is a local-subprocess-adapter concept, not a paper-defined taxonomy. Adding it to the live Harbor audit schema would be an inference, not a derivation, so it is deferred.

## Required Changes

1. `proposer_context_manifest` round shape gains *required* `task_ids: list[str]` on each `held_in_failure_patterns.patterns[]` and `passing_behavior_summaries.summaries[]`. Existing `mechanism_sha256`, `task_id_set_sha256`, and `preserved_behavior_sha256` fields remain for backwards-compat / proposer attestation.
2. New bundle invariant `cross_artifact_proposer_context_evidence_binding`:
   - Skip (return `None`) when any of `proposer_context_manifest`, `live_two_repeat_evaluation_report`, `live_harbor_audit`, `live_terminal_bench_split_manifest` is absent.
   - Compute `held_in_failing_task_ids` = {task_id ∈ held-in split : any attempt failed in the two-repeat report}. Compute `held_in_passing_task_ids` = {task_id ∈ held-in split : all attempts passed}.
   - For each failure pattern: require `task_ids` ⊆ `held_in_failing_task_ids`; require `size == len(task_ids)`; require every listed id is in the held-in split (rejects held-out, passing, or unknown ids).
   - Require `∪ patterns[].task_ids == held_in_failing_task_ids` (no silent drops, no attributed non-failures).
   - For each passing summary: require `task_ids` ⊆ `held_in_passing_task_ids`; require `∪ summaries[].task_ids == held_in_passing_task_ids`.
   - Strict re-derivation: for each passing summary, require `task_id_set_sha256 == sha256(stable_json_dumps(sorted(summary.task_ids)) + "\n")`.
   - Advisory metadata only: record `mechanism_sha256` and `preserved_behavior_sha256` as present/absent; do not fail on them.
3. `capture_extract.extract_proposer_context_manifest` copies `task_ids` from raw context log rows into the normalized artifact; reject rows whose `task_ids` reference unknown task ids when a split manifest is also supplied via a new optional `--split-manifest-result` flag.
4. `docs/operations/benchmark_reproduction_requirements.json` `proposer_context_ingredients` notes updated to describe the new coverage + strict-passing-hash binding.
5. `docs/architecture/schema_changelog.md` P81 entry.
6. Tests in `tests/test_reproduction_readiness.py`: clean pass; pattern task id outside held-in fails; pattern size drift fails; pattern union ≠ held-in failures fails; passing summary task id outside held-in passes fails; passing summary union drift fails; strict `task_id_set_sha256` mismatch fails; absent dependent artifact → check skipped; `mechanism_sha256`/`preserved_behavior_sha256` left opaque does not fail.
7. Fixture rotations: `tests/fixtures/release_candidate/reproduction_readiness_result.json`, `tests/fixtures/release_candidate/expected_hash.txt`, and any capture-rehearsal / capture-manifest paper-faithful fixture hashes that include the new required `task_ids` field. Canonical audit and canonical LLM audit hashes unchanged.

## Revised Plan

**Slice:** P81 — Proposer Context Evidence Derivation Binding.

**Binding mode (locked):** Coverage binding for failure patterns; coverage binding plus strict hash re-derivation for passing-summary `task_id_set_sha256`; opaque proposer attestation for `mechanism_sha256` and `preserved_behavior_sha256`.

**Serialization (locked):** `sha256((stable_json_dumps({"task_ids": sorted_ids}) + "\n").encode("utf-8")).hexdigest()` for any strictly re-derived hash, matching the existing `stable_json_dumps` convention used across the bundle.

**Invariants preserved:**
- `reproduction_claimed == false` everywhere.
- No live Harbor/Docker/model/PyPI/Sigstore contact.
- No audit schema version bump; `proposer_context_manifest` shape extension is additive under the existing closed-field policy because `task_ids` is added to an already-validated nested object, and the top-level schema version stays `1.0`.
- Skip semantics preserved when dependent artifacts are absent.
- Canonical readiness hash and canonical audit hash unchanged.

**Stop conditions:**
- New invariant green on the class-shaped fixture.
- At least one positive and one negative test per reconciled set (failures, passes) plus one strict-hash negative test.
- No new live dependency.
- Default release path and `reproduction_ready:false` default unchanged.

## Remaining Open Questions

1. Should `previous_attempted_edits` also gain task-id binding (e.g., to show which failures the accepted edit targeted)? **Deferred to P82**; round+surface+decision remains sufficient for paper Section 3.3 fidelity.
2. Do we need a parallel `proposer_context_evidence_diff` finding in `capture_manifest_diff`? **Deferred to P82**; bundle-side verification is sufficient for this round and keeps the slice focused.
3. Is the closed `failure_category` enum from P4 the right vocabulary for a future stricter mechanism binding? Out of scope for P81; would require a paper-defined mechanism taxonomy that does not exist in the current text.

These three questions are non-blocking: each has a defensible default and none gates execution of the P81 plan above.
