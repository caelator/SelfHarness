# Round 1

CONVERGED: NO

## Verdict

The most valuable next locally-implementable slice is **P91: proposal-validation proposer-round LLM traffic binding plus digest version marker**. P90 closed rehearsal-only content drift detection for task-level outcomes but left proposal-validation rounds unbound from the LLM traffic that generated their candidates. P91 adds two narrow, additive fields to `proposal_validation_manifest` rounds (`proposer_round_request_sha256`, `proposer_round_response_sha256`) copied from the matching `proposer_llm_request_log` round, plus a `task_outcomes_digest_version: 1` marker on capture-manifest diff findings so future task_outcome shape changes are detectable without silent hash rotation. This advances the paper Section 3.4 "proposal summary" auditability requirement without adding a new artifact class, raw trace binding, schema bump on existing artifact versions, semantic rejection parsing, or any live service contact. The initial plan below is sound but needs three tightenings before execution.

## Critique

- **Evidence (repo):** `_cross_artifact_proposal_validation_binding` in `reproduction_bundle.py` already aligns proposal-validation `round_index` with proposer LLM `round_index` and validates candidate counts against `attempted_proposals`/`committed_proposals`. However, there is no per-round binding from validation evidence back to the specific LLM request/response hashes that generated the round's candidates. An operator could swap a proposer log file with different traffic for the same round_index and the existing checks would still pass.
- **Evidence (repo):** `_proposal_validation_round_summary` in `capture_manifest_diff.py` computes `baseline_task_outcomes_digest` and `candidate_task_outcomes_digests` without any version marker. If P89's task_outcome shape (e.g., a future `terminal_cause` field) is extended, a digest rotation would be silent: planned-vs-realized drift would fire purely from the hash change with no signal explaining that the digest definition itself changed.
- **Evidence (repo):** `proposer_llm_request_log` already exposes per-round `request_sha256`/`response_sha256` over the same paper backend set, so P91 can copy these into validation rounds without re-deriving them.
- **Inference (paper):** Section 3.4 requires each evaluated candidate to record "proposal summary." Binding each validation round to the proposer LLM request/response hashes for that round extends the audit summary to cover proposer traffic provenance without storing raw prompts or responses in the reproduction bundle. The paper does not require raw trace storage; opaque hashes are consistent with the existing `summary_sha256`, `edited_surface_sha256`, and `targeted_mechanism_sha256` pattern.
- **Risk (low):** Additive optional fields on `proposal_validation_manifest` rounds. The P84 artifact shape validator will need its closed field set extended; this is not a `schema_version` bump because the fields are additive and existing artifacts without the fields remain valid. Backward compatibility: bundle verification treats absent fields as "not bound" rather than failure, mirroring how optional `task_outcomes` are handled in P89.
- **Risk (medium, mitigated):** Capture-extract (`extract_proposal_validation_manifest`) must source the request/response hashes from a supplied `proposer_llm_request_log` artifact path. Operators who do not supply the proposer log will produce a validation manifest without the binding fields, and bundle verification will skip the check rather than fail. This mirrors the P80/P82 pattern for proposer-context cross-artifact checks.
- **Risk (low):** Digest version marker is metadata-only on the capture-manifest diff finding. No schema bump on capture-manifest diff reports; the marker is a new field inside the existing `metadata` block of `proposal-validation-derivation`.

## Required Changes

1. The two new validation-round fields must be optional in the artifact shape validator. Existing fixtures and operator bundles without them must continue to verify cleanly; the new bundle check fires only when both the validation manifest and the proposer log declare the binding.
2. Capture-extract must accept an optional `--proposer-request-log`/`proposer_request_log` argument for `proposal_validation_manifest` extraction. When supplied, it must validate the proposer log artifact shape first, locate the matching `round_index` row, and copy the round's `request_sha256` and `response_sha256` into the validation round. When the proposer log is absent, the fields are omitted.
3. Bundle verification (`_cross_artifact_proposal_validation_binding`) must add a `proposer_round_traffic_violations` metadata list. For each validation round that declares `proposer_round_request_sha256`/`proposer_round_response_sha256`, the corresponding proposer LLM round must exist and the hashes must match. Validation rounds without the binding fields are skipped; proposer logs without the binding fields are also skipped. The check fails closed only when one side declares the binding and the other does not.
4. The `task_outcomes_digest_version` marker must be a literal integer (`1`) emitted in `proposal-validation-derivation` finding metadata alongside the existing `baseline_task_outcome_digest_drift` and `candidate_task_outcome_digest_drifts` blocks. The digest helper `_task_outcomes_digest` does not change; only the version label is added. Future task_outcome shape extensions must bump this marker.
5. Non-goals (confirmed): no new artifact class, no raw trace binding, no separate baseline evaluation artifact, no semantic rejection-reason parsing, no per-candidate acceptance-rule change, no `proposal_validation_manifest.schema_version` bump, no canonical readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, and no reproduction-claim change.

