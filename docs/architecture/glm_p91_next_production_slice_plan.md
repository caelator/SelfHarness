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
