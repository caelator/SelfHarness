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