## Revised Plan

**P91 — proposal-validation proposer-round LLM traffic binding plus task_outcomes digest version marker**

Files:
- `src/self_harness/_artifact_shapes.py`
  - Extend `_PROPOSAL_VALIDATION_ROUND_FIELDS` to include `proposer_round_request_sha256` and `proposer_round_response_sha256` as optional fields.
  - Extend `_proposal_validation_manifest` validator: when either field is present on a round, both must be present and must be valid 64-lowercase-hex sha256 digests.
- `src/self_harness/capture_extract.py`
  - Extend `extract_proposal_validation_manifest` to accept `proposer_request_log: Path | None = None`.
  - When supplied, load and shape-validate the proposer LLM request log artifact, build a `round_index -> (request_sha256, response_sha256)` map, and stamp both fields on each validation round whose `round_index` is present in the map. Rounds without a matching proposer row fail closed.
  - When not supplied, omit both fields (backward-compatible with existing operator workflows).
- `src/self_harness/reproduction_bundle.py`
  - In `_cross_artifact_proposal_validation_binding`, when both `proposal_validation_manifest` and `proposer_llm_request_log` are present, build a proposer-round traffic map and add `proposer_round_traffic_violations` to the check metadata. Violations cover missing proposer rounds, missing validation binding fields on one side only, and hash drift.
  - Skip the check when either artifact omits the binding fields entirely (advisory, not failure).
- `src/self_harness/capture_manifest_diff.py`
  - Extend `_proposal_validation_findings` metadata to include `task_outcomes_digest_version: 1` alongside the existing digest drift blocks.
  - Document the marker in the finding detail string so operators can identify which digest definition produced a drift signal.
- `src/self_harness/capture_manifest_build.py`
  - Update `_planned_artifact_stub` for `proposal_validation_manifest` to include the new binding fields using the existing proposer llm stub's per-round hashes, so planned and synthetic realized bundles remain symmetric.
- `tests/test_capture_extract.py`
  - Add a test where `--proposer-request-log` is supplied and the extracted validation manifest carries the expected binding fields.
  - Add a test where the proposer request log is omitted and the extracted manifest omits the fields.
  - Add a test where the proposer log's round_index does not match the audit round_index and extraction fails closed.
- `tests/test_reproduction_readiness.py`
  - Add a test where validation rounds declare the binding fields, the proposer log matches, and `cross_artifact_proposal_validation_binding` passes with `proposer_round_traffic_violations: []`.
  - Add a test where one validation round's `proposer_round_request_sha256` drifts from the proposer log and the check fails with a traffic violation entry.
  - Add a test where the proposer log is absent from the bundle and the validation rounds also omit the binding fields; the check passes (skip path).
- `tests/test_capture_manifest.py`
  - Add a test confirming the `task_outcomes_digest_version: 1` marker is present in `proposal-validation-derivation` finding metadata for both planned and realized sides.
- `docs/architecture/productionization_brief.md`
  - Append P91 entry using the P84–P90 template, citing paper Section 3.4 and P78/P84.
- `docs/operations/benchmark_reproduction_readiness.md`
  - Update the `proposal_validation_manifest` row to mention proposer-round traffic binding and the digest version marker.

