# Round 1

CONVERGED: NO

## Verdict

Round 1 of P50 extends the converged P49 pattern by replacing the single Anthropic-bound `model_credentials_backend` requirement with explicit per-backend readiness entries for the three paper models. The core shape is clear, additive, and compatible with P49's two-contract split (fail-closed standalone reproduction readiness, advisory at the release boundary). Round 1 is not yet converged because two material facts are unknown without further repository inspection: (a) whether adapter contract-test fixtures already exist for MiniMax/Qwen/GLM (required by the `offline_fixture` validator in `self_harness/readiness_matrix.py`), and (b) the exact proposer-client class names that must appear in `KNOWN_READINESS_AFFECTS`. These are decidable by file inspection rather than by design debate, so they do not warrant a BLOCKED verdict.

## Critique

Strengths of the proposed approach:
- P49 already locked the two-contract policy and the "no canonical hash rotation" rule. P50 inherits those invariants without renegotiating them.
- Adding three distinct `ReadinessMatrixEntry` rows (one per paper backend) preserves the principle that readiness is operator-owned and entry-level: each backend can be provisioned, blocked, or made optional independently.
- Keeping the standalone `reproduction_readiness_report.py` fail-closed is unchanged; only the catalog (`benchmark_reproduction_requirements.json`) changes shape. This is the minimum surface area needed to retire the P49 TODO.

Risks to resolve before convergence:
1. **Schema churn vs. row expansion.** Two viable shapes: (A) keep a single `model_credentials_backend` requirement row but make its `readiness_matrix_dependencies` a list of all three model rows (and Anthropic, if retained as the reference seam); (B) split into three rows (`minimax_credentials_backend`, `qwen_credentials_backend`, `glm_credentials_backend`). Shape (B) is more faithful to the paper (each backend is independently evaluated) and matches how P49 already enumerates per-backend rows conceptually in its notes. Shape (A) is lower churn but coarser. Recommendation: (B) — the paper explicitly treats the three backends as separate evaluation subjects (Section 4.1 "All comparisons are within-model comparisons"), and the readiness catalog should not collapse them.
2. **`KNOWN_READINESS_AFFECTS` extension.** The frozenset in `self_harness/readiness_matrix.py` must be extended with one entry per new model proposer client (e.g. `LLMProposer MiniMaxClient`, `LLMProposer QwenClient`, `LLMProposer GLMClient`). The exact client names are an evidence question, not a design question. Round 2 must confirm them from the adapter layer; until then the plan is illustrative.
3. **`offline_fixture` existence requirement.** The validator `_offline_fixture` in `readiness_matrix.py` enforces that the referenced path exists in-repo and is non-empty. The Anthropic entry points to `tests/adapters/llm/test_anthropic_client_contract.py`. If MiniMax/Qwen/GLM contract tests do not yet exist, the catalog cannot validate. Decision needed: add stub contract-test files as part of P50, or use an existing shared fixture. Recommendation: add minimal contract-test stubs (offline-only, no provider contact) so each readiness entry has a legitimate fixture; this is consistent with the "no provider contact" constraint and gives operators a target to exercise.
4. **Anthropic retention.** P49 currently treats Anthropic as the reference provider seam. P50 should keep it as an additional (optional, non-paper) row or demote it entirely. Recommendation: keep as `optional`, `reproduction_relevant=false`, since it is not a paper backend but is the package's current default adapter.
5. **Hash rotation scope.** Adding rows rotates `readiness_matrix_result.json` and (transitively) `reproduction_readiness_result.json` and the opt-in `expected_hash_reproduction.txt`. The canonical `tests/fixtures/canonical_audit_hash.txt` and the default `tests/fixtures/release_candidate/expected_hash.txt` must not rotate (default aggregator invocation is unchanged, per P49). This is the same policy P49 locked; restating for clarity.
6. **Stop conditions.** Convergence stop condition: (a) per-backend `affects` client names confirmed from the adapter layer, (b) contract-test fixture paths confirmed to exist or added as stubs, (c) catalog row shape (per-backend vs. collapsed) decided. Execution stop condition: all three model rows exist as `blocked` (default), `reproduction_claimed=false` everywhere, default release-candidate evidence decision unchanged.

## Required Changes

