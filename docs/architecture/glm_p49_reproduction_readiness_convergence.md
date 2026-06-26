# Round 1

CONVERGED: NO

## Verdict

The prior `glm_p49_reproduction_readiness_plan.md` correctly identified the additive opportunity but violated the controlling requirement: it wired `reproduction_ready=true` as a *required* gate inside `scripts/release_candidate_evidence.py`. Given the documented invariant that a valid release-candidate decision may coexist with `live_execution_blocked=true` (see `docs/operations/release_candidate_evidence.md` and `docs/operations/readiness_matrix.md`), that wiring would permanently block every package/operator release while Harbor/Docker/model infra remains unprovisioned. That is the exact contract conflict the task asks us to resolve. Round 1 is therefore not converged; the plan must be revised so the new artifact is machine-readable, fail-closed on the *paper reproduction* contract, and advisory (or absent) on the *non-reproduction release* contract.

## Critique

1. **Contract violation in the prior plan.** The release-candidate evidence aggregator is the wrong control point for a hard `reproduction_ready=true` requirement. Existing docs (P45-P48) intentionally separate "release/operator evidence" from "benchmark reproduction evidence." Making `reproduction_ready=true` required inside release-candidate evidence silently upgrades every package release into a reproduction claim.
2. **Correct instinct: separate artifact.** The prior plan's `benchmark_reproduction_requirements.json` catalog and standalone `scripts/reproduction_readiness_report.py` are the right shape. They should remain.
3. **Correct instinct: fail-closed on its own contract.** The standalone CLI should still exit 2 when `reproduction_ready=false`, because that is the honest paper-reproduction answer.
4. **Wrong instinct: required aggregator gate.** Release-candidate evidence must not require the report to be `ready`. Two acceptable options, in order of preference:
   - **(A) Advisory metadata only.** If a report is supplied, record `reproduction_ready`, `report_hash`, and boundary language in gate metadata, but only *fail* on missing-file, malformed-shape, schema mismatch, or `reproduction_claimed=true`. A not-ready-but-well-formed report is advisory.
   - **(B) Separate command.** Add `scripts/reproduction_candidate_evidence.py` and `make reproduction-candidate-evidence` as a distinct, optional operator surface. Do not touch `release_candidate_evidence.py` at all. Round 1 should pick one; recommend (A) because it keeps a single aggregator while preserving the non-reproduction path, and it matches the existing pattern used for `live_execution_blocked`.
5. **Hash policy.** Adopting (A) rotates the release-candidate evidence fixture hash (input set changes). The canonical paper-fidelity readiness hash must not rotate. This is consistent with P46-P48.
6. **Naming.** `benchmark_reproduction_readiness` is fine; keep the CLI target `reproduction-readiness-check` and the gate name `reproduction_readiness` to distinguish from generic `readiness_matrix` / `readiness_drift`.

## Required Changes

1. Drop the requirement that `release_candidate_evidence.py` fail when `reproduction_ready=false`.
2. Define the aggregator gate semantics as: required input only when `--reproduction-readiness-result` is passed OR a future flag `--require-reproduction-readiness` is set; in the default path, if supplied, the gate fails only on missing file, invalid JSON, `schema_version != "1.0"`, missing/invalid `report_hash`, or `reproduction_claimed=true`; `reproduction_ready=false` is recorded in metadata and does not block.
3. Keep the standalone `reproduction_readiness_report.py` fail-closed on its own contract (exit 2 when `reproduction_ready=false`, exit 3 on corrupt inputs).
4. Make the Makefile wiring explicit: `make reproduction-readiness-check` produces the report; `make release-candidate-evidence` references it but does not depend on it by default, to preserve the non-reproduction path. Add a documented `make release-candidate-evidence-reproduction` (or `RELEASE_REQUIRE_REPRODUCTION_READINESS=1`) escape hatch for operators who want the hard gate.
5. Tests must cover both the advisory path (not-ready does not block release-candidate evidence) and the explicit hard-gate path.
6. Docs must state both contracts: paper-reproduction contract (fail-closed) and release/operator contract (advisory unless operator opts in).

## Revised Plan

**P49 — Benchmark reproduction readiness mapping (advisory at the release boundary, fail-closed on its own contract).**

1. **Catalog.** Add `docs/operations/benchmark_reproduction_requirements.json`, schema `1.0`. Each row: `requirement_id`, `paper_reference`, `description`, `readiness_matrix_dependency` (FK into `readiness_matrix.json`), `required_artifact_class`, `required_state` (`provisioned`), `notes`. Rows enumerated in the prior plan (fixed Terminal-Bench split, two repeats, fixed model/evaluator/tool budget, Harbor execution, image trust, model credentials, network caps, live artifact ingest, no held-out leakage, release-evidence binding).

