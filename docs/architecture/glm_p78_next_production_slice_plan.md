CONVERGED: YES

## Verdict

Round 3 closes all three blocking Round 2 questions. The slice is the correct next production-fidelity step, is offline-implementable, additive, and execution-ready. The plan below locks canonical recipes, encodes the required-vs-skip dual semantics, and pins the per-round closed field set. No evidence is missing and no experiment is required.

Evidence (validated against repository):
- `src/self_harness/llm_proposer.py`: `LLMProposer.propose` calls `self.client.complete(system_prompt, user_prompt)` exactly once per round and discards the response; `LLMClient` Protocol is a single-method `complete(system_prompt, user_prompt) -> str` — confirms `RecordingLLMClient` wrapper is a pure engine concern.
- `src/self_harness/engine.py::SelfHarnessEngine.__init__` injects `self.proposer`, enabling in-place wrapping when `proposer` is `LLMProposer` and recording is enabled.
- `tests/test_llm_engine_loop.py::_run_mock_llm_canonical` constructs `LLMProposer(MockLLMClient(seed=0))` without any recorder; default-off recorder preserves `tests/fixtures/canonical_llm_audit_hash.txt`.
- `src/self_harness/_artifact_shapes.py` uses `_reject_unknown_fields`, `_require_ok_live`, and `_non_empty_str` helpers; new validator reuses the same grammar.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_capture_run_id_binding` calls `primary_capture_run_ids`, which iterates `_PRIMARY_CAPTURED_ARTIFACT_CLASSES`; adding the new class to that set is the single edit point, and skip-on-absent must be implemented by filtering `missing` against an exemption list.
- `src/self_harness/capture_extract.py::extract_live_two_repeat_evaluation_report` plus `_CAPTURE_ENVELOPE_FIELDS` is the canonical envelope-discipline template for `extract_proposer_llm_request_log`.
- `src/self_harness/types.py::stable_json_dumps` is available as the canonical serializer for the `request_sha256` recipe.

Inference (not yet in repo, but determined by the plan):
- `_cross_artifact_proposer_model_binding` is separable from `_cross_artifact_model_protocol_binding`, mirroring the P76/P77 split.
- Canonical audit hash does not rotate because the recorder is opt-in and off in the canonical mock-LLM path.

## Critique

Decisions closing Round 2 blockers:

1. **Canonical hash recipes (was blocking).** Locked:
   - `request_sha256 = sha256((stable_json_dumps({"system_prompt": system_prompt, "user_prompt": user_prompt}) + "\n").encode("utf-8")).hexdigest()`
   - `response_sha256 = sha256(response.encode("utf-8")).hexdigest()`
   Rationale: uses the existing canonical `stable_json_dumps` serializer; `\n` terminator matches the rest of the audit-tree discipline; response is raw bytes because the client contract returns `str` and no further structure is canonicalized by the proposer Protocol.

2. **Skip-on-absent vs. required-class dual semantics (was blocking).** Encoded:
   - `proposer_llm_request_log` is **added** to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES` and to `docs/operations/benchmark_reproduction_requirements.json::required_artifact_classes`, so paper reproduction bundles require it.
   - The new class is added to a new `_CAPTURE_RUN_ID_OPTIONAL_WHEN_ABSENT_CLASSES = frozenset({"proposer_llm_request_log"})` exemption. `primary_capture_run_ids` does not append to `missing` when the absent class is in this set; the cross-artifact sweep then passes whether the class is present (covered normally) or absent (exempted).
   - Rationale: paper Section 4 uses the LLM proposer, so paper reproduction requires this class; heuristic-proposer bundles are not paper reproduction bundles and rely on the exemption to keep the sweep well-formed.

3. **`proposal_count`/`accepted_count` ambiguity (was blocking).** Resolved: per-round record carries `attempted_proposals` (proposals parsed before filtering) and `committed_proposals` (post-filter rows that landed in `proposal_rows` with any of `accepted|merged|superseded|rejected|invalid`). This is the stable target future acceptance-rate and prompt-determinism bindings will pin to.

4. **`proposer_backend_map` key set (Round 2 noted).** Decided: closed subset `{minimax, qwen, glm}`, fail closed on unknown keys, mirroring `fixed_protocol_config.models` validation.

Non-blocking notes carried forward unchanged:
- Round-count reconciliation against `live_harbor_audit` trial count deferred to P79; P78 only enforces backend-set equality.
- Egress and prompt-determinism bindings deferred to P80+.
- Fixture-hash blast radius is fully enumerated in the plan; `canonical_llm_audit_hash.txt` is explicitly protected.

## Required Changes

None blocking. The revised plan below is execution-ready.

## Revised Plan

**P78: Proposer LLM request log artifact class and proposer↔preflight model binding**

