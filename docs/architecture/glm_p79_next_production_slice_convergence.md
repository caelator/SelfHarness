# Round 1

CONVERGED: NO

## Verdict

Round 1 proposes P79 = **proposer round-count reconciliation against live evidence**. This is the highest-leverage offline slice after P78 because P78 already landed `proposer_llm_request_log` as a paper-required artifact class with per-round hashes, but it deliberately did not constrain the *number* of rounds against the evaluation/audit evidence. Without that reconciliation, a bundle could claim paper fidelity while silently running a proposer schedule inconsistent with the paper Section 3.2 loop (T rounds × K proposal width × 2 evaluation repeats). The slice is offline-only, schema-additive at the verifier level, and leaves canonical audit/readiness hashes unchanged. It is not yet CONVERGED because three blocking decisions need to be made before execution.

## Critique

Blocking open decisions identified by abstract analysis:

1. **What is the canonical reconciliation formula?** The paper (Section 3.1, Algorithm 1) fixes the model, evaluator, tool set, benchmark environment, and number of rounds T, with K parallel proposals per round. P67 already pins the audit to 2 attempts × 64 tasks = 128 audit rows. The proposer side has no analogous invariant. Candidate invariants to choose between:
   - (a) `proposer_llm_request_log.round_count == live_harbor_audit.round_count` where the audit carries an explicit round-level breakdown; **but** current `live_harbor_audit` shape (P67) has no round-level field, so this would force an audit schema bump.
   - (b) Derive expected proposer rounds from the paper protocol: `round_count` must equal `fixed_protocol_config.self_harness_rounds` and each round must carry exactly K = `fixed_protocol_config.proposal_width` proposal branches, with the audit rows mapping 1:1 onto `(round, proposal_branch, attempt_index) ∈ {0..T-1} × {0..K-1} × {0,1}`.
   - (c) Weaker: only enforce that `round_count` is non-zero and that the union of all `attempted_proposals + committed_proposals` per round matches the audit row count, without pinning T or K.
   The right answer depends on whether `fixed_protocol_config` is intended to carry `self_harness_rounds` and `proposal_width` — currently it does not (see `_artifact_shapes.py::_fixed_protocol_config` which checks only `benchmark_protocol`, `models`, `evaluator`, `tool_set`, `decoding_budget`, `fixed_across_variants`). This is the real blocker.

2. **Does P79 extend `fixed_protocol_config` schema, or add a separate `self_harness_protocol_config` artifact?** Extending `fixed_protocol_config` is lower-friction but rotates the canonical hash of every existing fixture that builds one. Adding a sibling artifact avoids hash rotation but adds a new required class. The repo evidence (P78 fixture blast radius language) suggests extending in place is preferred, but this needs to be an explicit decision.

3. **What happens when the proposer log is absent (heuristic-proposer path)?** P78 added `_CAPTURE_RUN_ID_OPTIONAL_WHEN_ABSENT_CLASSES = frozenset({"proposer_llm_request_log"})` for capture-run-id binding. The round-count invariant has the same dual semantics problem: paper bundles require it; heuristic-proposer bundles must skip without failing. The pattern exists, but the new check name (`cross_artifact_proposer_round_count`) needs the same exemption wiring.

Non-blocking notes:
- Egress attestation and prompt-determinism bindings remain correctly deferred to P80+.
- Canonical audit hash is safe under all three candidate formulas as long as the engine's default mock-LLM path does not write `proposer_llm_request_log.json`.

## Required Changes

1. Pick one reconciliation formula from (a)/(b)/(c) above and encode the exact arithmetic.
2. Decide whether `fixed_protocol_config` schema is extended in place (`self_harness_rounds`, `proposal_width`) or a sibling artifact is added.
3. Confirm `cross_artifact_proposer_round_count` reuses the P78 skip-on-absent exemption pattern and enumerate every fixture file that must rotate.

## Revised Plan

**P79 (DRAFT — pending the three decisions above): Proposer round-count reconciliation**

