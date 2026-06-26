# Round 1

CONVERGED: NO

## Verdict

Round 1 plan below is sound and small, but cannot be marked converged yet because the exact JSON status schema, the relationship between the new `release_smoke` preflight surface and the existing PyPI catalog entry, and the hash-rotation impact on readiness drift / release-candidate evidence fixtures must be pinned down before execution. The slice is clearly the deferred P47 open question and is the correct next local, offline, non-reproduction step. Risks are bounded: additive schema, no canonical audit hash rotation, no live contact. Remaining questions are narrow and decision-shaped, not experimental.

## Critique

- **Evidence (current state):** `scripts/release_smoke.py` currently prints `release smoke passed` and returns `0`; it performs provenance + signature verification, isolated venv install, CLI parity, demo + trajectory, inspect-harness schema check, and canonical audit hash comparison, but emits no machine-readable status artifact. `make release-smoke` is a prerequisite of nothing downstream — its pass/fail is CI-only today.
- **Evidence (readiness drift wiring):** `src/self_harness/readiness_drift.py` already supports `release_smoke` as a valid `preflight_surface` enum value and accepts a `release_smoke_result` argument, but the canonical catalog entry for PyPI is `preflight_surface: "none"`, so drift is advisory today. P47 explicitly deferred this slice.
- **Evidence (release-candidate evidence aggregator):** `scripts/release_candidate_evidence.py` has no `release_smoke` gate today. The drift gate already covers release-smoke indirectly only if the catalog entry is tagged and a surface result is supplied.
- **Risk — JSON schema shape:** If release-smoke emits a freeform status object, drift detector's existing `ok` field check and `reproduction_claimed` walk will work, but we need a stable, deterministic, hashable report shape (`schema_version`, `ok`, `checks`, `report_hash`, `reproduction_claimed=false`, `boundary`) to be consistent with every other offline gate artifact. Otherwise the drift detector will treat missing `ok` as failure and the fixture hash will be fragile.
- **Risk — directory artifacts:** release-smoke produces no persisted artifacts today except the tempdir it tears down. The new JSON wrapper must be written to `dist/self-harness-release-smoke.json` and be the *only* new persisted output. Do not start keeping the temp venv/demo tree.
- **Risk — fixture hash cascade:** Tagging the PyPI catalog entry with `preflight_surface: release_smoke` rotates the readiness matrix `report_hash` (catalog rows carry `preflight_surface`) and rotates the readiness drift `report_hash` (new entry moves from advisory-no-surface to covered). Release-candidate evidence fixture hash rotates because the readiness-matrix and readiness-drift gate metadata carry those report hashes. This is permitted under P47 policy but must be called out and done in the correct order: code → catalog tag → regenerate all three fixtures → regenerate release-candidate expected hash last.
- **Risk — false production confidence:** release-smoke does *not* publish to PyPI and does *not* validate trusted-publishing configuration, OIDC, or TestPyPI. The drift detector must fail closed only on "the release-smoke status artifact is missing/malformed/failed," not on "PyPI trusted publishing is provisioned." That distinction is exactly what the existing `advisory vs. fail` split in `readiness_drift.py` already encodes; we must not weaken it.
- **Risk — CI drift job:** The dedicated `readiness-drift` CI job runs `make PYTHON=python readiness-drift-check` which depends on `operator-check scanner-check harbor-discovery-check` but *not* `release-smoke`. After this slice, the drift Makefile target must also depend on `release-smoke` (or at minimum produce `dist/self-harness-release-smoke.json`), otherwise drift will spuriously pass because no surface result is supplied.
- **Inference:** No audit, corpus, manifest, or canonical readiness hash rotation is required. Release-smoke consumes the canonical audit hash as an input only.

## Required Changes

1. Pin the release-smoke JSON status schema as `release_smoke_status/1.0` with fields: `schema_version`, `ok`, `checks` (list of `{name, status, detail, required}`), `report_hash` (SHA-256 over stable JSON minus hash), `reproduction_claimed=false`, and a `boundary` string that explicitly names release-smoke as offline installability evidence, not PyPI trusted-publishing or benchmark reproduction.
2. Pin drift semantics: the PyPI catalog entry moves from `preflight_surface: none` to `preflight_surface: release_smoke`. Because the PyPI entry is `status: blocked` today, drift remains advisory unless/until operators move it to `provisioned`. This is correct and must be preserved — release-smoke is not a substitute for real PyPI trusted-publishing validation.
3. Wire the new artifact through the Makefile in dependency order: `release-smoke` writes `dist/self-harness-release-smoke.json`; `readiness-drift-check` depends on it and passes `--release-smoke-result dist/self-harness-release-smoke.json`.
4. `release-candidate-evidence` does *not* need a new dedicated gate; the readiness-drift gate already covers it once the catalog is tagged and the surface result is supplied. (Adding a redundant dedicated gate would inflate the slice and double-rotate the evidence hash unnecessarily.)
5. Regenerate fixtures in strict order: readiness matrix result → readiness drift result → release-candidate evidence expected hash. Do not touch `tests/fixtures/canonical_audit_hash.txt`.
6. Tests must cover: release-smoke writes JSON on success; release-smoke writes a failed-status JSON and exits non-zero when an inner check fails; drift detector now requires the release-smoke surface result for the PyPI catalog entry when it is `provisioned` (synthetic catalog fixture, not the real blocked one); release-candidate evidence fixture hash matches.

## Revised Plan

**P48 — Release-smoke JSON status wrapper and PyPI readiness drift binding**

