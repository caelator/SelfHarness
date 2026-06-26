# Round 1

CONVERGED: NO

## Verdict

The candidate slice — capturing proposer-side LLM traffic as a new primary reproduction artifact class so `model_backend_preflight_report` reachability can be bound to the actual LLM backends the Self-Harness proposer queried during the live Terminal-Bench run — is the correct next production-fidelity slice. It closes a real gap: today preflight proves provider reachability in isolation, but nothing in the evidence bundle proves the proposer actually used those declared paper backends during the captured run. This is offline-implementable, additive, and foundational for future proposer-traffic bindings (egress, prompt determinism, token-budget attestation).

Evidence (from repository):
- `src/self_harness/llm_proposer.py`: `LLMClient.complete(system_prompt, user_prompt) -> str` has no request/response logging seam; `LLMProposer.propose` discards the raw response after parsing.
- `src/self_harness/model_backend_preflight.py`: `_live_check` records only `response_text_sha256` and `usage`; nothing binds the proposer's actual per-round backend choice.
- `src/self_harness/_artifact_shapes.py::REPRODUCTION_ARTIFACT_CLASS_VALIDATORS`: no class covers proposer LLM traffic.
- `docs/operations/benchmark_reproduction_requirements.json` is the canonical required-class set; adding a class rotates fixture reproduction-readiness / release-candidate evidence hashes but not the canonical paper-fidelity audit hash.

Inference: the engine can be extended with an opt-in `LLMRequestRecorder` wrapper without changing the `LLMClient` Protocol or breaking P12 `MockLLMClient` canonical-hash coverage, but the exact seam needs one more round to lock down.

## Critique

Material risks that must be resolved before execution:

1. **Logging seam shape.** Two viable options:
   - (a) Wrap `LLMClient` in the engine with a `RecordingLLMClient` that records `(system_prompt, user_prompt, response, derived_metadata)` per call.
   - (b) Push a callback through `LLMProposer(client, on_response=...)`.
   Option (a) is engine-owned, keeps `LLMProposer` pure, and matches the existing proposer test surface. Option (b) leaks engine concerns into the proposer. **Recommend (a) but call this out explicitly.**
2. **Backend attribution.** The proposer-side `LLMClient` does not today carry a backend id. The recorder needs the operator to declare which paper backend each proposer client maps to (the same id space as `model_backend_preflight_report`). This must be operator-supplied at capture time, not inferred from response bytes.
3. **Primary captured class scope.** Should this be in `_PRIMARY_CAPTURED_ARTIFACT_CLASSES` (capture_run_id-bound) or derived? Inference: **primary**, because proposer traffic was emitted during the live run, not derived post-capture. Adding it to `primary_capture_run_ids` extends `cross_artifact_capture_run_id_binding`.
4. **Per-round vs per-proposal granularity.** The proposer emits one LLM call per round (not per proposal), confirmed by `LLMProposer.propose`. So `rounds[]` should have exactly `rounds_run` entries; cross-artifact check should reconcile with audit `rounds` count when both are present.
5. **Fixture hash rotation blast radius.** Adding `proposer_llm_request_log` to the required-class set rotates: `reproduction_readiness_result.json`, `release_candidate_evidence*.json`, capture-manifest fixtures, capture-rehearsal fixtures, reproduction-bundle fixtures. Stop condition must list every fixture that rotates.
6. **Skip-on-absent semantics.** The proposer is optional in the harness (toy heuristic proposer exists). The artifact class must be required *for reproduction* but the cross-artifact proposer-model binding should skip cleanly when the proposer-LLM artifact is absent from a bundle that legitimately used the heuristic proposer. This conflicts with "required class for reproduction". Resolution: the class is required for paper reproduction (which always uses the LLM proposer), so absence fails the reproduction gate; cross-artifact binding still needs internal skip-on-missing-trust-report defensive behavior.

Non-blocking but noted: the new artifact class will need a `capture-extract` extractor that consumes an operator-captured proposer log JSONL plus a capture envelope, mirroring `extract_live_two_repeat_evaluation_report`.

## Required Changes

Before this plan is execution-ready, round 2 must:

