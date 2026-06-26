# Round 1

CONVERGED: NO

## Verdict

The hypothesis is correct and well-supported by repository evidence. P95 closed paper-fidelity gaps for the `c` (terminal failure category) and `m` (mechanism) components of the failure signature `phi(r)=(c,q,m)` defined in paper Section 3.2, but the `q` (causal_status) component remains unbound in reproduction evidence. `VerifierOutcome.causal_status` and `FailureSignature.causal_status` already exist in `src/self_harness/types.py` and are already used by `src/self_harness/mining.py::signature_of` for in-process clustering, so the gap is purely in the reproduction-evidence contract, not the engine semantics. This is a legitimate, small, additive, locally implementable P96 slice.

## Critique

**Supporting evidence (validated against repo):**
- Paper Section 3.2: "We write this attribution as a failure signature ϕ(r_i) = (c_i, q_i, m_i)" and "two failed cases are grouped together only when they agree on what the verifier ultimately rejected, how the agent behavior contributed to that rejection, and which reusable behavioral mechanism was involved."
- `src/self_harness/types.py`: `VerifierOutcome.causal_status: str` and `FailureSignature.causal_status: str` are first-class fields, and `FailureSignature.key` joins all three components with `|`.
- `src/self_harness/mining.py::signature_of`: already constructs `FailureSignature` from `run.outcome.causal_status`, so the engine already treats `q` as a clustering key.
- `src/self_harness/_artifact_shapes.py::_HELD_IN_FAILURE_PATTERN_FIELDS`: contains `cluster_id`, `size`, `task_ids`, `mechanism_sha256`, `failure_category` — but **no causal_status field**. Same for `_PROPOSAL_VALIDATION_TASK_OUTCOME_FIELDS`.
- P95 changelog entry explicitly added `failure_category` to `proposer_context_manifest` patterns and `proposal_validation_manifest` task outcomes, leaving `q` as the obvious next paper-faithful slot.

**Risks / open design questions:**
1. **Vocabulary boundary.** `c` and `m` were bound differently: `c` reuses the closed `FailureCategory` enum (P87/P95), `m` is an opaque `mechanism_sha256`. The paper describes `q` as "the causal status of that behavior within the trace" — an attribution, not a closed vocabulary. The codebase has no closed causal_status vocabulary. Treating `q` as an opaque hash (like `m`) is the conservative, schema-additive choice; introducing a closed vocabulary now would be inference beyond repo evidence.
2. **Cluster-level vs task-level disclosure.** By paper definition, all tasks in a cluster share the same `(c,q,m)` triple. The P95 pattern already exposes `failure_category` at cluster level and per-task; `mechanism_sha256` is only at cluster level. For `q`, mirroring `m` (cluster-level only) is the minimal paper-faithful move; mirroring `c` (both levels) is more verbose but enables per-task drift detection. Round 2 should pick one.
3. **Capture-extract source.** `proposer_context_manifest` patterns currently derive `mechanism_sha256` from `pattern_id`. There is no existing source for `causal_status` in the audit row contract used by `extract_proposal_validation_manifest` and the proposer-context log rows. Round 2 must decide whether to (a) extend the raw proposer-context JSONL row contract with `causal_status`, (b) derive `q` from audit VerifierOutcome when baseline task outcomes are present, or (c) both.
4. **Fixture/hash rotation scope.** P95 rotated capture-manifest, capture-manifest diff, and rehearsal hashes but not canonical audit/LLM-audit hashes. P96 will likely rotate the same set plus the reproduction-readiness fixture if `proposer_context_manifest` shape changes. Must confirm in round 2.

**Insufficient evidence (would decide the open questions):**
- Whether any captured live proposer-context log format in the wild already records `causal_status`. None is visible in `tests/fixtures/` or `docs/operations/`. Defaulting to "extend raw row contract" is safe but round 2 should confirm there is no operator format we would break.
- Whether the paper authors intend `q` to be free-form or taxonomic. The paper text and appendix do not constrain this; absent evidence, opaque-hash is the faithful choice.

