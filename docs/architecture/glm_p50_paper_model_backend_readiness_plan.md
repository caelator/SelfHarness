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