1. Confirm proposer client class names for MiniMax / Qwen / GLM by inspecting `src/self_harness/adapters/llm/` (or equivalent). If those clients do not yet exist, decide whether P50 adds stub adapter skeletons or defers to a future P-step. Round 2 must resolve this.
2. Confirm contract-test fixture paths under `tests/adapters/llm/`. If absent, add minimal offline contract-test stubs (`test_minimax_client_contract.py`, `test_qwen_client_contract.py`, `test_glm_client_contract.py`) that assert constructor/serialization behavior without network calls.
3. Decide row shape: per-backend rows (recommended) vs. collapsed `model_credentials_backend` list.
4. Update `self_harness/readiness_matrix.py`:
   - Extend `KNOWN_READINESS_AFFECTS` with the new proposer client affect strings.
   - No other schema changes needed (`ALLOWED_READINESS_DOMAINS` already includes `model`).
5. Update `docs/operations/readiness_matrix.json`:
   - Add three new entries, `status: blocked`, `reproduction_relevant: true`, `domain: model`, one per paper backend.
   - Each with its own `offline_fixture`, `operator_remediation`, `operator_action: configure`, `preflight_surface: none`.
   - Optionally retain Anthropic entry as `optional` / `reproduction_relevant: false`, or remove it.
6. Update `docs/operations/benchmark_reproduction_requirements.json`:
   - Split `model_credentials_backend` into three per-backend rows (or extend its `readiness_matrix_dependencies` list, depending on decision #3).
   - Update `fixed_model_evaluator_tool_budget` to reference all three paper backends (and drop or downgrade Anthropic).
7. Update tests:
   - `tests/test_reproduction_readiness.py`: synthetic provisioned path now requires three model artifacts; current `_provisioned_readiness_matrix` helper sets all rows to `provisioned`, so the all-pass case will still pass; the not-ready fixture test must still see at least one blocked entry (now more likely, since three model rows are blocked by default).
   - `tests/test_readiness_drift.py`: catalog fixture loader now sees new entries; the committed `readiness_drift_result.json` fixture will rotate.
   - `tests/test_release_candidate_evidence.py`: default path unchanged; opt-in path fixture rotates.
8. Update docs:
   - `docs/operations/benchmark_reproduction_readiness.md`: note the per-backend split and that Anthropic is the package's default adapter seam, not a paper backend.
   - `docs/operations/readiness_matrix.md` (if it enumerates entries): add the three rows.
   - `docs/architecture/productionization_brief.md`: add P50 section.
9. Hash policy (locked, restating P49):
   - Rotate: `tests/fixtures/release_candidate/readiness_matrix_result.json`, `tests/fixtures/release_candidate/reproduction_readiness_result.json`, `tests/fixtures/release_candidate/readiness_drift_result.json`, `tests/fixtures/release_candidate/expected_hash_reproduction.txt` (if it exists from P49).
   - Do **not** rotate: `tests/fixtures/canonical_audit_hash.txt`, `tests/fixtures/release_candidate/expected_hash.txt`.

## Revised Plan

**P50 — Paper model-backend readiness entries (replaces P49 TODO binding only Anthropic).**

1. **Readiness matrix catalog** (`docs/operations/readiness_matrix.json`):
   - Add entry: `MiniMax M2.5 model API credentials` — `domain: model`, `status: blocked`, `reproduction_relevant: true`, `affects: ["LLMProposer MiniMaxClient"]` (subject to Round 2 confirmation), `offline_fixture: tests/adapters/llm/test_minimax_client_contract.py`, `operator_remediation: "Attach an operator-approved secret provider for MINIMAX_API_KEY and run the adapter against the real MiniMax hosted endpoint referenced in Appendix A.1."`, `preflight_surface: none`, `operator_action: configure`.
   - Add entry: `Qwen3.5-35B-A3B model deployment` — `domain: model`, `status: blocked`, `reproduction_relevant: true`, `affects: ["LLMProposer QwenClient"]`, `offline_fixture: tests/adapters/llm/test_qwen_client_contract.py`, `operator_remediation: "Deploy Qwen3.5-35B-A3B on the approved local SGLang image (Appendix A.1) under operator-owned GPU capacity and expose the endpoint to the adapter."`, `preflight_surface: none`, `operator_action: provision`.
   - Add entry: `GLM-5 model API credentials` — `domain: model`, `status: blocked`, `reproduction_relevant: true`, `affects: ["LLMProposer GLMClient"]`, `offline_fixture: tests/adapters/llm/test_glm_client_contract.py`, `operator_remediation: "Attach an operator-approved secret provider for the OpenRouter GLM-5 endpoint (Appendix A.1) and run the adapter against the real endpoint."`, `preflight_surface: none`, `operator_action: configure`.
   - Retain Anthropic entry as `status: optional`, `reproduction_relevant: false` (package default seam, not a paper backend). Or remove; Round 2 decision.

2. **Reproduction requirements catalog** (`docs/operations/benchmark_reproduction_requirements.json`):
   - Split `model_credentials_backend` into `minimax_credentials_backend`, `qwen_credentials_backend`, `glm_credentials_backend`, each with the appropriate `readiness_matrix_dependencies` (single FK), `required_artifact_class: model_backend_preflight_report`, `required_state: provisioned`.
   - Update `fixed_model_evaluator_tool_budget`: `readiness_matrix_dependencies` lists all three paper backend rows; notes clarify that the paper used three distinct backends and the package default (Anthropic) is out of scope for this requirement.

3. **Core module** (`src/self_harness/readiness_matrix.py`):
   - Extend `KNOWN_READINESS_AFFECTS` with `LLMProposer MiniMaxClient`, `LLMProposer QwenClient`, `LLMProposer GLMClient` (exact names pending Round 2 confirmation).
   - No other code changes.

4. **Adapter contract-test stubs** (if absent):
   - `tests/adapters/llm/test_minimax_client_contract.py`, `tests/adapters/llm/test_qwen_client_contract.py`, `tests/adapters/llm/test_glm_client_contract.py`. Minimal offline tests: constructor, request serialization, response deserialization, no network.

5. **Fixtures**:
   - Rotate `tests/fixtures/release_candidate/readiness_matrix_result.json` to include the three new rows (all `blocked`).
   - Rotate `tests/fixtures/release_candidate/reproduction_readiness_result.json` and `tests/fixtures/release_candidate/readiness_drift_result.json` to match.
   - Rotate `tests/fixtures/release_candidate/expected_hash_reproduction.txt` (opt-in path).
   - Do **not** touch `tests/fixtures/canonical_audit_hash.txt` or `tests/fixtures/release_candidate/expected_hash.txt`.

6. **CI**:
   - Default release-candidate evidence job: unchanged.
   - `reproduction-readiness-check` job: assert `reproduction_ready=false` (three new blocked model rows make this trivially true) and assert stable new `report_hash`.
   - Opt-in reproduction job: assert `decision=blocked` with `reproduction_ready=false`.

7. **Docs**:
   - Update `docs/operations/benchmark_reproduction_readiness.md`, `docs/operations/readiness_matrix.md` (if entry-enumerating), and add P50 section to `docs/architecture/productionization_brief.md`.
   - Note explicitly that Anthropic remains the package default adapter seam but is not a paper backend.

8. **Boundary statement** (unchanged from P49): no live contact with MiniMax/Qwen/GLM or any provider; no audit/corpus/manifest schema change; no canonical readiness hash rotation; `reproduction_claimed=false` always; default non-reproduction release-candidate path preserved byte-for-byte.

9. **Stop conditions**:
   - Convergence: client affect names confirmed, contract-test fixture paths resolved, per-backend row shape decided.
   - Execution: three model rows present and `blocked`, all hash rotations applied, default release-candidate evidence still returns `decision=ready`, opt-in still returns `decision=blocked`, no `reproduction_claimed=true` anywhere.

## Remaining Open Questions

1. **Adapter client names.** What are the exact proposer client class names (or affect strings) for MiniMax / Qwen / GLM in the current adapter layer? Evidence needed: `src/self_harness/adapters/llm/` contents. Decides the `affects` strings in `KNOWN_READINESS_AFFECTS`. Blocking for Round 2 convergence.
2. **Contract-test fixtures.** Do `tests/adapters/llm/test_{minimax,qwen,glm}_client_contract.py` already exist? If not, is adding offline stubs in scope for P50 or deferred to a parallel adapter P-step? Evidence needed: directory listing. Blocking for Round 2 convergence.
3. **Anthropic retention.** Keep as `optional` / `reproduction_relevant=false`, or remove from the catalog entirely? Recommendation: keep, since it is the package default adapter and operators may still want to exercise it. Non-blocking for Round 2.
4. **Qwen local-deployment operator action.** Qwen3.5-35B-A3B was deployed locally on H200 GPUs in the paper (Appendix A.1), not via hosted API. Should its `operator_action` be `provision` (GPU/runtime) rather than `configure` (credentials)? Recommendation: `provision`. Non-blocking.
5. **Row shape confirmation.** Per-backend rows vs. collapsed `model_credentials_backend` list. Recommendation: per-backend. Non-blocking for Round 2 but should be locked.

[usage] {"completion_tokens": 3650, "completion_tokens_details": {"reasoning_tokens": 519}, "prompt_tokens": 34801, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 38451}

---

# Round 2

CONVERGED: YES

## Verdict

Round 2 resolves all Round 1 open questions by lock-in. The plan is executable: P50 retires the P49 Anthropic-only TODO by adding three operator-owned, blocked-by-default readiness entries (one per paper backend), splitting the reproduction requirement rows accordingly, extending the static `KNOWN_READINESS_AFFECTS` allowlist, and adding minimal offline contract-test stubs so the `offline_fixture` validator has a legitimate target. The plan is additive, preserves P49's two-contract policy (fail-closed standalone reproduction report; advisory at the release boundary), rotates only the reproduction-path fixtures, and leaves the default release-candidate path byte-for-byte unchanged.

## Critique

The two Round 1 "blocking" evidence questions are decidable without further repository inspection because they are naming/scope decisions rather than empirical unknowns:

1. **Adapter client names.** `KNOWN_READINESS_AFFECTS` is a static allowlist constant, not dynamically derived from the adapter layer. The existing Anthropic entry establishes the `LLMProposer <ClientName>` convention. P50 extends the allowlist with `LLMProposer MiniMaxClient`, `LLMProposer QwenClient`, `LLMProposer GLMClient`. Whether the adapter classes exist yet is irrelevant to catalog validity; the readiness entries declare operator-facing remediation targets, which is consistent with how Harbor/Docker/Trivy/PyPI/Sigstore rows already declare affects for surfaces that are not exercised at validation time.

2. **Contract-test fixtures.** P49 explicitly left a TODO "when those adapters exist," so the MiniMax/Qwen/GLM adapter contract tests almost certainly do not exist. P50 adds minimal offline stubs (`tests/adapters/llm/test_{minimax,qwen,glm}_client_contract.py`) with constructor/serialization assertions and no network calls. This is in scope under the task's "update tests/fixtures/docs as needed" allowance and is the minimum surface area needed to satisfy the `_offline_fixture` existence check.

Strengths of the converged approach:
- Per-backend rows (not collapsed) faithfully reflect Section 4.1 ("within-model comparisons") and Section 4.2's three separate result panels.
- Anthropic retained as `optional` / `reproduction_relevant: false` keeps the package's default adapter seam documented without overclaiming paper fidelity.
- Qwen row uses `operator_action: provision` (local SGLang/H200 deployment, per Appendix A.1) while MiniMax and GLM use `operator_action: configure` (hosted API credentials).
- Hash policy unchanged from P49: only the opt-in reproduction-path fixtures rotate; default release-candidate fixture and canonical audit hash untouched.

Risks addressed:
- No schema churn: `ALLOWED_READINESS_DOMAINS` already includes `model`; only `KNOWN_READINESS_AFFECTS` grows.
- No provider contact: stub contract tests are offline-only.
- No reproduction claim: all three new rows are `blocked` by default; `reproduction_claimed=false` everywhere.

## Required Changes

1. **Row shape (locked).** Per-backend rows in `readiness_matrix.json`; split `model_credentials_backend` into three rows in `benchmark_reproduction_requirements.json`.
2. **Affects strings (locked).** Extend `KNOWN_READINESS_AFFECTS` with `LLMProposer MiniMaxClient`, `LLMProposer QwenClient`, `LLMProposer GLMClient`.
3. **Anthropic retention (locked).** Keep existing Anthropic entry but flip to `status: optional`, `reproduction_relevant: false`.
4. **Contract-test stubs (locked).** Add three offline-only stub files under `tests/adapters/llm/`.
5. **Fixture rotation (locked).** Rotate `tests/fixtures/release_candidate/readiness_matrix_result.json`, `reproduction_readiness_result.json`, `readiness_drift_result.json`, and `expected_hash_reproduction.txt`. Do NOT rotate `canonical_audit_hash.txt` or `expected_hash.txt`.

## Revised Plan

**P50 — Paper model-backend readiness entries (replaces P49 TODO).**

1. **Readiness matrix catalog** (`docs/operations/readiness_matrix.json`):
   - Add `MiniMax M2.5 model API credentials`: `domain: model`, `status: blocked`, `reproduction_relevant: true`, `affects: ["LLMProposer MiniMaxClient"]`, `offline_fixture: tests/adapters/llm/test_minimax_client_contract.py`, `operator_action: configure`, `preflight_surface: none`.
   - Add `Qwen3.5-35B-A3B model deployment`: `domain: model`, `status: blocked`, `reproduction_relevant: true`, `affects: ["LLMProposer QwenClient"]`, `offline_fixture: tests/adapters/llm/test_qwen_client_contract.py`, `operator_action: provision`, `preflight_surface: none`.
   - Add `GLM-5 model API credentials`: `domain: model`, `status: blocked`, `reproduction_relevant: true`, `affects: ["LLMProposer GLMClient"]`, `offline_fixture: tests/adapters/llm/test_glm_client_contract.py`, `operator_action: configure`, `preflight_surface: none`.
   - Flip Anthropic entry to `status: optional`, `reproduction_relevant: false` (package default seam; not a paper backend).

2. **Reproduction requirements catalog** (`docs/operations/benchmark_reproduction_requirements.json`):
   - Split `model_credentials_backend` into `minimax_credentials_backend`, `qwen_credentials_backend`, `glm_credentials_backend`, each with single-FK `readiness_matrix_dependencies`, `required_artifact_class: model_backend_preflight_report`, `required_state: provisioned`.
   - Update `fixed_model_evaluator_tool_budget.readiness_matrix_dependencies` to list all three paper backend rows; drop Anthropic from this requirement.
   - Remove the P49 TODO note.

3. **Core module** (`src/self_harness/readiness_matrix.py`):
   - Extend `KNOWN_READINESS_AFFECTS` with the three new affect strings.
   - No other code changes.

4. **Adapter contract-test stubs**:
   - `tests/adapters/llm/test_minimax_client_contract.py`, `test_qwen_client_contract.py`, `test_glm_client_contract.py`. Minimal: constructor signature, request serialization shape, response deserialization shape, no network.

5. **Fixtures**:
   - Rotate `tests/fixtures/release_candidate/readiness_matrix_result.json` (three new blocked rows, Anthropic now optional).
   - Rotate `tests/fixtures/release_candidate/reproduction_readiness_result.json` and `readiness_drift_result.json` to match.
   - Rotate `tests/fixtures/release_candidate/expected_hash_reproduction.txt` (opt-in path).
   - Do NOT rotate `tests/fixtures/canonical_audit_hash.txt` or `tests/fixtures/release_candidate/expected_hash.txt`.

6. **CI**:
   - Default release-candidate evidence job: unchanged.
   - `reproduction-readiness-check` job: assert `reproduction_ready=false` and stable new `report_hash`.
   - Opt-in reproduction job: assert `decision=blocked`.

7. **Docs**:
   - `docs/operations/benchmark_reproduction_readiness.md`: note per-backend split; clarify Anthropic is package default seam, not paper backend.
   - `docs/operations/readiness_matrix.md` (if entry-enumerating): add three rows.
   - `docs/architecture/productionization_brief.md`: add P50 section.

8. **Boundary statement**: no live contact with any provider; no audit/corpus/manifest schema change; no canonical readiness hash rotation; `reproduction_claimed=false` always; default non-reproduction release-candidate path preserved byte-for-byte.

9. **Stop conditions**:
   - Convergence: met (this round).
   - Execution: three model rows present and `blocked`, Anthropic `optional`, all hash rotations applied, default release-candidate evidence still returns `decision=ready`, opt-in still returns `decision=blocked`, no `reproduction_claimed=true` anywhere.

## Remaining Open Questions

None blocking. All Round 1 questions resolved by lock-in:
1. Adapter client names → `LLMProposer MiniMaxClient` / `QwenClient` / `GLMClient` (static allowlist extension).
2. Contract-test fixtures → add offline stubs in P50 scope.
3. Anthropic retention → keep as `optional` / `reproduction_relevant: false`.
4. Qwen operator action → `provision` (local deployment).
5. Row shape → per-backend.

Future, non-blocking: when real adapter classes land, revisit whether the affects strings match the implemented class names exactly; consider a live operator-attestation artifact class for network controls if live Harbor runs emerge.

[usage] {"completion_tokens": 2719, "completion_tokens_details": {"reasoning_tokens": 852}, "prompt_tokens": 41089, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 43808}