1. `src/self_harness/_artifact_shapes.py`
   - Extend `fixed_protocol_config` closed field set with `self_harness_rounds: int >= 1` and `proposal_width: int >= 1` (assumes decision #2 = extend in place).
   - Extend `_fixed_protocol_config` validator to require both fields.
2. `src/self_harness/reproduction_bundle.py`
   - Add `_cross_artifact_proposer_round_count(bundle, proposer_entry, protocol_entry, audit_entry, evaluation_entry)`:
     - Skip (return `None`) when `proposer_entry` is absent.
     - Fail closed when `protocol_entry`, `audit_entry`, or `evaluation_entry` is absent.
     - Require `proposer.round_count == protocol.self_harness_rounds`.
     - Require `sum(round.attempted_proposals) <= protocol.proposal_width * protocol.self_harness_rounds` (attempted includes pre-filter proposals; K per round is the upper bound).
     - Require `sum(round.committed_proposals) <= sum(audit trial_artifacts) / 2` because each accepted proposal becomes one harness variant evaluated twice.
   - Wire into `_cross_artifact_invariants` after `_cross_artifact_proposer_model_binding`.
3. `src/self_harness/reproduction_bundle.py::_cross_artifact_capture_run_id_binding`
   - No change; exemption already covers `proposer_llm_request_log`.
4. Tests
   - `tests/test_reproduction_readiness.py::_class_shaped_payloads`: extend `fixed_protocol_config` payload with `self_harness_rounds` and `proposal_width`; extend `proposer_llm_request_log` payload so its `round_count` matches.
   - Add `test_reproduction_bundle_rejects_proposer_round_count_drift`.
   - Add `test_reproduction_bundle_skips_proposer_round_count_when_absent`.
5. Docs
   - `docs/operations/benchmark_reproduction_readiness.md`: document the new invariant and the formula.
   - `docs/architecture/schema_changelog.md`: note `fixed_protocol_config` schema remains `1.0` but gains two required fields; all paper-faithful fixtures rotate.
6. Fixture blast radius
   - `tests/fixtures/release_candidate/reproduction_readiness_result.json` rotates.
   - `tests/fixtures/release_candidate/release_candidate_evidence*.json` rotate.
   - Capture-manifest and capture-rehearsal fixtures rotate.
   - `tests/fixtures/canonical_llm_audit_hash.txt` **must not** rotate.

Stop conditions: same P78 pattern (make check / readiness / capture-* / release-candidate-evidence green; canonical audit hash unchanged; `reproduction_claimed: false` everywhere; no live contact).

## Remaining Open Questions

1. **Blocking:** Reconciliation formula (a)/(b)/(c)? — needs operator/architect decision; paper Section 3.2 implies (b) but does not state T or K numerically.
2. **Blocking:** Extend `fixed_protocol_config` in place vs. add sibling artifact? — needs decision before code touches `_artifact_shapes.py`.
3. **Blocking:** Should `attempted_proposals` per round be capped at K, or only `committed_proposals`? — paper Section 3.3 says the proposer generates K *distinct* branches; engine-side grounding/diversity filtering happens after, so `attempted >= committed` but `attempted` has no obvious K relationship. Needs decision.
4. Non-blocking: P80+ proposer egress attestation binding scope.
5. Non-blocking: P81+ prompt-determinism binding once paper prompt templates are versioned.

[usage] {"completion_tokens": 1800, "completion_tokens_details": {"reasoning_tokens": 132}, "prompt_tokens": 52250, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 54050}

---

# Round 2

CONVERGED: YES

## Verdict

Round 2 closes all three Round 1 blockers with architect decisions grounded in (i) the paper Algorithm 1 inputs list, (ii) the existing `_fixed_protocol_config` validator's field set, and (iii) P78's established `attempted_proposals`/`committed_proposals` semantics. The slice is offline-implementable, schema-additive on `fixed_protocol_config`, and execution-ready. The canonical LLM audit hash fixture is protected because the proposer recorder remains opt-in and off by default; only reproduction-readiness, release-candidate-evidence, capture-manifest, and capture-rehearsal fixtures rotate through existing validator plumbing.

Evidence (validated against repo):
- `src/self_harness/_artifact_shapes.py::_fixed_protocol_config` currently checks `benchmark_protocol`, `models`, `evaluator`, `tool_set`, `decoding_budget`, `fixed_across_variants`, `capture_run_id`, and `mode`. Adding two required ints is a closed-set extension consistent with the existing pattern.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_invariants` is already the single wiring point for cross-artifact checks; adding `_cross_artifact_proposer_round_count` immediately after `_cross_artifact_proposer_model_binding` matches the established sequencing.
- `src/self_harness/reproduction_bundle.py::primary_capture_run_ids` iterates `_PRIMARY_CAPTURED_ARTIFACT_CLASSES`; `proposer_llm_request_log` is already a member (P78). Round-count binding therefore inherits the skip-on-absent contract automatically once `_CAPTURE_RUN_ID_OPTIONAL_WHEN_ABSENT_CLASSES` is consulted — but since the new check skips when the entry itself is absent (mirroring `_cross_artifact_proposer_model_binding`), no new exemption set is needed.
- Paper Algorithm 1 explicitly lists `proposal width K, rounds T` as inputs, justifying in-place extension of `fixed_protocol_config` rather than a sibling artifact.

## Critique

Architect decisions closing Round 1 blockers:

1. **Reconciliation formula (b), locked.** `proposer_llm_request_log.round_count` must equal `fixed_protocol_config.self_harness_rounds`, and each round's `attempted_proposals` must equal `fixed_protocol_config.proposal_width`. Rationale: paper Algorithm 1 fixes both T and K as protocol inputs; the proposer is contracted to generate exactly K distinct branches per round, so `attempted_proposals == K` is the faithful invariant. `committed_proposals` remains data-dependent (acceptance-gated) and is not pinned to K.

2. **Extend `fixed_protocol_config` in place.** Add `self_harness_rounds: int >= 1` and `proposal_width: int >= 1` as required fields. Rationale: paper Section 3.1/Algorithm 1 treats rounds and proposal width as part of the fixed protocol alongside model/evaluator/tool-set; a sibling artifact would fragment the protocol declaration. Schema version stays `1.0`; closed-field extension is the additive path.

3. **`attempted_proposals` cap = K exactly, not ">=".** Paper Section 3.3 "The proposer generates K mutually distinct proposal bundles." Generation is the contract; downstream grounding/diversity filtering affects `committed_proposals`, not `attempted_proposals`. Lock the equality.

Non-blocking notes:
- P80+ proposer egress attestation and P81+ prompt-determinism bindings remain deferred.
- Canonical LLM audit hash does not rotate (recorder is opt-in).
- The equality on `attempted_proposals == K` assumes the recorder counts branches before grounding/diversity filtering; P78 already established this seam via `LLMProposerRoundMetadata`.

## Required Changes

None blocking. The plan below is execution-ready.

## Revised Plan

**P79: Proposer round-count reconciliation against fixed protocol**

1. `src/self_harness/_artifact_shapes.py`
   - Extend `_fixed_protocol_config` validator:
     - Require `self_harness_rounds` to be an `int >= 1`.
     - Require `proposal_width` to be an `int >= 1`.
   - No change to the `fixed_protocol_config` schema version; this is an additive closed-field extension.
2. `src/self_harness/reproduction_bundle.py`
   - Add `_cross_artifact_proposer_round_count(bundle, proposer_entry, protocol_entry)`:
     - Return `None` when `proposer_entry` is absent (heuristic-proposer path; mirrors `_cross_artifact_proposer_model_binding` skip semantics).
     - Fail closed when `proposer_entry` is present but `protocol_entry` is absent.
     - Require `proposer.round_count == protocol.self_harness_rounds`.
     - Require `len(proposer.rounds) == protocol.self_harness_rounds`.
     - For each round, require `round.attempted_proposals == protocol.proposal_width`.
     - Do not constrain `round.committed_proposals` numerically (data-dependent; covered by `<= attempted_proposals` invariant already in P78).
   - Wire into `_cross_artifact_invariants` immediately after `_cross_artifact_proposer_model_binding`.
3. Tests
   - `tests/test_reproduction_readiness.py::_class_shaped_payloads`:
     - Extend `fixed_protocol_config` payload with `self_harness_rounds` and `proposal_width`.
     - Extend `proposer_llm_request_log` payload so `round_count`, `len(rounds)`, and each round's `attempted_proposals` match.
   - Add `test_reproduction_bundle_rejects_proposer_round_count_drift`.
   - Add `test_reproduction_bundle_rejects_proposer_attempted_proposals_drift`.
   - Add `test_reproduction_bundle_skips_proposer_round_count_when_absent` (heuristic-proposer bundle).
4. Docs
   - `docs/operations/benchmark_reproduction_readiness.md`: document the new invariant and the formula.
   - `docs/architecture/schema_changelog.md`: note `fixed_protocol_config` gains two required fields; all paper-faithful fixtures rotate; schema version remains `1.0`.
   - `docs/operations/benchmark_reproduction_requirements.json`: update the `fixed_model_evaluator_tool_budget` notes to mention round/width binding via the new cross-artifact check.
5. Fixture blast radius (explicit):
   - `tests/fixtures/release_candidate/reproduction_readiness_result.json` rotates.
   - `tests/fixtures/release_candidate/release_candidate_evidence*.json` rotate.
   - Capture-manifest and capture-rehearsal fixtures rotate through existing validator plumbing.
   - `tests/fixtures/canonical_llm_audit_hash.txt` **must not** rotate.

Stop conditions:
- `make check`, `make readiness`, `make capture-manifest-check`, `make reproduction-bundle-check`, `make capture-extract-check`, and `make release-candidate-evidence` all pass.
- `tests/fixtures/canonical_llm_audit_hash.txt` byte-identical to current.
- Rotated fixture hashes recorded in `docs/architecture/schema_changelog.md`.
- `reproduction_claimed is False` on every new artifact and report.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact in any default target.

## Remaining Open Questions

None blocking. Future work, explicitly out of scope for P79:

1. P80: proposer egress attestation binding (`proposer_llm_request_log` ↔ `network_resource_controls_attestation`).
2. P81: proposer prompt-determinism binding (`request_sha256` ↔ canonical paper-prompt hash); deferred until paper prompt templates are versioned and pinned.
3. P82 (advisory): reconciling `committed_proposals` per round against accepted-candidate counts in the audit's harness lineage, once the audit schema records per-round candidate acceptance counts.

[usage] {"completion_tokens": 2334, "completion_tokens_details": {"reasoning_tokens": 683}, "prompt_tokens": 55611, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 57945}
