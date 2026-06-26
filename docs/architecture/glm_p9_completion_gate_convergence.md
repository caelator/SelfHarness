# Round 1

CONVERGED: NO

## Verdict

Round 1 architect review of an initial P9 plan (no candidate supplied, so I am producing it). The repository is at P0–P8 with a real, paper-aligned protocol core, deterministic audit contract, schema-versioned artifacts, optional corpus signatures, a Terminal-Bench/Harbor dry-run boundary with live preflight, and a readiness gate that explicitly forbids reproduction claims without a live run. The largest remaining paper-fidelity gap that is **implementable without Harbor/Docker** is the unbacked LLM proposer seam: the paper's central mechanism is that the same fixed model M that executes the agent also proposes bounded harness edits grounded in evidence, but no production-quality reference provider adapter is shipped and no paper-fidelity tests exercise the LLM proposer path. A secondary gap is observability: audit artifacts do not emit paper-figure-shaped trajectory data (Figure 5/6 evolution plots), so users cannot reproduce paper-style reporting from local runs. The plan below fills both gaps without overreaching into a benchmark-reproduction claim.

Evidence: paper Section 3.3 ("we invoke the same fixed model M with current harness h_t in a proposer role"); paper Section 4.3 (Figures 5/6 trajectory schema); README "Use `LLMProposer` with any provider client implementing complete(...)"; productionization brief "Later slices ... LLM proposer" listed as implemented only at the seam level. Inference: no reference adapter, no LLM-proposer-specific paper-fidelity invariants.

## Critique

The proposed plan is materially paper-faithful and implementable now, but four things need sharpening before it is execution-ready:

1. **Provider scope**: shipping two reference adapters (Anthropic + OpenAI) doubles the dependency surface and CI matrix without doubling paper fidelity. One well-hardened adapter is better than two thin ones. Anthropic is the better single choice: Claude Code is explicitly cited in paper Section 2, and the SDK has stable typed clients.

2. **Diversity enforcement locus**: the paper requires K *mutually distinct* proposals. It is currently unclear whether diversity is enforced inside `LLMProposer`, inside `ProposalPolicy`, or only checked post-hoc by the engine. For paper fidelity, the proposer itself must enforce distinctness at generation time (otherwise K parallel calls may return near-identical edits). The plan must pin this to one location.

3. **Trajectory schema is underspecified**: "shaped like paper Figure 5/6" is not a contract. The plan must commit to explicit field names so the readiness hash fixture and the canonical-deterministic invariant remain meaningful. Without that, the new CLI is untestable for byte-stability.

4. **Evidence-bundle completeness**: paper Section 3.2 specifies the bundle contains "cluster size, representative task instances, shared trace symptoms, verifier evidence, and the inferred agent mechanism." The current `ProposerContext.held_in_patterns` may already carry these, but no invariant test asserts the LLM proposer *renders* them into the user_prompt. Without that assertion, the seam can silently drop evidence and still pass tests.

A fifth, smaller concern: adding a provider SDK, even behind an extra, can destabilize the canonical audit hash fixture if any default code path imports the adapter. The plan must guarantee core-package import does not pull the optional dependency.

## Required Changes

1. Reduce reference adapters to **one**: `self_harness.adapters.llm.anthropic.AnthropicClaudeClient` only. Drop OpenAI from P9.
2. Pin diversity enforcement **inside the LLM proposer**: K parallel proposals whose `(pattern_id, surface, primary_op)` tuples collide are rejected as invalid before reaching the engine's existing merge logic. Add an invariant test.
3. Specify the trajectory JSONL schema explicitly (see Revised Plan). Make `audit-trajectory` output byte-stable under ambient env changes and add it to the canonical-hash invariants.
4. Add an invariant test asserting the LLM proposer user_prompt contains, for each held-in pattern, the pattern id, support (cluster size), at least one representative task id, at least one symptom, and the inferred mechanism. Use a mock client that records its input.
5. Guard against optional-dependency leakage: `python -c "import self_harness"` must succeed without the `anthropic` extra installed; add this to CI.
6. Update the readiness gate so any new canonical artifact layout is reflected in `tests/fixtures/canonical_audit_hash.txt` and the schema changelog.

## Revised Plan

### P9: Paper-faithful LLM proposer reference adapter, evidence-bundle invariants, and trajectory reporting

**Scope (implementable now, no Harbor/Docker):**

1. **`self_harness.adapters.llm.anthropic`**
   - `AnthropicClaudeClient(model: str, *, api_key_env: str = "ANTHROPIC_API_KEY", max_retries: int = 3, on_usage: Callable[[dict], None] | None = None)`
   - Method: `complete(system_prompt: str, user_prompt: str) -> str`
   - Behavior:
     - Reads API key from `api_key_env`; raises `LLMClientError` if missing.
     - Retries with exponential backoff on 429 and 5xx; surfaces 4xx as `LLMRequestError`.
     - Reports input/output token usage via optional `on_usage` callback.
   - Dependency: `anthropic>=0.40,<1.0` declared under `[project.optional-dependencies]` as `anthropic`.
   - No import of `anthropic` at module top; use lazy import inside `__init__` so core package import never requires the extra.