1. `src/self_harness/_artifact_shapes.py`
   - Add `_PROPOSER_LLM_REQUEST_LOG_FIELDS` closed set: `{schema_version, ok, mode, capture_run_id, rounds, round_count, reproduction_claimed, boundary}`.
   - Add per-round closed set `{round_index, backend, model, request_sha256, response_sha256, prompt_tokens, completion_tokens, attempted_proposals, committed_proposals}`.
   - Add `_proposer_llm_request_log` validator requiring `schema_version:"1.0"`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, non-empty `rounds`, monotonic `round_index` starting at 0 with no gaps, `round_count == len(rounds)`, `backend ∈ {minimax,qwen,glm}` (normalized through `_normal_model_backends`), 64-lowercase-hex sha256 grammar for request/response hashes, non-negative integer token and count fields, and `reproduction_claimed:false`.
   - Register in `REPRODUCTION_ARTIFACT_CLASS_VALIDATORS`.
2. `src/self_harness/engine.py`
   - Add internal `RecordingLLMClient` wrapping `LLMClient`; on each `complete(system_prompt, user_prompt)` it records the call and computes `request_sha256`/`response_sha256` per the canonical recipe.
   - Add `proposer_request_log: list[ProposerRoundRecord] | None = None` plus `enable_proposer_request_log()` opt-in method. Default `None`; canonical mock-LLM path unchanged.
   - When enabled and `self.proposer` is `LLMProposer`, wrap `proposer.client` in place before `run()`; after each round append `ProposerRoundRecord(round_index, request_sha256, response_sha256, prompt_tokens, completion_tokens, attempted_proposals, committed_proposals)`.
     - `attempted_proposals` = count of proposals parsed before `_enforce_grounding_and_diversity` and budget filtering (requires the proposer to expose this count, see step 3).
     - `committed_proposals` = `len(proposal_rows)` for that round.
   - After `run()`, if `proposer_request_log` is populated, write `proposer_llm_request_log.json` to the audit tree behind the existing schema-version discipline with `capture_run_id` left empty (filled by extractor).
3. `src/self_harness/llm_proposer.py`
   - No change to the `LLMClient` Protocol.
   - Expose `LLMProposerRoundMetadata(attempted_proposals: int, committed_proposals: int)` so the engine wrapper can read counts without re-parsing the proposer internals. Update `LLMProposer.propose` to return `(list[Proposal], LLMProposerRoundMetadata)` OR keep `propose` signature and add a last-round metadata attribute; prefer the attribute to avoid breaking the `Proposer` Protocol. Recorder reads `attempted_proposals` from `proposer._last_round_metadata` (or equivalent) after each round.
4. `src/self_harness/capture_extract.py`
   - Add `extract_proposer_llm_request_log(capture_envelope, request_log_rows, *, capture_run_id, proposer_backend_map)` mirroring `extract_live_two_repeat_evaluation_report`.
   - Reject unknown envelope fields via `_CAPTURE_ENVELOPE_FIELDS`; reject non-live mode; reject missing capture_run_id; reject `reproduction_claimed:true`.
   - For each row: require `round_index`, `request_sha256`, `response_sha256` (64 lowercase hex), `prompt_tokens`, `completion_tokens` (non-negative ints), `proposer_client` label (default `"primary"`).
   - Stamp `backend` from `proposer_backend_map[proposer_client]` and require values to be in `{minimax, qwen, glm}`; fail closed on unknown client label or unknown backend.
   - Stamp `model` from the backend's canonical paper model name (`MiniMax-M2.5`, `Qwen3.5-35B-A3B`, `GLM-5`) — derive from a new `_PAPER_MODEL_BY_BACKEND` constant co-located with `PAPER_MODEL_BACKENDS`.
   - Emit `attempted_proposals` and `committed_proposals` verbatim.
   - Run `_validated("proposer_llm_request_log", payload)`.
   - Extend `EXTRACTABLE_ARTIFACT_CLASSES` and `extract_artifact_from_paths` dispatcher.
