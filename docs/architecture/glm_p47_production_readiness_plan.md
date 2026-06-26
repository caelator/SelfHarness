CONVERGED: YES

## Verdict

The round 1 revised plan is ready to execute. It correctly narrows the P47 slice to closing P45 deferred Q2 (cross-reference readiness catalog with operator preflight outputs to detect declarative drift), uses an additive catalog schema bump with safe defaults, scopes the drift detector to operator-actionable *provisioned + reproduction-relevant* entries, avoids the 1:N preflight-check mapping trap by introducing a coarse `preflight_surface` enum, and keeps release-smoke status wrapping explicitly out of scope. All remaining open questions are non-blocking and appropriately deferred. Material risks (false drift, schema-coupling inflation, canonical audit hash rotation) are addressed.

## Critique

- **Evidence:** The existing offline gate stack (`release-candidate-evidence`, `operator-check`, `scanner-check`, `harbor-discovery-check`) already emits the JSON artifacts the drift detector will consume. CI already fixtures `operator_preflight_result.json`, `scanner_result.json`, `harbor_discovery_result.json` under `tests/fixtures/release_candidate/`. No new live dependencies are introduced.
- **Inference:** The catalog schema bump from `1.0` to `1.1` is additive (two optional enum fields with defaults), so existing catalog entries continue to load. The `report_hash` rotation on the readiness matrix report is the expected and permitted side effect, already called out as distinct from `canonical_audit_hash.txt`.
- **Strength:** The drift model correctly fails closed *only* for `provisioned + reproduction_relevant` entries with missing/malformed/failed named surfaces. Blocked and optional entries emit advisory only. This matches the operator-visibility-not-release-failure design from P46.
- **Strength:** Introducing `operator_action` as machine-readable metadata now (even though promotion-lifecycle gating is deferred) avoids a second schema bump later. Cheap and disciplined.
- **Risk accepted:** PyPI/release-smoke preflight surface is deferred because release-smoke output is a directory tree, not a JSON status wrapper. This is correctly bounded as a non-goal.
- **No unaddressed blocking risks.**

## Required Changes

None blocking. Execution must enforce:

1. Catalog loader must reject unknown enum values for `preflight_surface` and `operator_action` fail-closed, consistent with existing `ALLOWED_READINESS_DOMAINS` / `ALLOWED_READINESS_STATUSES` enforcement.
2. The drift detector must accept missing surface-result arguments gracefully (advisory) and fail closed only when the catalog claims `provisioned` for a reproduction-relevant dependency but the corresponding surface argument was not supplied.
3. The new `readiness-drift-check` Makefile target must depend on the existing `operator-check`, `scanner-check`, and `harbor-discovery-check` targets so `dist/self-harness-operator-preflight.json`, `dist/self-harness-scanner-check.json`, and `dist/self-harness-harbor-discovery.json` exist before the drift check runs.
4. The committed `tests/fixtures/release_candidate/readiness_drift_result.json` must be regenerated from the actual fixture catalog and fixture preflight outputs, not hand-authored.
5. The release-candidate-evidence expected hash fixture must be regenerated as the final step after all new gate artifacts are committed.

## Revised Plan

Execute the round 1 revised plan unchanged, in this order:

1. Bump `src/self_harness/readiness_matrix.py` catalog schema to `1.1`: add optional `preflight_surface` enum (`operator_preflight | scanner_check | harbor_discovery_check | release_smoke | none`, default `none`) and optional `operator_action` enum (`provision | configure | sign | publish | scan | discover`, default `provision`). Fail-closed loader enforcement; unknown fields still rejected.
2. Update `docs/operations/readiness_matrix.json` to tag Harbor, Docker, Trivy, Sigstore, PyPI, model entries with real preflight surfaces; scanner-db and kms stay `none`.
3. Add `src/self_harness/readiness_drift.py` with `ReadinessDriftReport`, `ReadinessDriftCheck`, `evaluate_readiness_drift(...)`, deterministic SHA-256 `report_hash`, `reproduction_claimed=false`, and the documented boundary string.
4. Add `scripts/readiness_drift_report.py` with exit codes `0` clean / `2` drift / `3` corrupt inputs; arguments `--catalog`, `--operator-preflight-result`, `--scanner-result`, `--harbor-discovery-result`, optional `--release-smoke-result`, `--out`, `--expected-hash`.
5. Makefile: add `readiness-drift-check` depending on `operator-check scanner-check harbor-discovery-check`; wire as prerequisite of `release-candidate-evidence`; pass `--readiness-drift-result dist/self-harness-readiness-drift.json`.
6. `scripts/release_candidate_evidence.py`: add required `--readiness-drift-result` gate using `_json_ok_gate` plus `report_hash` metadata.
7. Fixtures: add `tests/fixtures/release_candidate/readiness_drift_result.json`; regenerate `tests/fixtures/release_candidate/expected_hash.txt`. Do not rotate `tests/fixtures/canonical_audit_hash.txt`.
8. Tests: add `tests/test_readiness_drift.py` covering the seven cases enumerated in round 1.
9. CI: update `release-candidate-evidence` job to pass `--readiness-drift-result tests/fixtures/release_candidate/readiness_drift_result.json`; add a `readiness-drift` job running `make PYTHON=python readiness-drift-check` against fixture preflight outputs.
10. Docs: update `docs/operations/readiness_matrix.md` (1.1 fields + drift semantics + new gate), `docs/operations/release_candidate_evidence.md` (readiness drift as required), `RELEASE.md` (add `make readiness-drift-check` to release gate list), `docs/architecture/schema_changelog.md` (catalog 1.0 → 1.1 additive; report schema unchanged).
11. Explicit non-goals (from round 1, unchanged): no live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud contact; no audit/corpus/manifest schema change; no canonical audit hash rotation; no offline_fixture coverage instrumentation; no markdown rendering of readiness matrix; no benchmark reproduction claim.

## Remaining Open Questions

All non-blocking and explicitly deferred in round 1:

- **release_smoke preflight surface for PyPI entry:** deferred; needs a JSON status wrapper for the release-smoke directory tree. PyPI entry stays advisory until then.
- **operator_action gating promotion lifecycle transitions (e.g., `sign` actions require a signature gate):** deferred; introduced as metadata only in P47.
- **Markdown rendering of readiness matrix for release notes:** deferred (P45 Q3).
- **release-candidate-evidence failing on `live_execution_blocked=true`:** explicitly preserved as *no* by design from P46.

No further architect rounds required.