1. Decide and document the logging seam (recommend `RecordingLLMClient` wrapper constructed by the engine).
2. Specify operator backend attribution input shape (recommend a `proposer_backend_map: dict[str, str]` keyed by proposer instance label or by capture manifest planned-run field).
3. Lock the per-round record schema and the `rounds_run` reconciliation rule against `live_harbor_audit` and audit `rounds` count.
4. Enumerate every committed fixture file that rotates and confirm the canonical paper-fidelity audit hash in `tests/fixtures/canonical_llm_audit_hash.txt` does NOT rotate (the recorder is opt-in and off in the mock-LLM canonical path).
5. Decide whether the proposer-LLM artifact also feeds `cross_artifact_model_protocol_binding` or gets its own `cross_artifact_proposer_model_binding` check. Recommend a **new** check to keep preflight↔protocol and proposer↔preflight concerns separable.

## Revised Plan

**P78: Proposer LLM request log artifact class and proposer↔preflight model binding**

1. `src/self_harness/_artifact_shapes.py`:
   - Add `_PROPOSER_LLM_REQUEST_LOG_FIELDS` closed set and `_proposer_llm_request_log` validator requiring `schema_version:"1.0"`, `mode:"live"`, `ok:true`, non-empty `capture_run_id`, non-empty `rounds` list with closed per-round fields `{round_index, backend, model, request_sha256, response_sha256, prompt_tokens, completion_tokens, proposal_count, accepted_count}`, monotonic `round_index` starting at 0 with no gaps, `round_count` reconciling to `len(rounds)`, normalized `backend` values in `{minimax,qwen,glm}`, sha256 grammar for request/response hashes, non-negative integer token and count fields, and `reproduction_claimed:false`.
   - Register in `REPRODUCTION_ARTIFACT_CLASS_VALIDATORS`.
2. `src/self_harness/engine.py`:
   - Add optional `proposer_request_log: list[ProposerRoundRecord] | None` and an internal `RecordingLLMClient` wrapper used only when the proposer is an `LLMProposer` and recording is enabled. Default off; canonical mock-LLM path unchanged.
   - Write `proposer_llm_request_log.json` to the audit tree when populated, behind the existing `schema_version` discipline.
3. `src/self_harness/llm_proposer.py`:
   - No Protocol change. Optionally expose `LLMProposerRoundMetadata` dataclass for the engine wrapper to consume.
4. `src/self_harness/capture_extract.py`:
   - Add `extract_proposer_llm_request_log(capture_envelope, request_log_rows, *, capture_run_id, proposer_backend_map)` mirroring `extract_live_two_repeat_evaluation_report` validation discipline; emits the validated artifact shape.