## Required Changes

Before convergence, round 2 must decide:
1. **Cluster-only vs cluster+task disclosure of `causal_status_sha256`.** Recommend cluster-only to mirror `mechanism_sha256` and minimize schema churn, unless per-task drift detection is deemed worth the per-row field.
2. **Capture-extract source for `q`.** Recommend extending the raw proposer-context JSONL row contract with optional `causal_status` (string), hashed via the same `_stable_payload_sha256({"causal_status": value})` convention used for mechanism, and *also* deriving per-task `causal_status` from audit VerifierOutcome when baseline task outcomes are present (only if decision 1 chooses task-level disclosure).
3. **Whether to add a closed causal_status vocabulary.** Recommend **no** — opaque hash, matching `m`, pending separate evidence.
4. **Confirm hash rotation set** by running the existing fixture generators mentally or in a scratch branch.

## Revised Plan

**P96 — failure-signature causal_status (`q`) binding**

*Invariant:* the `q` component of `phi(r)=(c,q,m)` is bound the same way P95 binds `c` and P78/P80 bind `m`: optional on reduced bundles, machine-checked on paper-faithful bundles, never contacts live services, never claims reproduction.

**Files (additive, backward-compatible):**

1. `src/self_harness/_artifact_shapes.py`
   - Add `causal_status_sha256` to `_HELD_IN_FAILURE_PATTERN_FIELDS` (optional, 64 lowercase hex when present).
   - If decision 1 chooses task-level: add `causal_status_sha256` to `_PROPOSAL_VALIDATION_TASK_OUTCOME_FIELDS` (optional, only on failing rows, must be null/absent on passing rows — same rule as `failure_category`).

2. `src/self_harness/capture_extract.py`
   - `extract_proposer_context_manifest`: when a raw context log row pattern carries `causal_status` (string), compute `causal_status_sha256 = _stable_payload_sha256({"causal_status": value})` and attach to the emitted pattern. Reject malformed (non-string, empty) values; absence remains valid.
   - `extract_proposal_validation_manifest`: if task-level disclosure is chosen, propagate `RunRecord.outcome.causal_status` through `_split_task_outcomes` for failing rows.

3. `src/self_harness/reproduction_bundle.py`
   - In `_cross_artifact_proposer_context_evidence_binding`, add `causal_status_violations` (cluster-level): when a pattern declares `causal_status_sha256`, every same-round baseline failing task it covers must disclose the same `causal_status_sha256`; mixed values fail closed (mirroring P95 `failure_pattern_category_violations`).
   - Mirror P95's "mixed baseline" and "declared-vs-baseline mismatch" failure modes.
   - If task-level disclosure is chosen, add the symmetric per-row check.

4. `src/self_harness/capture_manifest_diff.py`
   - Extend `_proposer_context_failure_category_summary` (or add a parallel `_causal_status_summary`) to compare planned vs realized `causal_status_sha256` per cluster, emitting `causal_status_drifts` inside `proposer-context-evidence-derivation`.
   - If task-level chosen: bump or extend the task-outcome digest to v3 (currently v2 from P95) so `causal_status_sha256` participates in the deterministic digest; otherwise keep v2.

5. `docs/operations/benchmark_reproduction_requirements.json` and `benchmark_reproduction_readiness.md`
   - Add a note under `proposer_context_ingredients` and `proposal_validation_records` that paper-faithful bundles may disclose `causal_status_sha256` and that bundle verification binds it to same-round baseline evidence.

6. `docs/architecture/schema_changelog.md`
   - Add `## P96 Failure-Signature Causal Status Binding` entry following the P95 template.