Non-goals (explicit):
- No new artifact class.
- No raw trace binding.
- No separate baseline evaluation artifact class.
- No semantic rejection-reason parsing.
- No per-task candidate-vs-baseline acceptance rule change.
- No `proposal_validation_manifest.schema_version` bump.
- No new artifact class, no readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, no reproduction-claim change.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.
- Capture-rehearsal fixture hash rotates because the diff finding metadata gains a digest version marker and the synthetic validation stub gains proposer-round traffic binding fields; canonical paper-fidelity audit hash unchanged.

Stop conditions:
- All listed tests pass.
- `make reproduction-readiness-check` still reports `reproduction_ready: false` because live dependencies remain unprovisioned.
- No new live service contact, schema version bump, or reproduction claim introduced.

## Remaining Open Questions

- Should the proposer-round traffic binding eventually cover per-candidate granularity (e.g., a `proposer_response_chunk_sha256` per candidate)? **Non-blocking for P91**: the paper's proposer returns K proposals per request, so per-request binding is the natural unit; per-candidate chunking would require proposer response parsing and is deferred.
- Should the digest version marker also cover proposer-context evidence digests if those are added in a future slice? **Non-blocking for P91**: only task_outcomes have a content digest today; a parallel marker can be added when needed.
- Should the binding be made mandatory for paper reproduction bundles (i.e., required rather than optional)? **Non-blocking for P91**: P91 ships it as optional-additive to preserve backward compatibility with existing operator bundles; a future slice can promote it to required once the fixture set and operator tooling consistently produce the fields.

[usage] {"completion_tokens": 5616, "completion_tokens_details": {"reasoning_tokens": 3257}, "prompt_tokens": 149119, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 154735}

---

# Round 2

CONVERGED: YES

## Verdict

P91 (proposal-validation proposer-round LLM traffic binding plus task_outcomes digest version marker) is ready to execute. All Round 1 required changes have been incorporated and validated against repository evidence: optional-additive validator fields, capture-extract sourcing from a supplied proposer log, fail-closed round-index matching, bundle-verification traffic violations with skip-when-both-absent semantics, and a literal `task_outcomes_digest_version: 1` marker on the existing `proposal-validation-derivation` finding metadata. The slice advances the paper Section 3.4 "proposal summary" auditability requirement without contradicting the aggregate acceptance rule, without adding a new artifact class, without bumping artifact schema versions, and without contacting live services.

## Critique

- **Evidence (repo):** `_cross_artifact_proposal_validation_binding` in `reproduction_bundle.py` aligns validation `round_index` with proposer LLM `round_index` and validates candidate counts, but today an operator could swap a proposer log with different traffic for the same round and existing checks would still pass. P91 closes that gap with hash-level binding.
- **Evidence (repo):** `proposer_llm_request_log` validator in `_artifact_shapes.py` already exposes per-round `request_sha256`/`response_sha256` over the paper backend set, so P91 copies these without re-deriving them.
- **Evidence (repo):** `_proposal_validation_round_summary` in `capture_manifest_diff.py` computes task_outcome digests but emits no version marker. P91's literal `task_outcomes_digest_version: 1` makes future task_outcome shape changes non-silent.
- **Evidence (paper):** Section 3.4 requires each evaluated candidate to record "proposal summary"; opaque hash binding is consistent with the existing `summary_sha256`, `edited_surface_sha256`, and `targeted_mechanism_sha256` pattern and does not require raw trace storage.
- **Risk (low, mitigated):** Optional fields on `proposal_validation_manifest` rounds; absent fields remain valid for older fixtures. The P84 shape validator extends its closed field set rather than bumping `schema_version`.
- **Risk (low, mitigated):** Bundle verification skips when both sides omit the binding fields, mirroring P80/P82 proposer-context pattern.
- **Risk (low):** Digest version marker is metadata-only inside the existing `proposal-validation-derivation` finding metadata block; no capture-manifest diff schema bump.

## Required Changes

All Round 1 required changes remain valid and incorporated:

1. Optional-additive validator fields with paired-presence and sha256 grammar enforcement — confirmed.
2. `--proposer-request-log`/`proposer_request_log` on capture-extract with proposer-log shape validation first and fail-closed on round mismatch — confirmed.
3. `proposer_round_traffic_violations` in bundle verification metadata with skip-when-both-absent semantics and fail-closed on one-sided declaration — confirmed.
4. Literal `task_outcomes_digest_version: 1` marker inside the existing `proposal-validation-derivation` finding metadata — confirmed.
5. Non-goals confirmed: no new artifact class, no raw trace binding, no separate baseline artifact, no semantic rejection-reason parsing, no per-task acceptance rule change, no schema-version bumps, no readiness hash rotation, no live service contact, no reproduction-claim change — confirmed.