1. **`scripts/release_smoke.py`:**
   - Replace the final `print("release smoke passed")` with construction of a `ReleaseSmokeStatus` (`schema_version="1.0"`, `ok`, `checks`, `report_hash`, `reproduction_claimed=false`, `boundary`).
   - Each existing verification step (wheel path, provenance verify, signature verify, venv install, import, demo, trajectory, inspect-harness schema, audit-summary no-reproduction, canonical hash compare) becomes a named `checks` entry with `required=true` and `pass|fail`.
   - On any failure, write the JSON status with `ok=false` to `--out` (if provided) and exit non-zero.
   - Add `--out` argument defaulting to `dist/self-harness-release-smoke.json`; write status JSON even on failure.
   - Deterministic `report_hash` over stable JSON minus hash.
2. **`Makefile`:**
   - `release-smoke` passes `--out dist/self-harness-release-smoke.json` to the script.
   - `readiness-drift-check` adds dependency on `release-smoke` and passes `--release-smoke-result dist/self-harness-release-smoke.json`.
3. **`docs/operations/readiness_matrix.json`:**
   - PyPI entry: change `preflight_surface` from `"none"` to `"release_smoke"`. Status remains `"blocked"`. `operator_action` remains `"publish"`.
4. **No source module change required:** `readiness_drift.py` already supports `release_smoke` as a surface enum and already accepts `release_smoke_result`.
5. **Fixtures (regenerate, do not hand-edit):**
   - `tests/fixtures/release_candidate/readiness_matrix_result.json`
   - `tests/fixtures/release_candidate/readiness_drift_result.json`
   - `tests/fixtures/release_candidate/expected_hash.txt`
   - Add `tests/fixtures/release_candidate/release_smoke_result.json` as a committed representative successful status (used by the release-candidate evidence job which does not actually run release-smoke).
   - Do not rotate `tests/fixtures/canonical_audit_hash.txt`.
6. **Tests:**
   - `tests/test_release_smoke_status.py`: success path writes `ok=true` JSON with expected check names; synthetic failure (e.g., bad canonical hash fixture in a tmp repo root) writes `ok=false` JSON and exits non-zero; `reproduction_claimed=false` always; `report_hash` is 64 lowercase hex; boundary string present.
   - Extend `tests/test_readiness_drift.py`: a synthetic `provisioned` PyPI entry with `preflight_surface: release_smoke` fails when surface result missing, passes when supplied and clean, fails when supplied and `ok=false`.
   - Extend `tests/test_release_candidate_evidence.py`: confirm fixture hash matches after regeneration.
7. **CI (`.github/workflows/ci.yml`):**
   - `release-candidate-evidence` job: pass `--release-smoke-result` is *not* needed (drift gate handles it; evidence aggregator has no dedicated release-smoke gate). But the committed `tests/fixtures/release_candidate/release_smoke_result.json` must exist for the drift-result fixture regeneration logic if any test regenerates it. No change to the evidence aggregator CLI.
   - `readiness-drift` job now transitively runs `release-smoke`; confirm it still passes on Python 3.11.
   - `release-smoke` matrix job (3.11/3.12/3.13) continues to run `make release-smoke`; confirm the new `--out` path is uploaded via the existing `dist/*` upload.
8. **Docs:**
   - `docs/operations/release_candidate_evidence.md`: note that release-smoke status is consumed by readiness drift, not by a dedicated evidence gate, and explicitly state that release-smoke is installability evidence, not PyPI trusted-publishing validation.
   - `docs/operations/readiness_matrix.md`: document the PyPI entry's `preflight_surface: release_smoke` semantics and the blocked-vs-provisioned advisory boundary.
   - `RELEASE.md`: no new gate (release-smoke is already listed); clarify that release-smoke now emits a JSON status consumed by drift detection.
   - `docs/architecture/schema_changelog.md`: add `release_smoke_status/1.0` entry; note catalog unchanged (still schema 1.1, only an enum value usage change).
9. **Explicit non-goals:**
   - No live PyPI/TestPyPI/Sigstore/OIDC contact.
   - No canonical readiness hash rotation.
   - No audit/corpus/manifest schema change.
   - No dedicated release-smoke gate inside `release_candidate_evidence.py`.
   - No benchmark reproduction claim.
   - No persisted venv/demo tempdir.

## Remaining Open Questions

1. **Should `release-candidate-evidence` *also* get a dedicated `release_smoke` gate in addition to the drift-detector coverage?** Recommendation: no, to keep the slice minimal and avoid double-counting. Deciding factor: whether operators want a single flat list of gates without having to read drift sub-checks. Non-blocking for this round.
2. **Should the PyPI catalog entry status move from `blocked` to `provisioned` as part of this slice?** Recommendation: no. Release-smoke is offline installability only, not trusted-publishing validation. The entry stays `blocked`, drift remains advisory, and operators must explicitly advance it after validating real trusted-publishing in their own environment. Deciding factor: operator policy on what "provisioned" means for PyPI. Non-blocking.
3. **Should the release-smoke JSON status include per-step timing or command traces?** Recommendation: no for v1; would inflate the schema and risk leaking environment details. Non-blocking.
4. **Should the `release_smoke` preflight surface also cover the Anthropic model API entry (since release-smoke imports the core package without optional extras)?** Recommendation: no; that entry's reproduction relevance is the live API, not importability, and release-smoke does not exercise the Anthropic adapter. Non-blocking.

[usage] {"completion_tokens": 2801, "completion_tokens_details": {"reasoning_tokens": 178}, "prompt_tokens": 34647, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 37448}

---

# Round 2

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

[usage] {"completion_tokens": 2348, "completion_tokens_details": {"reasoning_tokens": 439}, "prompt_tokens": 39918, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 42266}