5. `src/self_harness/reproduction_bundle.py`
   - Add `proposer_llm_request_log` to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES`.
   - Add `_CAPTURE_RUN_ID_OPTIONAL_WHEN_ABSENT_CLASSES = frozenset({"proposer_llm_request_log"})`.
   - Update `primary_capture_run_ids` to skip appending to `missing` when the absent artifact class is in the exemption set; document that present-but-malformed still fails closed via the shape validator.
   - Add `_cross_artifact_proposer_model_binding(bundle, proposer_entry, preflight_entry, protocol_entry)`:
     - `return None` when `proposer_entry` is absent (defensive skip).
     - Fail closed when present but `preflight_entry` or `protocol_entry` is absent.
     - Read `rounds[]`, normalize backends through `_normal_model_backends`.
     - Require `proposer_backends == preflight_backends == protocol_backends == PAPER_MODEL_BACKENDS`.
     - Record metadata `{proposer_backends, preflight_backends, protocol_backends, unexpected_proposer_backends, missing_from_preflight, missing_from_protocol}`.
   - Wire the check into `_cross_artifact_invariants` immediately after `_cross_artifact_model_protocol_binding`.
6. `docs/operations/benchmark_reproduction_requirements.json`
   - Add `proposer_llm_request_log` to `required_artifact_classes` with source/provider/custody fields mirroring other primary captured classes.
7. `src/self_harness/capture_manifest.py` and `src/self_harness/capture_manifest_diff.py`
   - Accept the new class through the existing validator plumbing; no new diff finding kind for P78 (round-count drift is covered by the shape validator's monotonic-index rule).
8. Tests
   - `tests/test_llm_engine_loop.py`:
     - Add `test_canonical_mock_llm_audit_hash_unchanged_with_recorder_off` (existing assertion is sufficient; keep the default path untouched).
     - Add `test_opt_in_proposer_request_log_writes_well_formed_artifact`: enable recorder, run one round with `MockLLMClient`, assert `proposer_llm_request_log.json` exists, `schema_version=="1.0"`, `reproduction_claimed is False`, `rounds[0].request_sha256` matches `sha256((stable_json_dumps({"system_prompt":..., "user_prompt":...}) + "\n").encode())`, and `rounds[0].committed_proposals == len(rounds[0])`'s `proposal_rows`.
   - `tests/test_capture_extract.py`:
     - Add `test_extract_proposer_llm_request_log_happy_path`.
     - Add `test_extract_proposer_llm_request_log_rejects_unknown_envelope_field`.
     - Add `test_extract_proposer_llm_request_log_rejects_malformed_sha256`.
     - Add `test_extract_proposer_llm_request_log_rejects_unknown_backend_key`.
     - Add `test_extract_proposer_llm_request_log_rejects_reproduction_claim`.
     - Add `test_extract_proposer_llm_request_log_rejects_non_round_index_gap`.
   - `tests/test_reproduction_readiness.py`:
     - Extend `_class_shaped_payloads` with a valid `proposer_llm_request_log` payload (3 rounds, one per backend, monotonic `round_index 0..2`, valid sha256, `attempted_proposals`/`committed_proposals` non-negative ints).
     - Add `test_reproduction_bundle_binds_proposer_backends_to_preflight_and_protocol` (pass).
     - Add `test_reproduction_bundle_rejects_proposer_backend_drift` (proposer emits `openai` → fail closed).
     - Add `test_reproduction_bundle_skips_proposer_binding_when_artifact_absent` (heuristic-proposer path; assert `_cross_artifact_proposer_model_binding` is not emitted and `cross_artifact_capture_run_id_binding` passes via exemption).
   - `tests/test_capture_manifest.py` and `tests/test_capture_rehearsal.py`: rotate fixtures per the existing plumbing; assert the new class is covered by planned-shape validation.
9. Docs
   - `docs/operations/benchmark_reproduction_readiness.md`: add `proposer_llm_request_log` row with the dual required-class / skip-on-absent semantics and the new `cross_artifact_proposer_model_binding` language.
   - `docs/operations/capture_extract.md`: document `extract_proposer_llm_request_log` and the `proposer_backend_map` input shape.
   - `docs/operations/model_backend_preflight.md`: cross-reference that preflight alone is insufficient because the new proposer-LLM binding is what proves the proposer actually used the declared paper backends.
   - `docs/architecture/productionization_brief.md`: add P78 slice entry with standard boundary language.
   - `docs/architecture/schema_changelog.md`: add P78 entry; note new artifact class, new optional cross-artifact binding, no audit/corpus/manifest schema version bump.

Fixture hash blast radius (explicitly enumerated):
- `tests/test_reproduction_readiness.py::_class_shaped_payloads` gains a valid `proposer_llm_request_log` payload.
- `tests/fixtures/release_candidate/reproduction_readiness_result.json` rotates.
- `tests/fixtures/release_candidate/release_candidate_evidence*.json` fixtures rotate.
- Capture-manifest and capture-rehearsal fixtures rotate through existing validator plumbing.
- `tests/fixtures/canonical_llm_audit_hash.txt` **must not** rotate.

Stop conditions:
- `make check`, `make readiness`, `make capture-manifest-check`, `make reproduction-bundle-check`, `make capture-extract-check`, and `make release-candidate-evidence` all pass.
- `tests/fixtures/canonical_llm_audit_hash.txt` byte-identical to current.
- Reproduction-readiness, release-candidate evidence, capture-manifest, and capture-rehearsal fixture hashes rotate and are recorded in `docs/architecture/schema_changelog.md`.
- `reproduction_claimed is False` on every new artifact and report.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact in any default target.

## Remaining Open Questions

None blocking. Future work, explicitly out of scope for P78:

1. P79: reconcile proposer `round_count` against `live_harbor_audit` trial count (×2 attempts ÷ 2 repeats = expected proposer rounds).
2. P80+: proposer egress attestation binding `proposer_llm_request_log` to `network_resource_controls_attestation`.
3. P81+: proposer prompt-determinism binding `request_sha256` to a canonical paper-prompt hash; deferred until paper prompt templates are versioned and pinned.
