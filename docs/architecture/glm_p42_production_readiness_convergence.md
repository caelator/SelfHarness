# Round 1

CONVERGED: YES

## Verdict

The P42 task is a small, well-bounded additive integration of the existing P41 operator policy binding report into the existing P37 release-candidate evidence aggregator. The repository context fully determines the shape of the change: every required seam already exists (a standalone gate script with `ok`/`report_hash`/`reproduction_claimed=false`, an aggregator that already consumes operator promotion preflight output, a Makefile wiring pattern, a fixture-driven CI job, and a committed `expected_hash.txt`). No new schema concept is needed: the aggregator's `gates` array is already heterogeneous and extensible, so the integration is a new gate entry plus a new required CLI input, with no readiness-hash rotation and no audit/corpus schema change. Remaining choices are mechanical and non-blocking.

## Critique

Validated evidence (read directly from repo):

- `scripts/release_candidate_evidence.py` already has a uniform `_json_ok_gate(name, path)` pattern for scanner/operator/harbor artifacts, a dedicated `_audit_verify_gate` that additionally records `report_hash`, a `_reproduction_claim_gate` that iterates a fixed list of input paths, and a top-level `evidence_sha256` that recomputes after gate list construction. [evidence]
- `scripts/operator_policy_binding_verify.py` writes a structured JSON report with `ok`, `report_hash`, and `reproduction_claimed=false`, matching the shape the aggregator expects from other operator gates. [evidence]
- `Makefile` target `release-candidate-evidence` declares explicit dependencies (`provenance vuln-check scanner-check harbor-discovery-check operator-check operator-promotion-check audit-verify`) and constructs the CLI invocation inline; `operator-policy-binding-check` is a separate target that writes `dist/self-harness-operator-policy-binding.json`. [evidence]
- `tests/test_release_candidate_evidence.py` hardcodes fixture defaults and asserts an exact expected hash via `tests/fixtures/release_candidate/expected_hash.txt`; any new required gate will change `evidence_sha256` and require regenerating that file. [evidence]
- `.github/workflows/ci.yml` has a dedicated `release-candidate-evidence` job that invokes the script with explicit fixture paths and an `--expected-hash` check; it must be updated in lockstep with any new required input. [evidence]
- `docs/operations/release_candidate_evidence.md` documents the required gate set and the schema; it must be updated. [evidence]
- `docs/operations/operator_policy_binding.md` already states the deferral: "`make release-candidate-evidence` does not consume this report yet." [evidence]

Inference:

- Schema-version decision: keep release-candidate evidence output `schema_version` at `"1.0"`. The schema is structurally unchanged (still `schema_version`, `ok`, `decision`, `reproduction_claimed`, `gates[]`, `evidence_sha256`, `boundary`); only the contents of `gates[]` grow additively, which is the existing extensibility contract. No readiness-hash rotation is involved because `tests/fixtures/canonical_audit_hash.txt` is untouched; only the evidence aggregator's own `expected_hash.txt` is regenerated.

Risks identified:

1. Evidence-hash drift across PRs if Makefile, script, fixtures, test defaults, CI invocation, and `expected_hash.txt` are not updated atomically. Mitigation: single PR, regenerate hash locally, gate by CI.
2. Mistakenly treating the new gate as optional and silently allowing releases without binding verification. Mitigation: make it required (consistent with operator promotion and operator preflight, which are both required).
3. Accidentally widening `_reproduction_claim_gate`'s scan list inconsistently with the new input. Mitigation: add the new path to the iteration list so a binding report that wrongly claims reproduction also blocks the decision.
4. Implicit schema bump temptation. Mitigation: explicitly keep `"1.0"` and document the additive nature in `release_candidate_evidence.md`.

## Required Changes

1. `scripts/release_candidate_evidence.py`:
   - Add required CLI arg `--operator-policy-binding-result` (type `Path`, required=True), mirroring `--operator-promotion-result`.
   - Insert a new gate via the existing `_json_ok_gate("operator_policy_binding", args.operator_policy_binding_result)` in the `gates` list, placed immediately after `operator_promotion` to keep operator-family gates grouped.
   - Add `args.operator_policy_binding_result` to the path tuple inside `_reproduction_claim_gate` so a binding report that sets `reproduction_claimed=true` is caught.
