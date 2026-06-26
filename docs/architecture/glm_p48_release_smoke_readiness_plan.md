CONVERGED: YES

## Verdict

Round 1 plan is ready to execute. Schema is pinned (`release_smoke_status/1.0`), drift semantics are pinned (PyPI stays `blocked` so the entry remains advisory), fixture rotation order is pinned, Makefile dependency wiring is correct, no live contact is introduced, and the canonical audit hash is untouched. Remaining open questions are explicitly non-blocking and appropriately deferred. The slice correctly closes the P47-deferred release_smoke preflight surface gap without inflating scope or creating false production confidence.

## Critique

- **Evidence (drift semantics preserved):** `src/self_harness/readiness_drift.py` fails closed only when an entry is `provisioned` AND `reproduction_relevant`. Tagging the PyPI entry with `preflight_surface: release_smoke` while leaving `status: blocked` keeps the entry advisory, so no live or fixture release-smoke result is required to pass drift today. This is exactly the correct boundary.
- **Evidence (test fixture stability):** `tests/test_readiness_drift.py::test_readiness_drift_report_hash_matches_committed_fixture` does not pass `release_smoke_result`, which is consistent with the PyPI entry remaining advisory. The committed drift fixture will rotate only because the catalog row's `preflight_surface` field changed — a permitted, called-out rotation.
- **Evidence (Makefile wiring):** The current `readiness-drift-check` target depends on `operator-check scanner-check harbor-discovery-check`. Adding `release-smoke` as a prerequisite and threading `--release-smoke-result` is the minimal correct wiring. Without it, drift would spuriously pass once an operator advances PyPI to `provisioned`.
- **Evidence (release-candidate evidence):** `scripts/release_candidate_evidence.py` already has a `_readiness_drift_gate` consuming the drift report's `report_hash`. No new dedicated release-smoke gate is needed; the existing drift gate is the correct, minimal carrier.
- **Risk — fixture cascade ordering:** The plan correctly specifies the strict regeneration order (matrix → drift → release-candidate expected hash) and forbids touching `tests/fixtures/canonical_audit_hash.txt`. This is enforceable and matches P47 policy.
- **Risk — false production confidence:** The `boundary` string and `reproduction_claimed=false` field on the new release-smoke status, combined with the unchanged `blocked` status of the PyPI catalog entry, prevent the slice from implying PyPI trusted-publishing validation. Acceptable.
- **Inference:** No audit, corpus, manifest, or canonical readiness hash rotation is required. Release-smoke consumes the canonical audit hash as input only.

## Required Changes

None blocking. Execution must enforce:

1. `release_smoke_status/1.0` JSON must always be written (success and failure paths) before exiting non-zero, with `report_hash` computed over stable JSON minus the hash field.
2. The `checks` list in the release-smoke status must name each existing verification step as a discrete entry with `required=true`, so drift's failed-required-check walk operates on a stable surface.
3. The PyPI catalog entry's `status` must remain `blocked`; do not advance to `provisioned` in this slice.
4. Regenerate fixtures in the strict order: `readiness_matrix_result.json` → `readiness_drift_result.json` → `expected_hash.txt`. Do not modify `canonical_audit_hash.txt`.
5. `make release-smoke` must produce `dist/self-harness-release-smoke.json`; `make readiness-drift-check` must depend on `release-smoke` and pass `--release-smoke-result dist/self-harness-release-smoke.json`.
6. CI's `readiness-drift` job transitively runs `release-smoke`; confirm it still passes on Python 3.11.

## Revised Plan

Execute the round 1 revised plan unchanged:

1. **`scripts/release_smoke.py`:** Replace `print("release smoke passed")` with a `ReleaseSmokeStatus` builder. Each existing verification step (wheel path, provenance verify, provenance signature verify, venv install, import, demo, trajectory, inspect-harness schema, audit-summary no-reproduction, canonical hash compare) becomes a named `checks` entry with `required=true` and `pass|fail`. Add `--out` defaulting to `dist/self-harness-release-smoke.json`; write JSON on both success and failure paths; exit non-zero on failure after writing. Compute deterministic SHA-256 `report_hash` over stable JSON minus hash. Pin `schema_version="1.0"`, `reproduction_claimed=false`, and a `boundary` string naming release-smoke as offline installability evidence only — not PyPI trusted-publishing and not benchmark reproduction.
2. **`Makefile`:** `release-smoke` passes `--out dist/self-harness-release-smoke.json`. `readiness-drift-check` adds `release-smoke` as a prerequisite and passes `--release-smoke-result dist/self-harness-release-smoke.json`.
3. **`docs/operations/readiness_matrix.json`:** Change the PyPI entry's `preflight_surface` from `"none"` to `"release_smoke"`. Keep `status: "blocked"` and `operator_action: "publish"`.
4. **No source module change required:** `readiness_drift.py` already supports `release_smoke` as a valid surface and already accepts `release_smoke_result`.
5. **Fixtures (regenerate, do not hand-edit):**
   - `tests/fixtures/release_candidate/readiness_matrix_result.json`
   - `tests/fixtures/release_candidate/readiness_drift_result.json`
   - `tests/fixtures/release_candidate/expected_hash.txt`
   - Add `tests/fixtures/release_candidate/release_smoke_result.json` as a committed representative successful status (used by operators and as a stable reference shape; the release-candidate evidence aggregator does not consume it directly).
   - Do not rotate `tests/fixtures/canonical_audit_hash.txt`.
6. **Tests:**
   - `tests/test_release_smoke_status.py`: success path writes `ok=true` JSON with expected check names; synthetic failure (e.g., bad canonical hash fixture in a tmp repo root) writes `ok=false` JSON and exits non-zero; `reproduction_claimed=false` always; `report_hash` is 64 lowercase hex; `boundary` string present.
   - Extend `tests/test_readiness_drift.py`: a synthetic `provisioned` PyPI entry with `preflight_surface: release_smoke` fails when surface result missing, passes when supplied and clean, fails when supplied and `ok=false`.
   - Extend `tests/test_release_candidate_evidence.py`: confirm fixture hash matches after regeneration.
7. **CI (`.github/workflows/ci.yml`):**
   - `release-candidate-evidence` job: no new `--release-smoke-result` argument (drift gate handles it).
   - `readiness-drift` job now transitively runs `release-smoke`; confirm it passes on Python 3.11.
   - `release-smoke` matrix job continues to upload `dist/*` — confirm the new JSON is captured by the existing glob.
8. **Docs:**
   - `docs/operations/release_candidate_evidence.md`: state that release-smoke status is consumed by readiness drift, not by a dedicated evidence gate; release-smoke is installability evidence, not PyPI trusted-publishing validation.
   - `docs/operations/readiness_matrix.md`: document the PyPI entry's `preflight_surface: release_smoke` semantics and the blocked-vs-provisioned advisory boundary.
   - `RELEASE.md`: clarify that release-smoke now emits a JSON status consumed by drift detection.
   - `docs/architecture/schema_changelog.md`: add `release_smoke_status/1.0` entry; catalog schema stays at `1.1` (only an enum value usage change for the PyPI entry).
9. **Explicit non-goals (unchanged):** No live PyPI/TestPyPI/Sigstore/OIDC contact; no canonical readiness hash rotation; no audit/corpus/manifest schema change; no dedicated release-smoke gate inside `release_candidate_evidence.py`; no benchmark reproduction claim; no persisted venv/demo tempdir.

## Remaining Open Questions

All non-blocking and explicitly deferred:

- **Should `release-candidate-evidence` also get a dedicated `release_smoke` gate?** Recommendation: no. Deciding factor: operator preference for a flat gate list. Defer.
- **Should the PyPI entry move to `provisioned`?** Recommendation: no. Release-smoke is offline installability only, not trusted-publishing validation. Defer to operator policy.
- **Should release-smoke status include per-step timing or command traces?** Recommendation: no for v1. Defer.
- **Should `release_smoke` preflight surface cover the Anthropic model API entry?** Recommendation: no; reproduction relevance is the live API, not importability. Defer.

No further architect rounds required.