2. **Core module.** Add `self_harness/reproduction_readiness.py`:
   - `ReproductionRequirement`, `ReproductionReadinessReport` dataclasses.
   - `evaluate_reproduction_readiness(requirements_catalog, readiness_matrix_report, artifact_index)`:
     - For each row: fail if FK missing, if matrix entry is not `provisioned`, or if no non-empty artifact of `required_artifact_class` is present.
     - Reject any input or output containing `reproduction_claimed=true`.
     - Return `reproduction_ready: bool`, per-row status, deterministic `report_hash`, `reproduction_claimed=false`, `boundary`.

3. **CLI.** Add `scripts/reproduction_readiness_report.py`:
   - Inputs: `--requirements`, `--readiness-matrix-result`, `--audit-verify-result`, optional `--artifact-dir`.
   - Output: `dist/self-harness-reproduction-readiness.json`, schema `1.0`.
   - Exit codes: `0` ready, `2` not-ready, `3` corrupt inputs.

4. **Makefile.**
   - Add `reproduction-readiness-check: readiness-matrix audit-verify` writing `dist/self-harness-reproduction-readiness.json`.
   - Do **not** add it as a prerequisite of `release-candidate-evidence`.
   - Add opt-in target `release-candidate-evidence-reproduction: release-candidate-evidence reproduction-readiness-check` that re-runs the aggregator with `--require-reproduction-readiness`.

5. **Aggregator wiring (Option A — advisory).** Extend `scripts/release_candidate_evidence.py`:
   - New optional `--reproduction-readiness-result` argument.
   - New optional `--require-reproduction-readiness` flag.
   - New gate `reproduction_readiness`:
     - If argument absent: gate is `skipped`, required=false.
     - If present: required=true. Fail on missing file, invalid JSON, `schema_version != "1.0"`, missing/malformed `report_hash`, or `reproduction_claimed=true`.
     - If `--require-reproduction-readiness` is set: also fail when `reproduction_ready != true`.
     - Otherwise record `reproduction_ready` in metadata; do not fail the decision.
   - Rotates `tests/fixtures/release_candidate/expected_hash.txt`. Do not touch `tests/fixtures/canonical_audit_hash.txt`.

6. **CI.**
   - Add a job for `reproduction-readiness-check` asserting deterministic `reproduction_ready=false` and stable `report_hash` against current fixtures.
   - Existing release-candidate evidence fixture job should pass `--reproduction-readiness-result` to exercise the advisory path and assert `decision=ready` despite `reproduction_ready=false`.
   - Optional separate job running with `--require-reproduction-readiness` asserting `decision=blocked`.

7. **Docs.**
   - Add `docs/operations/benchmark_reproduction_readiness.md` describing paper-requirement mapping, fail-closed standalone contract, and advisory release-boundary contract.
   - Update `docs/operations/release_candidate_evidence.md` with the new optional input, the `--require-reproduction-readiness` escape hatch, and the explicit "non-reproduction release path preserved" note.
   - Add P49 section to `docs/architecture/productionization_brief.md`.

8. **Tests.** Cover: clean not-ready standalone report (exit 2); corrupt inputs (exit 3); `reproduction_claimed=true` rejection; synthetic all-provisioned path (exit 0); aggregator advisory path (supplied report, `reproduction_ready=false`, decision still `ready`); aggregator hard-gate path (decision `blocked`); aggregator rejection of malformed/claiming report.

9. **Boundary statement** (code comments + report output): no live Harbor/Docker/Trivy/PyPI/Sigstore/registry/scanner DB/model contact; no audit schema change; no corpus schema change; no manifest schema change; no canonical readiness hash rotation; `reproduction_claimed=false` always.

## Remaining Open Questions

1. **Default Makefile posture.** Should `make release-candidate-evidence` pass `--reproduction-readiness-result` by default (advisory visibility) or omit it entirely until an operator opts in? Recommendation: pass it by default for visibility, with `--require-reproduction-readiness` available as the hardening switch. Decides fixture hash and CI shape; non-blocking for round 2.
2. **Model backend binding.** Bind `model_credentials_backend` to the existing Anthropic readiness entry, or add MiniMax/Qwen/GLM entries? Recommendation: bind to Anthropic for P49 with a TODO. Non-blocking.
3. **Network controls representation.** Operator attestation artifact class vs Harbor readiness binding? Recommendation: Harbor binding for P49. Non-blocking.
4. **Operator escape-hatch naming.** `--require-reproduction-readiness` vs a separate `reproduction-candidate-evidence` subcommand (Option B). Recommendation: flag-based Option A for now; revisit if operators want a separate signed artifact. Non-blocking.

[usage] {"completion_tokens": 2557, "completion_tokens_details": {"reasoning_tokens": 384}, "prompt_tokens": 20307, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 22864}

---

# Round 2

CONVERGED: YES