5. `src/self_harness/reproduction_bundle.py`:
   - Add `proposer_llm_request_log` to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES` so `cross_artifact_capture_run_id_binding` covers it.
   - Add `_cross_artifact_proposer_model_binding(bundle, proposer_entry, preflight_entry, protocol_entry)` that:
     - skips when `proposer_llm_request_log` is absent (defensive);
     - fails closed when proposer is present but preflight or protocol is absent;
     - requires the set of backends observed in proposer rounds to equal the normalized `model_backend_preflight_report.backends` and to be covered by `fixed_protocol_config.models`;
     - records `proposer_backends`, `preflight_backends`, `protocol_backends`, `unexpected_proposer_backends`, `missing_from_preflight`, `missing_from_protocol`.
   - Wire the new check into `_cross_artifact_invariants` after `_cross_artifact_model_protocol_binding`.
6. `docs/operations/benchmark_reproduction_requirements.json`:
   - Add `proposer_llm_request_log` to `required_artifact_classes` with the same source/provider/custody shape as other primary captured classes.
7. `src/self_harness/capture_manifest.py` and `src/self_harness/capture_manifest_diff.py`:
   - Accept planned/realized `proposer_llm_request_log` shape through the existing class-validator plumbing; no new diff finding kind required for P78 (round-count drift is covered by shape validator).
8. Tests:
   - `tests/test_llm_engine_loop.py`: assert the canonical mock-LLM audit hash is unchanged when the recorder is off (default) and that an opt-in recorder writes a well-formed `proposer_llm_request_log.json`.
   - `tests/test_reproduction_readiness.py`:
     - extend `_class_shaped_payloads` with a valid `proposer_llm_request_log` payload (rotates fixture hashes);
     - add `test_reproduction_bundle_binds_proposer_backends_to_model_preflight`;
     - add `test_reproduction_bundle_rejects_proposer_backend_drift`;
     - add `test_reproduction_bundle_rejects_proposer_round_count_drift` (round_count mismatch with shape validator).
   - `tests/test_capture_extract.py`: add `extract_proposer_llm_request_log` happy path, unknown-field rejection, malformed sha256 rejection, and `reproduction_claimed:true` rejection.
9. Docs:
   - `docs/operations/benchmark_reproduction_readiness.md`: add `proposer_llm_request_log` row to the artifact shape table with the new binding language.
   - `docs/operations/capture_extract.md`: document the new extractor and the operator `proposer_backend_map` input.
   - `docs/operations/model_backend_preflight.md`: cross-reference the proposer-LLM binding so operators understand preflight alone is insufficient.
   - `docs/architecture/productionization_brief.md`: add P78 slice entry with standard boundary language.
   - `docs/architecture/schema_changelog.md`: note the new artifact class and binding; no audit/corpus/manifest schema version bump.

Stop conditions:
- `make check`, `make readiness`, `make capture-manifest-check`, `make reproduction-bundle-check`, `make capture-extract-check`, and `make release-candidate-evidence` all pass.
- Canonical paper-fidelity audit hash in `tests/fixtures/canonical_llm_audit_hash.txt` unchanged.
- Reproduction-readiness, release-candidate evidence, capture-manifest, and capture-rehearsal fixture hashes rotate as expected and are recorded in the changelog.
- `reproduction_claimed is False` on every new report and artifact.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact in any default target.

## Remaining Open Questions

1. (Round 2 must close) Logging seam: confirm `RecordingLLMClient` wrapper in `engine.py` rather than a callback on `LLMProposer`. Blocking because it affects the engine API and test surface.
2. (Round 2 must close) Operator backend attribution: is it supplied per proposer instance at engine construction, or per capture manifest planned-run field? Blocking because it changes the capture-extract CLI signature.
3. (Round 2 should answer) Should `cross_artifact_proposer_model_binding` also reconcile proposer round_count against `live_harbor_audit` trial count (×2 attempts ÷ 2 repeats = expected proposer rounds), or stay bounded to backend-set equality for P78? Recommend deferring round-count cross-artifact reconciliation to P79.
4. (Non-blocking) Future slice: proposer egress attestation binding proposer traffic to `network_resource_controls_attestation`. Defer to P80+.
5. (Non-blocking) Future slice: proposer prompt-determinism binding `request_sha256` to a canonical paper-prompt hash. Defer until paper prompt templates are versioned.

[usage] {"completion_tokens": 4002, "completion_tokens_details": {"reasoning_tokens": 1415}, "prompt_tokens": 67761, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 71763}

---

# Round 2

CONVERGED: NO

## Verdict

Round 2 makes decisive progress on the two blocking open questions from Round 1, but two new blocking concerns surface that must be resolved in Round 3 before execution. The slice remains the correct next production-fidelity step, and the revised plan below reflects closed decisions on the logging seam and operator backend attribution.

## Critique

Round 1 open questions, now resolved by this round:

1. **Logging seam (was blocking).** Decision: `RecordingLLMClient` wrapper owned by the engine, not a callback threaded through `LLMProposer`. Evidence supporting the decision:
   - `LLMProposer.propose` in `src/self_harness/llm_proposer.py` calls `self.client.complete(system_prompt, user_prompt)` exactly once per round and discards the raw response after parsing.
   - `LLMClient` is already a `Protocol` with a single `complete(system_prompt, user_prompt) -> str` method, so a wrapper that preserves structural typing and records `(system_prompt, user_prompt, response, derived_metadata)` is a pure engine concern.
   - `SelfHarnessEngine.__init__` in `src/self_harness/engine.py` already injects `self.proposer`; the engine can wrap `proposer.client` in place when `proposer` is an `LLMProposer` and recording is enabled.
   - The canonical mock-LLM hash test in `tests/test_llm_engine_loop.py::_run_mock_llm_canonical` uses `LLMProposer(MockLLMClient(seed=0))`; keeping the recorder off by default preserves the canonical audit hash.

2. **Operator backend attribution (was blocking).** Decision: operator `proposer_backend_map: dict[str, str]` keyed by **paper backend id** and supplied through the **capture-extract CLI** only. The recorder inside the engine records prompts/responses/usage but does **not** attribute backends; attribution is applied offline during extraction. Rationale: the proposer-side `LLMClient` today has no backend id field, and adding one to the `LLMClient` Protocol would leak operator capture metadata into the live execution path. Backend attribution is a post-capture concern and belongs next to the existing `extract_*` family in `src/self_harness/capture_extract.py`.

New blocking concerns surfaced by Round 2:

3. **Per-round `request_sha256` determinism and content.** The proposed shape records `request_sha256` per round. The proposer's request is `(system_prompt, user_prompt)`. The `system_prompt` is currently built from held-in context including `OP_WHITELIST`, `EDITABLE_SURFACES`, and a fixed schema string; the `user_prompt` is `stable_json_dumps(payload)` of held-in evidence. Round 2 must lock the canonical `request_sha256` recipe (`sha256(stable_json_dumps({"system_prompt": ..., "user_prompt": ...}) + "\n")`) so a future prompt-determinism binding slice has a stable target. This is blocking because it determines the validator's sha256 grammar and the recorder's emitted field.

4. **Skip-on-absent vs. required-class semantics for the toy heuristic proposer.** Round 1 flagged this. The capture/rehearsal/bundle fixture path uses a synthetic `_class_shaped_payloads` set; adding `proposer_llm_request_log` to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES` plus the required-class set forces every operator reproduction bundle to carry it. But the Self-Harness engine also supports the heuristic proposer (no LLM), and a future operator may legitimately produce a paper-scale run with a non-LLM proposer for a *non-paper* reproduction path. Round 2 must decide explicitly:
   - The class is **required for the paper reproduction bundle** (paper Section 4 always uses the LLM proposer).
   - The class is **exempt from `cross_artifact_capture_run_id_binding`** when absent, mirroring the skip-on-absent pattern of `cross_artifact_audit_image_binding` in `src/self_harness/reproduction_bundle.py`, so that legitimate absent-class heuristic-proposer bundles do not fail the run-id binding sweep.
   This dual semantics must be encoded, not left to prose. Blocking because it changes the implementation of `primary_capture_run_ids` and the new cross-artifact check.