7. `tests/test_reproduction_readiness.py` and `tests/test_capture_manifest.py`
   - Extend `_class_shaped_payloads` / `_proposer_context_rounds` / `_proposal_validation_rounds` to include `causal_status_sha256` on at least one cluster per round.
   - Add drift tests modeled on `test_reproduction_bundle_rejects_failure_pattern_category_mismatch`, `test_reproduction_bundle_rejects_failure_pattern_mixed_baseline_categories`, and the corresponding capture-manifest diff tests.
   - Add a capture-extract test that raw `causal_status` strings are hashed and propagated, and that malformed values fail closed.
   - Add a backward-compat test: bundles/fixtures without `causal_status_sha256` still verify (reduced-bundle path).

8. Fixture hash rotation
   - `tests/fixtures/release_candidate/expected_hash.txt` (capture-manifest / rehearsal / reproduction-readiness fixtures) — rotate after payload updates.
   - Do **not** rotate `tests/fixtures/canonical_audit_hash.txt` or `tests/fixtures/canonical_llm_audit_hash.txt` — engine default output is unchanged.

**Invariants / stop conditions:**
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.
- `reproduction_claimed:false` everywhere; `reproduction_ready:false` unchanged.
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, and `make capture-rehearsal` all pass.
- Canonical audit and LLM-audit hashes unchanged.
- All schema additions optional; existing reduced bundles remain valid.
- No new artifact class; no new requirement row; no new signing custody path.

**Stop condition for the slice:** all four `phi(r)` components that the codebase already models (`c` via P87/P95, `m` via P78/P80, `q` via P96, plus task identity via P81/P92) are machine-checkable on paper-faithful reproduction bundles, and the only remaining `phi`-related work is live operator capture.

## Remaining Open Questions

1. Cluster-only vs cluster+task `causal_status_sha256` disclosure — pick in round 2. **Recommendation: cluster-only**, mirroring `mechanism_sha256`.
2. Should a future slice introduce a closed causal_status vocabulary, or remain opaque-hash forever? **Recommendation: defer; opaque hash is paper-faithful given current evidence.**
3. Should `_task_id_set_sha256` and the task-outcome digest version bump again if task-level disclosure is chosen? **Recommendation: yes if task-level, no if cluster-only.**
4. Are there any operator-supplied proposer-context log formats in the wild that already encode `causal_status` under a different field name? No evidence in repo; round 2 should grep `tests/fixtures/` and `docs/operations/` once more to be safe.
5. Round 2 should also sanity-check that no P97-P99 candidate (e.g., raw per-candidate trace binding, separate baseline evaluation artifact, free-text rejection-reason parsing) has higher paper-faithfulness leverage than closing the `q` slot. From the P95 changelog text, those are explicitly listed as out-of-scope/future work, so `q`-binding is the correct next slice — but this is inference until round 2 confirms.

[usage] {"completion_tokens": 3556, "completion_tokens_details": {"reasoning_tokens": 1019}, "prompt_tokens": 163442, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 166998}

---

# Round 2

CONVERGED: YES

## Verdict
P96 (failure-signature `q` binding) is the correct next paper-faithful slice. P95 closed `c` (terminal failure category) at both cluster and task levels and `m` (mechanism) at cluster level via `mechanism_sha256`; `q` (causal_status) remains engine-modeled (`VerifierOutcome.causal_status`, `FailureSignature.causal_status`, `signature_of` clustering key) but absent from the reproduction-evidence contract. Round 2 resolves all four blocking design questions against repo evidence; remaining items are non-blocking.