## Verdict

Round 1's revised plan correctly resolves the original contract conflict by introducing a standalone, fail-closed reproduction-readiness artifact and wiring it into the release-candidate aggregator as an *advisory* gate with an operator opt-in hardening flag. The plan preserves the documented invariant that a valid release-candidate decision may coexist with `live_execution_blocked=true` (per `docs/operations/release_candidate_evidence.md` and `docs/operations/readiness_matrix.md`), while still adding the requested machine-readable paper-reproduction readiness mapping. Round 1's open questions are resolvable without further evidence; locking them here yields an executable plan.

## Critique

Strengths confirmed:
- The two-contract split is correct: paper-reproduction contract is fail-closed (standalone CLI exits 2 when not ready); release/operator contract is advisory unless an operator opts in. This matches the existing treatment of `live_execution_blocked` as operator information rather than a release-candidate failure.
- The standalone artifact shape (`benchmark_reproduction_requirements.json` catalog + `reproduction_readiness_report.py` CLI + `self_harness/reproduction_readiness.py` core module) is additive and does not rotate the canonical audit hash, consistent with P45-P48 policy.
- The aggregator extension follows the existing optional-gate pattern (mirrors `--attestation-result`), so no schema bump is needed for release-candidate evidence (schema `1.0` `gates` array remains the extension point).
- The `reproduction_claimed=false` invariant is enforced both in the standalone report output and as a hard aggregator failure on any input or output claiming reproduction, matching P39-P48 conventions.

Risks addressed:
- The Round 1 plan that made `reproduction_ready=true` a required aggregator gate is explicitly dropped. The advisory semantics mean a not-ready-but-well-formed report does not block package/operator release.
- Hash policy is explicit: only the release-candidate evidence fixture hash rotates *if* the default path passes the report; canonical paper-fidelity readiness hash is untouched.

