# Round 1

CONVERGED: NO

## Verdict

The candidate gap is real and material: P80 proves that proposer context manifest carries non-empty held-in-failure and passing-behavior blocks with hashes/counts, but it does **not** prove those hashes bind to actual held-in evidence inside `live_harbor_audit` / `live_two_repeat_evaluation_report` / `live_terminal_bench_split_manifest`. An operator (or a malicious capture run) could put any 64-hex string in `mechanism_sha256` or `task_id_set_sha256` and still pass `cross_artifact_proposer_context_binding`. This is the next locally implementable fidelity slice because it closes an evidence-derivation hole using only offline cross-artifact math — no Harbor/Docker/model/PyPI/Sigstore access required. Round 1 produces a workable plan but it is not yet execution-ready: two design decisions must be locked before tests/rotations can be written deterministically.

## Critique

Strengths of pursuing this slice next:
- Strictly offline, additive invariant work, consistent with the P66-P80 cadence.
- Closes the most obvious "hashes exist but are unbound" fidelity gap that a reviewer of the bundle would flag.
- Reuses existing reader helpers (`primary_capture_run_ids`, `read_artifact_payload`, `_object_list`).
- Does not require rotating canonical audit hashes because proposer context manifests remain opt-in raw live evidence.

Weaknesses / unknowns that block convergence on round 1:
1. **Binding strength decision.** Two viable modes: (a) *strict derivation* — recompute the expected hash from bundled live evidence and require equality; (b) *coverage binding* — require the referenced task-id set to be a subset of held-in failures/passes and require cluster sizes to reconcile, without re-computing the mechanism hash. Strict derivation is more faithful to the paper ("derived from verifier-grounded failures") but requires picking the canonical serialization the engine used when it stamped the manifest. Without seeing the engine's serialization code I cannot confirm the exact bytes.
2. **Mechanism-hash material.** The paper's `mechanism_sha256` is "the inferred agent mechanism" (Section 3.2). It is unclear whether the production engine derives it from trace text, from `(terminal_cause, failure_category)` pairs, or from a proposer-side free-form text block. If it is free-form proposer text, then strict re-derivation is impossible and only coverage binding is viable.
3. **Failure pattern ↔ audit task-id alignment.** Audit rows in `live_harbor_audit` carry `verifier_outcome` and `attempts[].pass`, but do **not** carry a `failure_category` per attempt (only `terminal_cause` optionally). If the mechanism hash must bind to failure categories, the audit shape may need a new optional field — which is a schema decision, not just an invariant.

## Required Changes

To converge in round 2, the revised plan must:
1. Pick a binding mode and justify it against the engine's actual serialization path (or document why coverage binding is the maximum offline-verifiable contract).
2. Decide whether `live_harbor_audit.trial_artifacts[].attempts[]` should gain an optional `failure_category` field (would be a schema `1.4` → `1.5` minor bump or a new optional field under existing 1.4) so mechanism hashes can bind to verifier-grounded categories.
3. Specify exact byte-serialization for any strict hashes (e.g. `stable_json_dumps(...) + "\n"`), so fixture rotations are deterministic.
4. Define the exact failure-pattern cluster reconciliation rule: cluster `size` must equal the number of held-in failing task ids whose verifier outcome maps to that cluster's mechanism, and the union of all cluster task-id sets must equal the held-in failing set (no unattributed failures, no attributed non-failures).
5. Define the exact passing-summary reconciliation rule: `task_id_set_sha256 = sha256(stable_json_dumps(sorted(held_in_passing_task_ids)) + "\n")`, and `preserved_behavior_sha256` either binds to a deterministic summary of the audit rows for those tasks or is left as an opaque proposer-attested hash with a weaker invariant.

## Revised Plan

**Slice name (proposed):** P81 — Proposer Context Evidence Derivation Binding.

**Goal:** Add `cross_artifact_proposer_context_evidence_binding` to `reproduction_bundle._cross_artifact_invariants`, proving that held-in failure patterns and passing behavior summaries in `proposer_context_manifest` are derived from the bundled live audit/evaluation/split evidence rather than free-floating hashes.