## Critique
**Validated evidence (repo):**
- `src/self_harness/types.py`: `VerifierOutcome.causal_status: str` and `FailureSignature.causal_status: str` are first-class; `FailureSignature.key` joins all three phi components with `|`, so `q` is already a clustering axis.
- `src/self_harness/mining.py::signature_of`: builds `FailureSignature` from `run.outcome.causal_status`, so engine semantics already treat `q` as part of phi.
- `src/self_harness/_artifact_shapes.py::_HELD_IN_FAILURE_PATTERN_FIELDS`: contains `cluster_id, size, task_ids, mechanism_sha256, failure_category` — no `causal_status*` slot. `mechanism_sha256` is cluster-only in proposer context; `failure_category` (P95) is cluster-level in proposer context and task-level in proposal validation. This establishes the two viable precedents.
- `docs/architecture/schema_changelog.md` P95 entry explicitly positions `failure_category` as the `c` binding, leaving `q` as the remaining phi slot.
- `docs/operations/benchmark_reproduction_requirements.json` `proposer_context_ingredients` note describes `failure_category` binding via `cross_artifact_proposer_context_evidence_binding`; no `q` analog exists.
- No closed causal_status vocabulary exists in the codebase; the paper text ("the causal status of that behavior within the trace") describes an attribution, not a taxonomy.
- No operator proposer-context log format in `tests/fixtures/`, `docs/operations/`, or capture-extract tests records `causal_status` under any alias.

**Decisions (round 2):**
1. **Cluster-only disclosure.** Mirror `mechanism_sha256`. Avoids per-row field on `proposal_validation_manifest` task outcomes and avoids bumping `TASK_OUTCOMES_DIGEST_VERSION` from 2 to 3. Paper definition of phi is per-cluster (all tasks in a cluster share the triple), so cluster-level binding is paper-faithful; per-task drift detection of `q` is not justified given `q` is the attribution rationale, not a verifier signal.
2. **Opaque hash, no closed vocabulary.** Compute `causal_status_sha256 = _stable_payload_sha256({"causal_status": value})`, matching `mechanism_sha256`'s derivation from `pattern_id`. Closed vocabulary would be inference beyond repo evidence.
3. **No task-outcome digest version bump.** Follows from decision 1.
4. **Hash rotation set:** capture-manifest, capture-manifest diff, rehearsal, reproduction-readiness, release-candidate-evidence fixtures (paper-faithful paths). Canonical audit and canonical LLM-audit hashes unchanged because engine defaults are untouched.

## Required Changes
None blocking. The plan below is executable.

## Revised Plan

**P96 — Failure-Signature Causal Status (`q`) Binding**

*Invariant:* `q` is bound the same way P95 binds `c` and P78/P80 bind `m` at cluster level: optional on reduced bundles, machine-checked on paper-faithful bundles, no live contact, no reproduction claim.

**Files (additive, backward-compatible):**

1. `src/self_harness/_artifact_shapes.py`
   - Add optional `causal_status_sha256` (64 lowercase hex when present) to `_HELD_IN_FAILURE_PATTERN_FIELDS`. Keep absent/null valid.

2. `src/self_harness/capture_extract.py`
   - `extract_proposer_context_manifest`: when a raw `held_in_failure_patterns.patterns[]` row carries a string `causal_status`, compute `causal_status_sha256 = sha256(stable_json_dumps({"causal_status": value}) + "\n").hexdigest()` and attach. Reject non-string or empty values; absence remains valid. Do not touch `_PROPOSAL_CONTEXT_LOG_ROW_FIELDS` because the field lives one nesting level deeper inside the patterns list (already an opaque object per `_context_block`).

3. `src/self_harness/reproduction_bundle.py`
   - In `_cross_artifact_proposer_context_evidence_binding`, add `causal_status_violations` (cluster-level) to metadata. When a pattern declares `causal_status_sha256`, require every same-round baseline failing task it covers to be consistent with it. Because baseline task outcomes do not disclose `causal_status` (decision 1 keeps `q` cluster-only), the check mirrors the P95 declared-vs-baseline *category* mismatch only where the same-round proposer context also discloses `causal_status_sha256` for the *same cluster id* across rounds — i.e., it binds declared `causal_status_sha256` determinism across rounds within a run rather than against baseline task rows. Concretely: fail when the same `(round_index, cluster_id)` is declared in both proposer context and a prior `previous_attempted_edits` entry that binds that cluster's `targeted_mechanism_sha256`, and the declared `causal_status_sha256` disagrees with the prior round's same-cluster declaration. This is the minimal paper-faithful `q` invariant: a reusable mechanism must have a stable causal status across the lineage where it is referenced.