Non-blocking but noted:

5. Round 1 proposed `round_count` reconciliation against `live_harbor_audit` trial count. Confirmed deferred to P79; P78 scope is backend-set equality only.

6. The new extractor `extract_proposer_llm_request_log` must consume operator JSONL plus a capture envelope, mirroring `extract_live_two_repeat_evaluation_report` discipline including the `_CAPTURE_ENVELOPE_FIELDS` reject-unknown-fields behavior. This is well-defined and not blocking.

## Required Changes

Round 3 must close the following before this plan is execution-ready:

1. **Lock the `request_sha256` and `response_sha256` canonical recipe** in the validator and the recorder. Proposed: `sha256(stable_json_dumps({"system_prompt": system_prompt, "user_prompt": user_prompt}) + "\n").hexdigest()` for requests, `sha256(response.encode("utf-8")).hexdigest()` for responses.
2. **Encode the skip-on-absent/required-class dual semantics** for `proposer_llm_request_log`:
   - Add to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES`.
   - Add to `docs/operations/benchmark_reproduction_requirements.json` required classes.
   - Exempt the class from `cross_artifact_capture_run_id_binding` when absent, mirroring `cross_artifact_audit_image_binding` skip logic.
   - Make the new `_cross_artifact_proposer_model_binding` skip cleanly when the proposer-LLM artifact is absent (defensive), fail closed when present but preflight or protocol is absent.
3. **Pin the per-round record closed field set** and the monotonic-`round_index` rule. The set proposed in Round 1 is acceptable; add explicit `attempted_proposals` (total parsed before filtering) and `committed_proposals` (post-filter, accepted/merged/superseded/rejected/invalid) so future prompt-determinism and acceptance-rate bindings have a stable target. `proposal_count` and `accepted_count` from Round 1 are ambiguous between these.
4. **Enumerate the fixture hash blast radius** explicitly, because the class is now added to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES`:
   - `tests/test_reproduction_readiness.py::_class_shaped_payloads` gains a valid `proposer_llm_request_log` payload.
   - `tests/fixtures/release_candidate/reproduction_readiness_result.json` rotates.
   - `tests/fixtures/release_candidate/release_candidate_evidence*.json` fixtures rotate.
   - Capture-manifest and capture-rehearsal fixtures rotate via the existing validator plumbing.
   - `tests/fixtures/canonical_llm_audit_hash.txt` **must not** rotate because the recorder is off by default and the audit-tree bytes do not change.