**Files to modify (proposed):**
- `src/self_harness/_artifact_shapes.py` — extend `proposer_context_manifest` round shape:
  - `held_in_failure_patterns.patterns[]` gains required `task_ids: list[str]` and optional `failure_category: str` (closed enum from P4 taxonomy).
  - `passing_behavior_summaries.summaries[]` gains required `task_ids: list[str]`.
  - Keep existing `mechanism_sha256` / `preserved_behavior_sha256` for backwards-compat / proposer attestation.
- `src/self_harness/reproduction_bundle.py` — add `_cross_artifact_proposer_context_evidence_binding(bundle, context_entry, audit_entry, evaluation_entry, split_entry)`:
  - Returns `None` if any of context/audit/evaluation/split is absent (preserves reduced-bundle skip semantics).
  - Validates held-in pattern `task_ids` ⊆ held-in failing audit task set; rejects any pattern whose task_ids include a held-out task, a passing task, or an unknown id.
  - Validates union of pattern task_ids == held-in failing set (no silent drops).
  - Validates pattern `size` == `len(task_ids)`.
  - Validates `passing_behavior_summaries[].task_ids` ⊆ held-in passing audit task set, and union == held-in passing set.
  - If `failure_category` is present on patterns, validates it against audit `attempts[].terminal_cause`/category mapping when audit carries that field; else skip with advisory metadata.
- `src/self_harness/capture_extract.py` — extend `extract_proposer_context_manifest` to copy `task_ids` (and optional `failure_category`) from raw context log rows; reject rows that claim task ids absent from the supplied split/audit when those are also passed in.
- `docs/operations/benchmark_reproduction_requirements.json` — update `proposer_context_ingredients` notes to reflect the new binding.
- `docs/architecture/schema_changelog.md` — add P81 entry.
- `tests/test_reproduction_readiness.py` — add tests for:
  - clean binding passes,
  - pattern task id outside held-in fails,
  - pattern size drift fails,
  - pattern union ≠ held-in failures fails,
  - passing summary task id outside held-in passes fails,
  - audit/eval/split absent → check skipped,
  - deterministic fixture hash rotation for paper-faithful bundles.

**Invariants:**
- `reproduction_claimed == false` everywhere.
- No live contact, no readiness hash rotation, no canonical audit hash rotation.
- Skip semantics preserved when any of {context, audit, evaluation, split} is absent.
- Closed-set validation for `failure_category` if introduced.

**Fixture rotations (expected):**
- `tests/fixtures/release_candidate/reproduction_readiness_result.json`
- `tests/fixtures/release_candidate/expected_hash.txt`
- Capture-rehearsal and capture-manifest paper-faithful fixture hashes (because planned context stubs gain required fields).

**Stop conditions:**
- New invariant green on the class-shaped fixture.
- At least one positive and one negative test per reconciled set (failures, passes).
- No new live dependency.
- Reproduction readiness remains `false` for the default bundle path until operators supply signed live bundles; default release path unchanged.

**Open decisions to resolve before round 2:**
1. Strict hash re-derivation vs coverage-only binding.
2. Whether to add `failure_category` to audit attempts or leave mechanism hashes as opaque proposer attestations.

## Remaining Open Questions

1. Does the existing proposer engine already stamp `mechanism_sha256` from a deterministic source (trace text, category tuple, proposer rationale)? If yes, share the exact serialization so strict binding is possible; if no, lock coverage-only binding.
2. Should `previous_attempted_edits` also gain task-id binding (e.g. to show which failures the accepted edit targeted), or is round+surface+decision enough for paper fidelity? Default proposal: leave as-is for P81; defer to a later slice if reviewers ask.
3. Is the closed `failure_category` enum from P4 the right vocabulary for `mechanism_sha256` binding, or does the paper use a finer-grained mechanism taxonomy that would require a new enum?
4. Do we need a parallel `proposer_context_evidence_diff` finding in `capture_manifest_diff` (plan-vs-realized), or is bundle-side verification sufficient for this round? Default proposal: defer the diff finding to P82 to keep P81 focused.

[usage] {"completion_tokens": 2410, "completion_tokens_details": {"reasoning_tokens": 467}, "prompt_tokens": 87436, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 89846}

---

# Round 2

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

[usage] {"completion_tokens": 2017, "completion_tokens_details": {"reasoning_tokens": 379}, "prompt_tokens": 91349, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 93366}