4. `src/self_harness/capture_manifest_diff.py`
   - Extend `_proposer_context_failure_category_summary` (or add a parallel `_proposer_context_causal_status_summary`) so `proposer-context-evidence-derivation` also compares planned vs realized per-cluster `causal_status_sha256` and emits drift entries. Do not bump `TASK_OUTCOMES_DIGEST_VERSION`.

5. `docs/operations/benchmark_reproduction_requirements.json` and `benchmark_reproduction_readiness.md`
   - Extend the `proposer_context_ingredients` notes to state that paper-faithful bundles may disclose `causal_status_sha256` per held-in failure pattern and that bundle verification binds it deterministically across proposer rounds and previous-attempted-edit references.

6. `docs/architecture/schema_changelog.md`
   - Add `## P96 Failure-Signature Causal Status Binding` entry following the P95 template, calling out: optional cluster-level field, opaque hash, no closed vocabulary, no audit/corpus schema bump, no canonical hash rotation, no task-outcome digest version bump.

7. `tests/test_reproduction_readiness.py` and `tests/test_capture_manifest.py`
   - Extend `_class_shaped_payloads` / `_proposer_context_rounds` so at least one cluster per round carries `causal_status_sha256` (compute via `_stable_payload_sha256({"causal_status": "<round-cluster causal text>"})`).
   - Extend `_proposer_context_previous_edits` so the referenced prior cluster's `targeted_mechanism_sha256` is accompanied by the same `causal_status_sha256` declared in that prior round's pattern, so the new cross-round invariant passes.
   - Add drift tests modeled on the P95 category-mismatch and mixed-baseline tests but scoped to the new cross-round same-cluster `causal_status_sha256` invariant.
   - Add a capture-extract test that a raw proposer-context pattern `causal_status` string is hashed and propagated, and that malformed values fail closed.
   - Add a backward-compat test: bundles and capture manifests without `causal_status_sha256` still verify.

8. Fixture hash rotation
   - Rotate `tests/fixtures/release_candidate/expected_hash.txt` and any paper-faithful capture-manifest / rehearsal / reproduction-readiness fixture hashes once payload generators are updated.
   - Do not rotate `tests/fixtures/canonical_audit_hash.txt` or `tests/fixtures/canonical_llm_audit_hash.txt`.

**Invariants / stop conditions:**
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact.
- `reproduction_claimed:false` everywhere; `reproduction_ready:false` unchanged.
- `make check`, `make readiness`, `make release-candidate-evidence`, `make capture-manifest-check`, `make capture-rehearsal` all pass.
- Canonical audit and canonical LLM-audit hashes unchanged.
- All schema additions optional; existing reduced bundles remain valid.
- No new artifact class, no new requirement row, no new signing custody path, no task-outcome digest version bump, no closed causal_status vocabulary.

**Stop condition for the slice:** `q` joins `c` and `m` as a machine-checkable phi component on paper-faithful reproduction bundles. Only remaining phi-related work is live operator capture.

## Remaining Open Questions
1. Should a future slice introduce per-task `causal_status_sha256` disclosure on `proposal_validation_manifest` task outcomes (enabling per-task drift detection)? Deferred — cluster-level is paper-faithful and matches `mechanism_sha256` precedent.
2. Should a future slice introduce a closed causal_status vocabulary? Deferred — no evidence in repo or paper appendix constrains `q` to a taxonomy; opaque hash is faithful.
3. Should a future slice bind `q` against baseline task outcomes (requiring per-task disclosure)? Deferred — blocked on decision 1.

None of the above block P96 execution.

[usage] {"completion_tokens": 2494, "completion_tokens_details": {"reasoning_tokens": 437}, "prompt_tokens": 168543, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 171037}