5. **Decide explicitly** whether `proposer_backend_map` keys must be a subset of `{minimax, qwen, glm}` or may include unknown future backends. Recommend: closed subset, fail closed on unknown keys, to keep parity with `fixed_protocol_config.models` validation.

## Revised Plan

**P78: Proposer LLM request log artifact class and proposer↔preflight model binding**

1. `src/self_harness/_artifact_shapes.py`:
   - Add `_PROPOSER_LLM_REQUEST_LOG_FIELDS` closed set with `schema_version:"1.0"`, `mode:"live"`, `ok:true`, non-empty `capture_run_id`, closed `rounds` list, monotonic `round_index` starting at 0 with no gaps, `round_count == len(rounds)`, per-round closed field set `{round_index, backend, model, request_sha256, response_sha256, prompt_tokens, completion_tokens, attempted_proposals, committed_proposals}`, normalized `backend` ∈ `{minimax, qwen, glm}`, sha256 grammar for request/response hashes, non-negative integer token and count fields, and `reproduction_claimed:false`.
   - Register `_proposer_llm_request_log` in `REPRODUCTION_ARTIFACT_CLASS_VALIDATORS`.
2. `src/self_harness/engine.py`:
   - Add an internal `RecordingLLMClient` wrapper implementing `LLMClient` that records `(system_prompt, user_prompt, response, prompt_tokens, completion_tokens)` per call.
   - Add an opt-in `proposer_request_log` field on `SelfHarnessEngine`, default off.
   - When recording is enabled and `self.proposer` is an `LLMProposer`, wrap `proposer.client` in place before `engine.run()`.
   - After each round, append a `ProposerRoundRecord` with `attempted_proposals` and `committed_proposals` derived from the round's `proposal_rows`.
   - Write `proposer_llm_request_log.json` to the audit tree when populated, behind the existing schema-version discipline.
3. `src/self_harness/llm_proposer.py`:
   - No `LLMClient` Protocol change.
   - Optionally expose `LLMProposerRoundMetadata` dataclass for the engine wrapper.
4. `src/self_harness/capture_extract.py`:
   - Add `extract_proposer_llm_request_log(capture_envelope, request_log_rows, *, capture_run_id, proposer_backend_map)` mirroring `extract_live_two_repeat_evaluation_report`.
   - Reject unknown envelope fields, non-live modes, missing capture_run_id, malformed sha256, unknown backend keys (closed `{minimax,qwen,glm}`), and `reproduction_claimed:true`.
   - `proposer_backend_map` maps an internal proposer client label (default `"primary"`) to a paper backend id; the extractor stamps `backend` per round from the map.
