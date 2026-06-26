CONVERGED: YES

## Verdict

Execution-ready. The P56 readiness-promotion and P54/P55 reproduction-bundle gates already exist in `release_candidate_evidence.py` as opt-in advisory inputs; the missing work is purely wiring them into the default release-evidence path as first-class advisory gates, rotating the release/operator fixture hash, and updating CI + docs. No schema churn, no live-service contact, no reproduction-claim risk because the default-path promotion report is a baseline=candidate noop and the bundle remains absent/skipped on the non-reproduction release path.

## Critique

Evidence (from repo):
- `scripts/release_candidate_evidence.py` already supports `--readiness-promotion-result` (advisory, `required=False`, status `pass|advisory|fail`) and `--reproduction-bundle-result` (skipped when not supplied and not required).
- `Makefile`'s `release-candidate-evidence` target does NOT depend on `readiness-promotion-check` and does NOT pass `--readiness-promotion-result`.
- CI `release-candidate-evidence` job runs the aggregator with fixtures but omits both promotion and bundle inputs.
- Release workflow runs `make release-candidate-evidence`, so the gap propagates to tagged releases.
- `tests/test_release_candidate_evidence.py` covers the advisory promotion path and the bundle path via fixtures, but the default-args fixture (`expected_hash.txt`) does not include a promotion gate.

Inference: making the gates "first-class" means (a) the default release-evidence artifact visibly carries the promotion admission result as an advisory gate, and (b) CI/release exercise the noop baseline=candidate promotion check so the gate is never silently absent. The bundle cannot be made first-class on the default path because no operator-supplied live artifacts exist in CI or the default release; it correctly remains skipped/advisory and only becomes required on `--require-reproduction-readiness`.

Risk: rotating `tests/fixtures/release_candidate/expected_hash.txt` is required and permitted by the schema-changelog policy (release/operator fixture, not the canonical readiness hash). The canonical audit hash in `tests/fixtures/canonical_audit_hash.txt` must not move.

## Required Changes

1. Make `make release-candidate-evidence` depend on `readiness-promotion-check` and pass `--readiness-promotion-result dist/self-harness-readiness-promotion.json` to the aggregator. Use `READINESS_BASELINE_CATALOG` and `READINESS_CANDIDATE_CATALOG` both defaulting to `docs/operations/readiness_matrix.json` so the default path is a documented noop admission.
2. Update `.github/workflows/ci.yml` `release-candidate-evidence` job to pass the fixture `tests/fixtures/release_candidate/readiness_promotion_result.json` (already referenced by tests) and assert the gate appears with `required=false` and status `pass|advisory`.
3. Update `tests/fixtures/release_candidate/expected_hash.txt` to the new aggregator hash and add a regression test asserting the default release path emits a `readiness_promotion` advisory gate.
4. Leave the reproduction bundle out of the default `release-candidate-evidence` target and out of the default CI/release invocation; document that operators supply it only via `release-candidate-evidence-reproduction`.
5. Add a `Release-Candidate Evidence Inputs 1.0` changelog entry noting the advisory promotion gate is now part of the default release-evidence input set and that only the release/operator fixture hash rotates.
6. Confirm `tests/fixtures/canonical_audit_hash.txt` is unchanged.

## Revised Plan

Slice P57: Readiness-promotion advisory gate promotion to default release evidence.

- `Makefile`: `release-candidate-evidence` prereq adds `readiness-promotion-check`; aggregator invocation adds `--readiness-promotion-result dist/self-harness-readiness-promotion.json`. No change to `release-candidate-evidence-reproduction` semantics.
- `scripts/release_candidate_evidence.py`: no code change required; the advisory gate path is already correct. (Optional polish: emit `reproduction_bundle` as an explicit `skipped` gate on the default path for visibility. Conservative choice: do not change, to keep the hash rotation minimal.)
- CI: extend the `release-candidate-evidence` job with `--readiness-promotion-result tests/fixtures/release_candidate/readiness_promotion_result.json` and a Python assertion block that the gate exists, is not required, and does not claim reproduction.
- Release workflow: inherits the change via `make release-candidate-evidence`; no direct edit needed.
- Fixtures: regenerate `tests/fixtures/release_candidate/expected_hash.txt`. Add `tests/fixtures/release_candidate/readiness_promotion_result.json` if not already present (it is referenced by tests; verify existence).
- Tests: add `test_release_candidate_evidence_default_path_includes_readiness_promotion_advisory_gate` to `tests/test_release_candidate_evidence.py`.
- Docs: append `Release-Candidate Evidence Inputs 1.0` entry to `docs/architecture/schema_changelog.md` and a P57 bullet to `docs/architecture/productionization_brief.md` stating: default release path now emits the readiness-promotion advisory gate via a baseline=candidate noop comparison; bundle admission remains operator-only; canonical readiness hash unchanged; no live contact; no reproduction claim.

Non-goals (explicit): wiring bundle admission into the default release path; making promotion a hard required gate; changing audit/corpus/manifest schemas; rotating the canonical readiness hash; adding live Harbor/Docker/Trivy/PyPI/Sigstore/model contact.

## Remaining Open Questions

- Should the default path also emit an explicit `skipped` reproduction-bundle gate for visibility, accepting an additional fixture hash rotation? Non-blocking; conservative plan omits this. Deciding factor: operator feedback on whether a visible "skipped" entry aids release-evidence triage versus noise.
