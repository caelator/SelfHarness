# Round 1

CONVERGED: NO

## Verdict

The current state after P46 has the readiness matrix as required release-candidate evidence, all offline gates implemented, and remaining production work almost entirely gated behind live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud access. The single highest-value *locally implementable* next slice is closing P45 deferred question Q2: **cross-reference the readiness matrix catalog with operator preflight outputs to detect declarative drift**. This is the only P45/P46 deferred item that (a) is actually ready, (b) materially hardens the release gate, and (c) requires no live access. Round 1 plan below is sound in shape but needs tightening around schema impact and the precise drift model before execution.

## Critique

- **Evidence:** `scripts/release_candidate_evidence.py` already aggregates the readiness matrix and operator preflight reports side by side but performs *no* cross-consistency check between them. They are independent gates today. (Inference: a drift detector is a new offline verifier, not a refactor.)
- **Evidence:** `src/self_harness/readiness_matrix.py` uses frozen sets `ALLOWED_READINESS_DOMAINS`, `ALLOWED_READINESS_STATUSES`, `KNOWN_READINESS_AFFECTS`, and `_ENTRY_FIELDS`. Any new catalog field is a fail-closed schema change and rotates the readiness matrix `report_hash`. That is permitted by the rotation policy (`docs/operations/readiness_matrix.md`), but it must be called out explicitly.
- **Evidence:** `scripts/operator_preflight.py` emits `PreflightCheck` rows with `name`, `status`, `detail`, `required`, `metadata`. There is no stable cross-reference key today; `name` is human-readable and not part of the readiness vocabulary.
- **Gap:** The initial plan to add `preflight_check_ids` to catalog entries assumes a 1:1 mapping that does not exist. Operator preflight checks are coarse (bundle, image_policy, scanner_dry_run_command, harbor_discovery_offline) while readiness catalog entries are fine-grained per dependency. A naive cross-reference will produce false drift.
- **Risk:** If the drift detector only checks "catalog entry X has no preflight coverage," every fine-grained catalog row will fail because preflight checks are intentionally broader. The detector must be scoped to *operator-actionable* drift: catalog claims `provisioned` for a reproduction-relevant dependency, but the matching preflight surface fails or is absent.
- **Risk:** Coupling the catalog schema bump to unrelated P45 Q3 work (markdown rendering) would inflate the slice. Keep them separate.
- **Non-goal discipline:** This slice must not attempt to verify that `offline_fixture` paths are actually exercised by tests. That is desirable but requires a coverage-instrumentation layer that does not exist; deferring keeps the slice executable.

## Required Changes

1. Replace the proposed `preflight_check_ids` field with a coarser, operator-meaningful `preflight_surface` enum on catalog entries (e.g., `operator_preflight`, `scanner_check`, `harbor_discovery_check`, `release_smoke`, `none`). This matches how the operator actually validates each dependency offline and avoids false drift.
2. The drift detector must fail closed *only* when: catalog `status == "provisioned"` AND `reproduction_relevant == true` AND the named preflight surface's report is missing, malformed, or reports a failed check. Blocked/optional entries are advisory only.
3. Add a stable `operator_action` enum to catalog entries (`provision`, `configure`, `sign`, `publish`, `scan`, `discover`) so operator remediation language is machine-checkable, not just prose. Optional but cheap and removes a class of future drift.
4. Bump readiness catalog schema to `1.1` (additive fields, all optional with safe defaults) and readiness report schema stays `1.0` because the report shape is unchanged except for carried-through metadata. Document in `docs/architecture/schema_changelog.md`.
5. Rotate `tests/fixtures/release_candidate/readiness_matrix_result.json` and `tests/fixtures/release_candidate/expected_hash.txt`. Do **not** rotate `tests/fixtures/canonical_audit_hash.txt`.
6. New gate target `make readiness-drift-check` running the new verifier against the fixture catalog and fixture preflight reports. Wire into `release-candidate-evidence` as a required gate.

## Revised Plan

**P47 — Readiness catalog drift detection via preflight cross-reference**

1. **Catalog schema bump (additive, `1.0` → `1.1`):**
   - `entries[].preflight_surface`: optional enum `operator_preflight | scanner_check | harbor_discovery_check | release_smoke | none`. Default `none`.
   - `entries[].operator_action`: optional enum `provision | configure | sign | publish | scan | discover`. Default `provision`.
   - Loader in `src/self_harness/readiness_matrix.py` enforces enum values fail-closed; unknown fields still rejected.
2. **Update `docs/operations/readiness_matrix.json`:**
   - Tag Harbor, Docker, Trivy, Sigstore, PyPI, model entries with their real preflight surface.
   - Tag scanner-db and kms entries as `preflight_surface: none` (advisory only).
