# Round 1

CONVERGED: YES

## Verdict

The highest-value P12 slice executable without Harbor/Docker is **LLM Proposer Engine-Loop Integration Hardening**: prove the P3/P9 provider-neutral proposer seam functions correctly inside the full `SelfHarnessEngine` loop (not only in isolation), add a deterministic mock-client-driven canonical audit fixture, and lock in paper-fidelity invariants for proposer-context rendering. This is concrete code, requires no API keys or containers, directly de-risks the most likely real-world usage path, and preserves the existing no-false-reproduction contract. Material risks are addressed below; remaining questions are implementer-discretion and non-blocking.

## Critique

Initial inclination was to pursue paper Figure 5/6/10-style trajectory reporting or parallel-proposal diversity enforcement. Both are valuable, but:

- **Trajectory reporting** risks being partially docs/visualization work unless paired with schema changes, and the README already states `audit-trajectory` derives a "stable paper-style evolution trace." Without evidence that the current output is insufficient, this is lower-value.
- **Parallel-proposal diversity** depends on current `HeuristicProposer` behavior I cannot validate from the supplied context (proposer.py not provided). Enforcing a diversity invariant without knowing the baseline risks redundant work or breaking the canonical hash fixture.
- **LLM proposer loop hardening** is the seam real users exercise, has no dependency on unprovided files (the contract is documented in README), and the P11 brief explicitly lists "remaining production work" as integration-adjacent. A deterministic mock-client end-to-end run closes the gap between "adapter exists" and "adapter works in the engine loop."

The critique weakens the first two candidates and strengthens the third.

## Required Changes To The Initial Sketch

1. **No schema bump.** Initial sketch considered a trajectory schema v1.1. Drop it—this slice must not perturb the canonical audit hash or schema changelog. Use existing schema 1.4.
2. **Mock client must be deterministic and hash-stable.** Any canonical fixture using the mock must be invariant under `LANG`/`TZ` perturbation, matching the existing `test_canonical_audit_hash_is_stable_under_ambient_environment_changes` discipline.
3. **No new CLI command in this slice.** A `--mock-llm` demo flag is tempting but expands surface area. Keep the mock in test infrastructure; add CLI later if requested.
4. **Explicit no-reproduction invariant coverage.** The mock-driven audit must still be unable to set `reproduction_claimed=true` under `benchmark_protocol="terminal-bench@2.0"`. Add an invariant test proving the gate fires on the LLM path too, not only the heuristic path.
5. **Proposer-context evidence-only invariant must cover the LLM renderer.** P9 added proposer-side invalid reasons for ungrounded suggestions, but the engine-loop test must assert that held-out data never enters the rendered prompt, not just that it never enters `ProposerContext`.

## Revised Plan

**P12: LLM Proposer Engine-Loop Integration Hardening**

Scope (concrete code):

1. **`self_harness.testing.MockLLMClient`** (or under `tests/` if you prefer private): implements `LLMClient.complete(system_prompt, user_prompt) -> str` with a deterministic, seeded response generator that emits valid JSON proposals grounded in provided held-in pattern ids. No network, no optional extras.
2. **Engine-loop integration test**: construct `SelfHarnessEngine` with `LLMProposer(MockLLMClient(seed=...))`, run 1–2 rounds against `demo_tasks()`, assert:
   - At least one valid proposal is emitted and evaluated.
   - Audit `proposals.jsonl` contains LLM-sourced rows with proposer-rendered evidence fields.
   - `evaluations.jsonl` is well-formed under schema 1.4.
   - The run terminates without `PaperFidelityError`.
3. **Canonical LLM-driven audit fixture + hash**: under `tests/fixtures/canonical_llm_audit_hash.txt`, record the `audit_tree_hash` of a fixed mock-driven run. Add `test_canonical_llm_audit_hash_matches_fixture` mirroring the existing canonical-hash test discipline. Rotate policy follows `RELEASE.md`.
4. **Paper-fidelity invariant expansion** in `tests/invariants/test_paper_fidelity_invariants.py`:
   - `test_llm_proposer_context_renders_held_in_evidence_only`: render a `ProposerContext` containing a decoy held-out pattern/summary, assert the rendered `system_prompt`/`user_prompt` text does not contain held-out task ids, pattern ids, or held-out trace messages.
   - `test_llm_driven_terminal_bench_audit_cannot_claim_reproduction`: run the LLM path with `benchmark_protocol="terminal-bench@2.0"` and assert `reproduction_claimed=true` raises `PaperFidelityError` (closes the invariant on the LLM branch).
   - `test_llm_proposer_rejects_ungrounded_pattern_id`: configure the mock to emit a fabricated pattern id not present in the context; assert the proposal is audited as `invalid` with a proposer-side reason and never promoted.
5. **Docs**: add `docs/architecture/p12_llm_loop_hardening_brief.md` recording scope, the no-reproduction stance, the mock-client contract, and deferral of real-provider validation. Update `productionization_brief.md` "Implemented" section once landed. README stable-API section already lists `LLMClient`, `LLMProposer`, `LLMClientError`, `LLMRequestError`—no README change required unless the mock is promoted to public `self_harness.testing`.

Stop conditions (all must hold):

- `make check` and `make readiness` pass on Python 3.11/3.12/3.13.
- Canonical LLM audit hash fixture committed and invariant test green.
- Three new paper-fidelity invariant tests green.
- No schema changelog edit; no `reproduction_claimed=true` path reachable.
- `productionization_brief.md` updated with P12 entry under "Implemented."

Schema/version implications:

- **None.** Reuses schema 1.4 and trajectory schema 1.0. No `SUPPORTED_SCHEMA_VERSIONS` change. No `audit_schema_policy.md` edit.

Deferred until a provisioned host exists (or API keys available):

- Real Anthropic Claude end-to-end loop validation (requires `anthropic` extra and live key).
- Capturing a real-provider canonical audit hash.
- Validating proposer behavior under non-deterministic model output.
- Any Terminal-Bench-protocol reproduction claim.

## Remaining Open Questions

- Should `MockLLMClient` live under `self_harness/testing/` (importable by downstream users) or `tests/` (private)? **Non-blocking.** Recommend `self_harness/testing/` if the stable API intends to support third-party adapter authors; otherwise `tests/`.
- Should the mock support configurable "invalid JSON" / "missing field" failure modes for negative-path tests? **Non-blocking.** Add only if the three invariants above require it; otherwise defer to P13.
- Is a CLI `--mock-llm` demo flag desired for interactive exploration? **Non-blocking.** Not in this slice; revisit if user-facing demos are requested.

[usage] {"completion_tokens": 4232, "completion_tokens_details": {"reasoning_tokens": 2678}, "prompt_tokens": 39133, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 43365}