One ambiguity to lock down before execution (Round 1 open question #1): whether the default `make release-candidate-evidence` passes `--reproduction-readiness-result`. Evidence from `docs/operations/release_candidate_evidence.md` shows the default release path intentionally tolerates `live_execution_blocked=true`; passing reproduction-readiness by default would couple the default fixture to reproduction material and force fixture rotation. The task requirement is to "preserve the existing non-reproduction package release path unless there is a compelling reason to break it" — no such reason exists. Decision: do **not** pass it by default.

## Required Changes

Round 1 plan is accepted with the following lock-ins and minor clarifications:

1. **Makefile posture (locked).** The default `make release-candidate-evidence` target must **not** pass `--reproduction-readiness-result` and must **not** depend on `reproduction-readiness-check`. This preserves the existing fixture hash and the non-reproduction release path byte-for-byte. Advisory visibility is available by running `make reproduction-readiness-check` separately and invoking the script directly with `--reproduction-readiness-result`, or via the opt-in `make release-candidate-evidence-reproduction` target.
2. **Opt-in hardening target (locked).** Add `release-candidate-evidence-reproduction: release-candidate-evidence reproduction-readiness-check` that re-invokes `scripts/release_candidate_evidence.py` with both `--reproduction-readiness-result` and `--require-reproduction-readiness`, writing a separate `dist/self-harness-release-candidate-evidence-reproduction.json`. This keeps the default release artifact pristine.
3. **Fixture hash (locked).** Because the default aggregator invocation does not change, `tests/fixtures/release_candidate/expected_hash.txt` does **not** rotate. A new fixture `tests/fixtures/release_candidate/expected_hash_reproduction.txt` (or equivalent) covers the opt-in path. `tests/fixtures/canonical_audit_hash.txt` remains untouched.
4. **Gate semantics (locked, restating Round 1).** In `scripts/release_candidate_evidence.py`:
   - Add optional `--reproduction-readiness-result` and optional `--require-reproduction-readiness`.
   - If the result argument is absent: gate `reproduction_readiness` is `skipped`, `required=false`.
   - If present: `required=true`. Fail on missing file, invalid JSON, `schema_version != "1.0"`, missing/malformed `report_hash`, or any `reproduction_claimed=true`. Record `reproduction_ready` and `report_hash` in metadata.
   - If `--require-reproduction-readiness` is also set: additionally fail when `reproduction_ready != true`.
5. **Catalog content (locked, restating Round 1).** `docs/operations/benchmark_reproduction_requirements.json` schema `1.0` with the ten rows enumerated in Round 1. Bind `model_credentials_backend` to the existing Anthropic readiness entry with a TODO for MiniMax/Qwen/GLM. Bind `network_resource_controls` to the Harbor readiness entry.
6. **Boundary statement (locked).** No live Harbor/Docker/Trivy/PyPI/Sigstore/registry/scanner DB/model contact; no audit/corpus/manifest schema change; no canonical readiness hash rotation; `reproduction_claimed=false` always.

## Revised Plan

**P49 — Benchmark reproduction readiness mapping (advisory at the release boundary, fail-closed on its own contract, default path unchanged).**

1. **Catalog.** Add `docs/operations/benchmark_reproduction_requirements.json`, schema `1.0`. Columns: `requirement_id`, `paper_reference`, `description`, `readiness_matrix_dependency` (FK into `readiness_matrix.json`), `required_artifact_class`, `required_state` (`provisioned`), `notes`. Rows: `terminal_bench_fixed_split`, `two_repeated_attempts`, `fixed_model_evaluator_tool_budget`, `harbor_execution`, `docker_container_image_trust`, `model_credentials_backend` (Anthropic, TODO MiniMax/Qwen/GLM), `network_resource_controls` (Harbor binding), `live_artifact_ingest`, `no_held_out_leakage` (binds `audit_verify_report`), `release_evidence_binding` (PyPI + Sigstore).

2. **Core module.** Add `self_harness/reproduction_readiness.py`:
   - `ReproductionRequirement`, `ReproductionReadinessReport` dataclasses.
   - `evaluate_reproduction_readiness(requirements_catalog, readiness_matrix_report, artifact_index)`:
     - For each row: fail if FK missing, matrix entry not `provisioned`, or no non-empty artifact of `required_artifact_class`.
     - Reject any input/output containing `reproduction_claimed=true`.
     - Return `reproduction_ready`, per-row status, deterministic `report_hash`, `reproduction_claimed=false`, `boundary`.

3. **CLI.** Add `scripts/reproduction_readiness_report.py`:
   - Inputs: `--requirements`, `--readiness-matrix-result`, `--audit-verify-result`, optional `--artifact-dir`.
   - Output: `dist/self-harness-reproduction-readiness.json`, schema `1.0`.
   - Exit codes: `0` ready, `2` not-ready, `3` corrupt inputs.

4. **Makefile.**
   - `reproduction-readiness-check: readiness-matrix audit-verify` → writes `dist/self-harness-reproduction-readiness.json`.
   - `release-candidate-evidence` unchanged (no new prereq, no new arg).
   - `release-candidate-evidence-reproduction: release-candidate-evidence reproduction-readiness-check` → re-invokes aggregator with `--reproduction-readiness-result dist/self-harness-reproduction-readiness.json --require-reproduction-readiness --out dist/self-harness-release-candidate-evidence-reproduction.json`.

5. **Aggregator wiring.** Extend `scripts/release_candidate_evidence.py`:
   - Optional `--reproduction-readiness-result`; optional `--require-reproduction-readiness`.
   - New gate `reproduction_readiness` per semantics in Required Changes §4.
   - Default fixture hash unchanged. New fixture for opt-in path.

6. **CI.**
   - `reproduction-readiness-check` job asserting deterministic `reproduction_ready=false` and stable `report_hash`.
   - Default release-candidate evidence fixture job unchanged (no new arg).
   - Opt-in job running `release-candidate-evidence-reproduction` asserting `decision=blocked` with `reproduction_ready=false`.

7. **Docs.**
   - Add `docs/operations/benchmark_reproduction_readiness.md` (paper mapping, fail-closed standalone contract, advisory release-boundary contract, default-path-unchanged note).
   - Update `docs/operations/release_candidate_evidence.md` describing the optional input, the opt-in flag, and the explicit preservation of the non-reproduction release path.
   - Add P49 section to `docs/architecture/productionization_brief.md`.

8. **Tests.** Cover: standalone not-ready (exit 2); corrupt inputs (exit 3); `reproduction_claimed=true` rejection; synthetic all-provisioned path (exit 0); aggregator with no report (gate skipped, decision ready); aggregator advisory path (supplied report, not-ready, decision still ready); aggregator hard-gate path (decision blocked); aggregator rejection of malformed/claiming report.

9. **Boundary statement** in code comments and report output: no live contact, no schema changes, no canonical hash rotation, `reproduction_claimed=false` always.

## Remaining Open Questions

None blocking. The four Round 1 open questions are resolved:
1. Default Makefile posture → do not pass `--reproduction-readiness-result` by default (locked above).
2. Model backend binding → Anthropic entry with TODO (locked above).
3. Network controls → Harbor binding (locked above).
4. Escape-hatch naming → flag-based Option A via `--require-reproduction-readiness` plus a separate Makefile target (locked above).

Future, non-blocking considerations: revisit MiniMax/Qwen/GLM readiness entries when adapters exist; revisit network-control attestation artifact class if live Harbor runs emerge; revisit a separate signed reproduction-candidate artifact if operators want stronger custody than advisory metadata.

[usage] {"completion_tokens": 3154, "completion_tokens_details": {"reasoning_tokens": 965}, "prompt_tokens": 24677, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 27831}