2. **`LLMProposer` paper-fidelity hardening**
   - Enforce diversity: before returning K proposals, drop any whose `(pattern_id, surface, primary_op)` tuple duplicates an already-accepted proposal in the same call. Remaining duplicates are emitted with `status="invalid"` and reason `"diversity_collision"` (engine already handles invalid proposals, but the proposer must be the one to flag them so the audit row carries the right reason).
   - Enforce grounding: each valid proposal must reference a `pattern_id` present in `context.held_in_patterns`. Otherwise it is returned with `status="invalid"`, reason `"ungrounded_proposal"`.
   - Render the full paper-spec evidence bundle into the user_prompt: for each pattern, include `id`, `support`, up to 3 `task_ids`, up to 3 `symptoms`, up to 3 `verifier_evidence`, and the `mechanism`.

3. **`self-harness audit-trajectory` CLI**
   - Usage: `self-harness audit-trajectory <run-dir> [--out trajectory.jsonl] [--pretty]`
   - Default output: `<run-dir>/trajectory.jsonl`
   - Per-round record schema (one JSON object per line):
     ```json
     {
       "schema_version": "1.0",
       "round": 0,
       "harness_before_hash": "...",
       "harness_after_hash": "...",
       "baseline_held_in_passed": 4,
       "baseline_held_out_passed": 2,
       "after_held_in_passed": 5,
       "after_held_out_passed": 2,
       "proposals": [
         {
           "id": "...",
           "status": "accepted|rejected|invalid|merged|superseded",
           "pattern_id": "...",
           "changed_surfaces": ["bootstrap"],
           "primary_op": "ReplaceSurface",
           "score_held_in_delta": 1,
           "score_held_out_delta": 0,
           "decision_reason": "..."
         }
       ],
       "merged": false
     }
     ```
   - Output is produced by reading existing audit artifacts only; no re-execution.
   - Byte-stable: deterministic key ordering, no timestamps, no env-dependent fields.

4. **Paper-fidelity invariants (new tests)**
   - `tests/invariants/test_paper_fidelity_llm_proposer.py`:
     - LLM proposer user_prompt contains the required evidence fields per held-in pattern.
     - Diversity: two mock proposals sharing `(pattern_id, surface, primary_op)` result in exactly one valid + one invalid with reason `diversity_collision`.
     - Grounding: a proposal with a fabricated `pattern_id` is flagged `ungrounded_proposal`.
     - Held-out evidence leakage check still passes when LLM proposer is used.
   - `tests/invariants/test_audit_trajectory_stability.py`:
     - `audit-trajectory` output is byte-identical across `LANG`/`TZ` env variants.
     - `audit-trajectory` output is added to the canonical audit hash fixture calculation if it lives inside the run directory (decision: CLI writes to run dir by default → it is part of the tree → hash must include it; alternatively, write outside run dir by default and only include when `--out` points inside).

5. **Adapter contract tests**
   - `tests/adapters/llm/test_anthropic_client_contract.py`:
     - Mock transport; verify retry on 429, 5xx.
     - Verify `LLMRequestError` on 400.
     - Verify `LLMClientError` when API key missing.
     - Verify `on_usage` callback receives token counts.

6. **Docs**
   - `docs/source/llm_proposer_integration.md`: how to instantiate `AnthropicClaudeClient`, wire into `LLMProposer`, run a demo with a real model key, expected output shape.
   - `docs/source/audit_trajectory.md`: schema, mapping to paper Figure 5/6, example plots.
   - README: new "LLM Proposer Quickstart" section; update "Stable API" to include `AnthropicClaudeClient` and `audit-trajectory`.
   - `docs/architecture/schema_changelog.md`: add `trajectory 1.0` schema entry.

7. **CI / packaging**
   - Add `anthropic` extra to `pyproject.toml`.
   - Add CI job (Python 3.11/3.12/3.13 matrix) that runs `python -c "import self_harness"` without extras installed.
   - Add CI job that installs `[anthropic]` and runs adapter contract tests (mock transport; no real API key).

**Acceptance tests:**
- All new tests above pass.
- `make check` and `make readiness` green on all three Python versions.
- Canonical audit hash fixture updated and rotation policy applied.
- `audit-trajectory runs/demo` produces stable bytes across `LANG`/`TZ` env variants.
- `python -c "import self_harness"` succeeds in a fresh venv without `anthropic` installed.

**Stop conditions:**
- Tests green; readiness gate green; CI green on matrix.
- No new runtime dependencies in the core package (only behind `anthropic` extra).
- README + 2 new docs merged; schema changelog updated.
- Plan is bounded: no Harbor, no Docker, no reproduction claim, no stronger-external-model proposer.