3. **New module `src/self_harness/readiness_drift.py`:**
   - `ReadinessDriftReport` dataclass with `schema_version="1.0"`, `ok`, `checks: tuple[ReadinessDriftCheck, ...]`, `report_hash`, `reproduction_claimed=false`, boundary string.
   - `evaluate_readiness_drift(catalog, *, operator_preflight_result, scanner_result, harbor_discovery_result, release_smoke_available)`:
     - For each catalog entry where `status == "provisioned"` and `reproduction_relevant == true`:
       - Look up the named preflight surface result.
       - Fail closed if missing, malformed, `ok != true`, or any required check failed.
     - For each catalog entry where `preflight_surface != "none"` but the surface result is absent entirely: emit `status=advisory` (does not block).
   - Deterministic SHA-256 over stable JSON of the report-minus-hash.
4. **New script `scripts/readiness_drift_report.py`:**
   - Args: `--catalog`, `--operator-preflight-result`, `--scanner-result`, `--harbor-discovery-result`, `--release-smoke-result` (optional), `--out`, `--expected-hash`.
   - Exit `0` clean, `2` drift detected, `3` corrupt inputs.
5. **Makefile:**
   - New target `readiness-drift-check` consuming `dist/self-harness-operator-preflight.json`, `dist/self-harness-scanner-check.json`, `dist/self-harness-harbor-discovery.json`, and the fixture catalog.
   - Add as prerequisite of `release-candidate-evidence`; pass `--readiness-drift-result dist/self-harness-readiness-drift.json` to the evidence aggregator.
6. **`scripts/release_candidate_evidence.py`:**
   - Add required `--readiness-drift-result` gate using the existing `_json_ok_gate` helper plus `report_hash` metadata extraction.
7. **Fixtures:**
   - Add `tests/fixtures/release_candidate/readiness_drift_result.json` generated from the fixture catalog and existing fixture preflight outputs.
   - Regenerate `tests/fixtures/release_candidate/expected_hash.txt`.
8. **Tests (`tests/test_readiness_drift.py`):**
   - Catalog entry `provisioned` + missing surface result → fail.
   - Catalog entry `provisioned` + surface result `ok=false` → fail.
   - Catalog entry `blocked` + missing surface result → pass (advisory).
   - Catalog entry `provisioned` + `reproduction_relevant=false` + missing surface → pass (advisory).
   - Malformed surface JSON → fail.
   - Reproduction-claim leak in any input → fail.
   - Deterministic `report_hash` matches committed fixture.
9. **CI (`.github/workflows/ci.yml`):**
   - `release-candidate-evidence` job: add `--readiness-drift-result tests/fixtures/release_candidate/readiness_drift_result.json`.
   - Add a dedicated `readiness-drift` job running `make PYTHON=python readiness-drift-check` against fixture preflight outputs.
10. **Docs:**
    - `docs/operations/readiness_matrix.md`: document `1.1` fields, drift semantics, and the new gate.
    - `docs/operations/release_candidate_evidence.md`: move readiness drift from nonexistent to required.
    - `RELEASE.md`: add `make readiness-drift-check` to the release gate list.
    - `docs/architecture/schema_changelog.md`: catalog `1.0 → 1.1` additive entry; report schema unchanged.
11. **Explicit non-goals:**
    - No live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud contact.
    - No audit/corpus/manifest schema change.
    - No rotation of `tests/fixtures/canonical_audit_hash.txt`.
    - No verification that `offline_fixture` paths are actually exercised by tests (deferred — needs coverage instrumentation).
    - No markdown rendering of the readiness matrix (P45 Q3 — separate slice).
    - No benchmark reproduction claim.

## Remaining Open Questions

- Should the drift detector also cover `release_smoke` as a preflight surface for the PyPI/publish entry? **Tentative yes**, but release-smoke output is a directory tree, not a JSON report, so it needs a thin JSON status wrapper first. **Defer** to keep P47 executable; PyPI entry stays `preflight_surface: none` (advisory) until a release-smoke status JSON exists.
- Should `operator_action` eventually gate promotion lifecycle transitions (e.g., `sign` actions require a signature gate)? **Likely yes** but out of scope for P47; the field is introduced as machine-readable metadata only.
- Is a markdown rendering of the readiness matrix wanted for release notes? **Deferred** (P45 Q3); non-blocking for P47.
- Should `release-candidate-evidence` fail when `live_execution_blocked=true`? **No, by design** — preserved from P46.

[usage] {"completion_tokens": 3233, "completion_tokens_details": {"reasoning_tokens": 1029}, "prompt_tokens": 44039, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 47272}

---

# Round 2

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

[usage] {"completion_tokens": 1845, "completion_tokens_details": {"reasoning_tokens": 377}, "prompt_tokens": 48474, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 50319}