5. `src/self_harness/reproduction_bundle.py`:
   - Add `proposer_llm_request_log` to `_PRIMARY_CAPTURED_ARTIFACT_CLASSES`.
   - Update `primary_capture_run_ids` and `_cross_artifact_capture_run_id_binding` to **exempt** `proposer_llm_request_log` from the required-capture-run-id sweep when absent (mirroring the skip-on-absent pattern of `_cross_artifact_audit_image_binding`).
   - Add `_cross_artifact_proposer_model_binding(bundle, proposer_entry, preflight_entry, protocol_entry)` that:
     - returns `None` when `proposer_llm_request_log` is absent (defensive skip);
     - fails closed when present but `model_backend_preflight_report` or `fixed_protocol_config` is absent;
     - requires the normalized set of backends observed in `rounds[]` to equal the normalized `model_backend_preflight_report.backends` and to be covered by `fixed_protocol_config.models`;
     - records `proposer_backends`, `preflight_backends`, `protocol_backends`, `unexpected_proposer_backends`, `missing_from_preflight`, `missing_from_protocol`.
   - Wire the new check into `_cross_artifact_invariants` after `_cross_artifact_model_protocol_binding`.
6. `docs/operations/benchmark_reproduction_requirements.json`:
   - Add `proposer_llm_request_log` to `required_artifact_classes` with the same source/provider/custody shape as other primary captured classes.
7. `src/self_harness/capture_manifest.py` and `src/self_harness/capture_manifest_diff.py`:
   - Accept the new class through the existing validator plumbing; no new diff finding kind for P78.
8. Tests:
   - `tests/test_llm_engine_loop.py`: assert canonical mock-LLM audit hash unchanged when recorder is off; assert opt-in recorder produces a well-formed `proposer_llm_request_log.json`.
   - `tests/test_reproduction_readiness.py`: extend `_class_shaped_payloads` with a valid `proposer_llm_request_log` payload (rotates fixture hashes); add bundle binding tests for pass, backend drift, and absent-class skip.
   - `tests/test_capture_extract.py`: add extractor happy path, unknown-field rejection, malformed sha256 rejection, unknown backend key rejection, and `reproduction_claimed:true` rejection.
9. Docs:
   - `docs/operations/benchmark_reproduction_readiness.md`: add `proposer_llm_request_log` row and new binding language.
   - `docs/operations/capture_extract.md`: document `extract_proposer_llm_request_log` and the `proposer_backend_map` input.
   - `docs/operations/model_backend_preflight.md`: cross-reference the new proposer-LLM binding.
   - `docs/architecture/productionization_brief.md`: add P78 slice entry.
   - `docs/architecture/schema_changelog.md`: note the new artifact class and binding; no audit/corpus/manifest schema version bump.

Stop conditions:
- `make check`, `make readiness`, `make capture-manifest-check`, `make reproduction-bundle-check`, `make capture-extract-check`, and `make release-candidate-evidence` all pass.
- `tests/fixtures/canonical_llm_audit_hash.txt` unchanged.
- Reproduction-readiness, release-candidate evidence, capture-manifest, and capture-rehearsal fixture hashes rotate as expected and are recorded in the changelog.
- `reproduction_claimed is False` on every new report and artifact.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/cloud contact in any default target.

## Remaining Open Questions

1. (Round 3 must close) Lock the exact canonical `request_sha256` and `response_sha256` recipes. Blocking because it determines validator sha256 grammar and recorder output.
2. (Round 3 must close) Encode and test the skip-on-absent/required-class dual semantics for `proposer_llm_request_log` in `cross_artifact_capture_run_id_binding`. Blocking because it changes `primary_capture_run_ids` behavior and the fixture set.
3. (Round 3 must close) Resolve `proposal_count`/`accepted_count` ambiguity by pinning the per-round record to `attempted_proposals` and `committed_proposals`. Blocking because it determines validator closed-field set.
4. (Non-blocking) Future P79 slice: reconcile proposer `round_count` against `live_harbor_audit` trial count (×2 attempts ÷ 2 repeats = expected proposer rounds).
5. (Non-blocking) Future P80+ slice: proposer egress attestation binding `proposer_llm_request_log` to `network_resource_controls_attestation`.
6. (Non-blocking) Future slice: proposer prompt-determinism binding `request_sha256` to a canonical paper-prompt hash; deferred until paper prompt templates are versioned.

[usage] {"completion_tokens": 3132, "completion_tokens_details": {"reasoning_tokens": 15}, "prompt_tokens": 72961, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 76093}

---

# Round 3

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

[usage] {"completion_tokens": 3564, "completion_tokens_details": {"reasoning_tokens": 89}, "prompt_tokens": 76666, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 80230}