2. `tests/fixtures/release_candidate/operator_policy_binding_result.json`:
   - Add a committed fixture that is a clean P41 binding report (`{"ok": true, "schema_version": "1.0", "report_hash": "...", "reproduction_claimed": false, ...}`). The exact `report_hash` value must match what `operator_policy_binding_verify.py` produces deterministically for the fixture bundle/promotion pair, so generate it by running `make operator-policy-binding-check` against the existing fixture inputs and copying the output.
3. `tests/fixtures/release_candidate/expected_hash.txt`:
   - Regenerate by running the aggregator locally with all fixture inputs including the new binding fixture; commit the new SHA-256.
4. `tests/test_release_candidate_evidence.py`:
   - Add `"--operator-policy-binding-result"` to the defaults dict in `_run_evidence` (resolving to the new fixture path) so all existing tests continue to exercise the all-pass path.
   - Add two new fail-closed tests mirroring the existing operator-promotion/scanner failure tests: (a) missing-file failure (`--operator-policy-binding-result` pointing at nonexistent path → `operator_policy_binding` gate fails with `missing artifact`, exit code 2, decision `blocked`), and (b) gate-failure case (`{"ok": false}` content → fails with `ok field is not true`).
5. `Makefile`:
   - Add `operator-policy-binding-check` to the dependency list of the `release-candidate-evidence` target (it already writes `dist/self-harness-operator-policy-binding.json`).
   - Add `--operator-policy-binding-result dist/self-harness-operator-policy-binding.json` to the aggregator invocation in that target.
6. `.github/workflows/ci.yml`:
   - In the `release-candidate-evidence` job, add `--operator-policy-binding-result tests/fixtures/release_candidate/operator_policy_binding_result.json` to the script invocation. (No Makefile dependency change needed in CI because that job invokes the script directly with fixtures.)
7. `docs/operations/release_candidate_evidence.md`:
   - Add `operator policy binding result JSON` to the required inputs list.
   - State that the gate must contain `"ok": true` and must not claim reproduction, and that `report_hash` is contributed to gate metadata like other operator gates.
   - Note that output schema remains `1.0` because the `gates` array is the documented extension point.
8. `docs/operations/operator_policy_binding.md`:
   - Replace the deferral sentence ("`make release-candidate-evidence` does not consume this report yet...") with a statement that the binding report is now a required release-candidate evidence gate.
9. `docs/architecture/productionization_brief.md`:
   - Add a P42 entry under implemented work describing the additive release-candidate evidence integration, the schema-version decision (unchanged `1.0`), and the unchanged boundaries (no audit/corpus schema change, no readiness hash rotation, `reproduction_claimed=false` preserved).

## Revised Plan

Execute as a single atomic change set:

1. Add the new required CLI argument and gate to `scripts/release_candidate_evidence.py`; extend `_reproduction_claim_gate`'s scan list to include the new path.
2. Add the clean fixture `tests/fixtures/release_candidate/operator_policy_binding_result.json` produced from the existing operator-policy-binding fixture pair (deterministic `report_hash`).
3. Regenerate `tests/fixtures/release_candidate/expected_hash.txt` by running the aggregator with the full fixture set including the new input.
4. Update `tests/test_release_candidate_evidence.py` defaults and add the two fail-closed tests.
5. Wire the Makefile `release-candidate-evidence` target to depend on `operator-policy-binding-check` and pass its output.
6. Update the CI `release-candidate-evidence` job's direct script invocation with the new fixture path.
7. Update the two operations docs and the productionization brief.

Stop conditions for the loop: pass `make check`, `make readiness`, `make release-candidate-evidence` with regenerated `expected_hash.txt`, and the dedicated CI `release-candidate-evidence` job's `--expected-hash` check. No live validation, no network, no readiness-hash rotation, no schema bump, no audit/corpus schema change.

## Remaining Open Questions

None blocking. Two minor items the implementer may decide locally without further architect input:

- Whether to also surface `report_hash` in the new gate's metadata (mirroring `_audit_verify_gate`). Recommended: yes, for symmetry with audit_integrity and to aid operator triage; the existing `_json_ok_gate` helper can be extended inline or replaced with a small `_operator_policy_binding_gate` helper modeled on `_audit_verify_gate`.
- Whether to commit a second fixture under `tests/fixtures/operator_policy_binding/` representing the binding report's source bundle/promotion pair if one does not already exist; the existing `tests/fixtures/operator_bundle/valid.json` and `tests/fixtures/operator_promotion/valid.json` already drive `make operator-policy-binding-check`, so no new source fixture should be required.

[usage] {"completion_tokens": 2644, "completion_tokens_details": {"reasoning_tokens": 592}, "prompt_tokens": 15254, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 17898}