## Revised Plan

**P91 — proposal-validation proposer-round LLM traffic binding plus task_outcomes digest version marker**

Files:
- `src/self_harness/_artifact_shapes.py`
  - Extend `_PROPOSAL_VALIDATION_ROUND_FIELDS` to include `proposer_round_request_sha256` and `proposer_round_response_sha256` as optional fields.
  - Extend `_proposal_validation_manifest` validator: paired-presence rule and 64-lowercase-hex grammar.
- `src/self_harness/capture_extract.py`
  - Extend `extract_proposal_validation_manifest` signature with `proposer_request_log: Path | None = None`.
  - When supplied: load + shape-validate the proposer log, build `round_index -> (request_sha256, response_sha256)`, stamp both fields on matching validation rounds, fail closed on missing rounds.
  - When absent: omit both fields (backward-compatible).
- `src/self_harness/reproduction_bundle.py`
  - In `_cross_artifact_proposal_validation_binding`: when both artifacts present and declare binding, add `proposer_round_traffic_violations` metadata; skip when both sides omit.
- `src/self_harness/capture_manifest_diff.py`
  - Extend `_proposal_validation_findings` metadata with literal `task_outcomes_digest_version: 1` alongside existing digest drift blocks; document marker in the finding detail string.
- `src/self_harness/capture_manifest_build.py`
  - Update `_planned_artifact_stub` for `proposal_validation_manifest` to stamp the two new binding fields from the existing proposer llm stub's per-round hashes so planned/realized remain symmetric.
- `tests/test_capture_extract.py`
  - Supplied `--proposer-request-log` produces binding fields.
  - Omitted proposer log omits fields.
  - Round-index mismatch fails closed.
- `tests/test_reproduction_readiness.py`
  - Binding on both sides + matching proposer log: `proposer_round_traffic_violations: []`.
  - One round drifts request hash: failure with traffic violation.
  - Both sides omit binding: check passes (skip path).
- `tests/test_capture_manifest.py`
  - `task_outcomes_digest_version: 1` marker present in both planned and realized finding metadata.
- `docs/architecture/productionization_brief.md`
  - Append P91 entry citing paper Section 3.4 and P78/P84.
- `docs/operations/benchmark_reproduction_readiness.md`
  - Update `proposal_validation_manifest` row to mention proposer-round traffic binding and digest version marker.

Non-goals (explicit):
- No new artifact class, no raw trace binding, no separate baseline evaluation artifact class, no semantic rejection-reason parsing, no per-task acceptance rule change, no `proposal_validation_manifest.schema_version` bump, no readiness hash rotation, no live service contact, no reproduction-claim change.

Verification gates:
- `make check`, `make readiness`, `make reproduction-readiness-check`, `make capture-manifest-check`, `make capture-manifest-diff-check`.
- Capture-rehearsal fixture hash rotates because diff finding metadata gains a digest version marker and synthetic validation stub gains proposer-round traffic binding fields; canonical paper-fidelity audit hash unchanged.

Stop conditions:
- All listed tests pass.
- `make reproduction-readiness-check` still reports `reproduction_ready: false`.
- No new live service contact, schema version bump, or reproduction claim introduced.

## Remaining Open Questions

All confirmed non-blocking:
- Per-candidate proposer response chunking (`proposer_response_chunk_sha256` per candidate) — deferred; per-request binding is the natural paper unit since the proposer returns K proposals per request.
- Extending the digest version marker pattern to future proposer-context digests — defer when those digests exist.
- Promoting traffic binding to required for paper reproduction bundles — defer until fixture/operator tooling consistently emits the fields; P91 ships optional-additive to preserve backward compatibility.

[usage] {"completion_tokens": 1623, "completion_tokens_details": {"reasoning_tokens": 92}, "prompt_tokens": 153864, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 155487}