**Explicit non-goals (deferred to P10+):**
- Live Harbor execution and real Terminal-Bench reproduction.
- Multi-model comparison runner.
- Actual subagent/middleware runtime execution (currently declarative surfaces only).
- Stronger-external-model proposer (paper explicitly forbids; not in scope).
- Provider adapters beyond Anthropic.

## Remaining Open Questions

The following are **non-blocking** for execution but should be answered during implementation:

1. **Default location of `trajectory.jsonl`**: inside the run directory (becomes part of audit tree hash) vs. alongside it. Recommendation: write inside the run directory by default so the trajectory is part of the auditable artifact set; update canonical hash fixture accordingly.
2. **`anthropic` SDK version pin**: pin to a minor range (`>=0.40,<1.0`) and let CI surface drift; do not over-pin to a patch.
3. **Whether to expose a no-deps HTTP fallback client**: out of scope for P9; the contract test uses mock transport, so users without the `anthropic` extra can still run the rest of the suite.
4. **Whether `LLMProposer` should support a `seed` for provider-side determinism**: defer; paper does not require deterministic model output, only deterministic audit artifacts, which the engine already guarantees.
5. **Trajectory schema `1.0` vs. reusing audit schema `1.x`**: keep trajectory as a separate, independently versioned schema because it is a derived view, not a primary audit artifact.

These can be resolved by the implementer without another architect round.

[usage] {"completion_tokens": 7359, "completion_tokens_details": {"reasoning_tokens": 4662}, "prompt_tokens": 32370, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 39729}

---

# Round 2

CONVERGED: YES

## Verdict

Round 2 architect review. The Round 1 revised plan is implementation-ready: it targets the correct paper-fidelity gap (unbacked LLM proposer seam + missing trajectory reporting), stays within the implementable-now boundary (no Harbor/Docker), pins the ambiguous design decisions (diversity locus, trajectory schema, evidence-bundle completeness), and hardens the production contract (optional-dependency leakage guard, canonical-hash stability). Evidence from the repository confirms the seam exists (`LLMProposer`, `LLMClient` in stable API; `ProposerContext.held_in_patterns` already carries `FailurePattern` with `support`, `task_ids`, `symptoms`, `verifier_evidence`, `mechanism`) but no reference adapter ships and no invariant tests exercise the LLM path. The plan closes both gaps without overreaching into reproduction claims.

## Critique

The Round 1 plan resolved all five material risks it identified. Two minor implementation details remain that do not block execution:

1. **Exception types**: The plan references `LLMClientError` and `LLMRequestError`. Evidence: `self_harness/exceptions.py` currently exports `PaperFidelityError` and `InvalidPatchError` (visible in test imports). The adapter will need to add these two new exceptions or reuse a generic `LLMClientError` with a `status` field. This is a coding decision, not an architecture decision.

2. **Merge-arm trajectory derivation**: The trajectory schema's `score_held_in_delta`/`score_held_out_delta` per-proposal fields must be derived from each proposal's own candidate evaluation, while the round-level `after_held_in_passed`/`after_held_out_passed` must reflect the committed arm (merged if accepted, else best single, else baseline). The existing `_committed_eval_selector` in `audit.py` already encodes this logic; the CLI should reuse it. Non-blocking.

Both are resolvable during implementation without another round.

## Required Changes

None blocking. The plan from Round 1 is approved as written.

## Revised Plan

No changes from the Round 1 revised plan. Proceed to implementation with the following clarifications (non-blocking):

- Add `LLMClientError` and `LLMRequestError` to `self_harness/exceptions.py` as distinct typed exceptions; export them in the stable API.
- `audit-trajectory` must reuse `audit._committed_eval_selector` (or equivalent logic) to determine which evaluation arm represents the committed round outcome, ensuring per-round `after_*` fields match `audit-summary` output.
- Update `tests/fixtures/canonical_audit_hash.txt` as part of the same PR that adds `trajectory.jsonl` to the run directory; document the fixture rotation in `docs/architecture/schema_changelog.md` under a `trajectory 1.0` entry.
- The `python -c "import self_harness"` CI job must run in an environment where neither `anthropic` nor `provenance` extras are installed, to prove the core package has zero required runtime dependencies beyond the standard library.

## Remaining Open Questions

All five questions from Round 1 remain non-blocking and can be resolved by the implementer:

1. Default location of `trajectory.jsonl`: inside run directory (recommended; becomes part of audit tree hash).
2. `anthropic` SDK pin: `>=0.40,<1.0` minor range.
3. No-deps HTTP fallback: out of scope for P9.
4. `LLMProposer` seed: deferred; paper requires deterministic audit artifacts, not deterministic model output.
5. Trajectory schema versioning: separate `1.0` schema, independently versioned from audit schema.

[usage] {"completion_tokens": 2066, "completion_tokens_details": {"reasoning_tokens": 1285}, "prompt_tokens": 37792, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 39858}
