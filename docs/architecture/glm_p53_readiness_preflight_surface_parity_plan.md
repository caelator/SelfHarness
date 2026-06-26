CONVERGED: YES

## Verdict
The P53 slice is well-bounded by existing code shapes: `readiness_drift.py` already accepts arbitrary surface-result mappings and only enforces `ok: true` + no failed required checks; `attestations.py` already emits a structural-only `AttestationVerificationReport` with `reproduction_claimed=false`; `container_preflight.py` already produces a `PreflightReport` that can be generated in a daemon-skipped mode. The dead end is purely a catalog/preflight-surface wiring gap, not a missing primitive. Round-1 convergence is achievable without further evidence.

## Critique
- **Evidence (from repo):** `ALLOWED_READINESS_PREFLIGHT_SURFACES` is a closed enum; Docker and Sigstore rows are explicitly `preflight_surface: "none"` in the catalog, and `_evaluate_entry` hard-fails on `none` for provisioned+reproduction_relevant rows. So the dead end is real and exactly as described.
- **Evidence:** `_failed_required_checks` and the surface-result `ok` contract are surface-agnostic — adding new surfaces needs no engine change, only new kwargs and CLI plumbing.
- **Evidence:** `verify_attestation` structural backend already returns `ok=true` with `cryptographic_valid=null` and `reproduction_claimed=false`, which is compatible with drift's "passing surface" contract and with the no-reproduction-claim invariant.
- **Inference:** Docker evidence can be supplied without contacting the daemon by reusing the `PreflightReport` shape and a new offline producer that emits `docker_cli_present` (real `shutil.which`) + `docker_daemon_reachable: skipped` + `container_image_present: skipped` plus a `required: false` flag on the skipped checks. This keeps the report honest about what was and was not probed.
- **Inference:** Because both rows remain `status: blocked` in the default catalog, the default release path stays advisory and non-reproduction release evidence continues to pass — no fixture regression on the canonical audit hash.

## Required Changes
1. **`src/self_harness/readiness_matrix.py`**
   - Extend `ALLOWED_READINESS_PREFLIGHT_SURFACES` with `container_preflight` and `attestation_check`.
2. **`src/self_harness/readiness_drift.py`**
   - Add `container_preflight_result` and `attestation_result` keyword args to `evaluate_readiness_drift`; wire them into `surface_results`.
   - No change to `_evaluate_entry` semantics (fail-closed preserved).
3. **`scripts/readiness_drift_report.py`**
   - Add `--container-preflight-result` and `--attestation-result` CLI flags feeding the new kwargs.
4. **`scripts/container_preflight_report.py` (new)**
   - Offline producer: emits a `PreflightReport`-shaped JSON with `docker_cli_present` (real `shutil.which`) and skipped daemon/image checks (`required_for_live: false`); accepts `--mode offline` (default) and `--mode live` for operator-owned Docker contact later. Default mode never invokes `docker info` or `docker image inspect`.
5. **`scripts/attestation_drift_surface.py` (new, thin wrapper) OR reuse `scripts/verify_attestation.py`**
   - Adapt the existing structural attestation report into a drift-surface JSON. Simplest path: `verify_attestation.py --out` already produces the required shape (`ok`, `checks[]`, `reproduction_claimed=false`); document that path in drift plumbing instead of adding a new script.
6. **`docs/operations/readiness_matrix.json`**
   - Set Docker daemon entry `preflight_surface: "container_preflight"`.
   - Set Sigstore Fulcio/Rekor entry `preflight_surface: "attestation_check"`.
   - Keep both `status: "blocked"`.
7. **Makefile**
   - `make container-preflight` → offline producer → `dist/self-harness-container-preflight.json`.
   - `make attestation-check` (already exists from P43) → `dist/self-harness-attestation.json`.
   - `make readiness-drift-check` passes both new files via CLI flags.
8. **Fixture rotation**
   - Regenerate `tests/fixtures/release_candidate/readiness_drift_result.json` (hash rotates because catalog changed).
   - Regenerate `tests/fixtures/release_candidate/release_candidate_evidence.json` if it embeds drift metadata.
   - Add `tests/fixtures/release_candidate/container_preflight_result.json` and `attestation_result.json`.
9. **Tests**
   - `test_readiness_drift.py`: provisioned Docker row with no surface → fail; with clean offline container-preflight surface → pass; with failed required check → fail; blocked row → advisory.
   - Same matrix for Sigstore/attestation surface.
   - Reproduction-claim leak test for both new surfaces.
   - Container preflight offline producer test: no subprocess to `docker`, `docker_cli_present` may be pass-or-fail but daemon check is `skipped` and not `required_for_live`.
10. **Docs**
    - Update `docs/operations/readiness_matrix.md`: document the two new surfaces, the offline boundary, and that promotion still requires operator-owned live evidence (which is out of scope for the default release path).

## Revised Plan
**P53: Offline Docker and Sigstore readiness drift surfaces**

- **Catalog change:** Promote the Docker and Sigstore rows from `preflight_surface: none` to `container_preflight` and `attestation_check` respectively, while keeping `status: blocked`. This eliminates the dead-end without weakening the default release path.
- **Surface producers (offline by default):**
  - Docker: new `scripts/container_preflight_report.py` wraps `run_container_preflight(require_daemon=False, require_image_present=False)` and serializes a `PreflightReport`. Daemon and image checks are `skipped` with `required_for_live: false`, so a missing Docker daemon does not fail the surface.
  - Sigstore: reuse `scripts/verify_attestation.py` structural output; the existing report already satisfies the drift surface contract.
- **Drift wiring:** Add the two new surface kwargs and CLI flags. Fail-closed behavior is unchanged: a provisioned reproduction-relevant entry with a missing, malformed, non-`ok`, or failed-required-check surface still fails.
- **Release-candidate evidence:** Continue to require the drift report; the new inputs flow through transparently because the evidence aggregator consumes the drift report's `ok` field, not its surface inputs.
- **Boundary preservation:** No new live contact; no audit/corpus/manifest schema change; no readiness-hash rotation; `reproduction_claimed=false` invariant maintained on both new surfaces.
- **Verification commands:**
  - `make container-preflight` (offline)
  - `make attestation-check` (structural, offline)
  - `make readiness-drift-check` (consumes both)
  - `make release-candidate-evidence` (unchanged contract, rotated fixture hashes only)
  - `pytest tests/test_readiness_drift.py tests/test_readiness_matrix.py`

## Remaining Open Questions
- Should the offline container-preflight surface ever graduate to a live mode inside the default release path? **Non-blocking:** default stays offline; live mode is operator-owned and out of P53 scope.
- Should the attestation surface require `cryptographic_valid=true` instead of accepting structural-only? **Non-blocking:** drift already enforces structural integrity via `ok=true` + no failed required checks; full cryptographic verification remains operator-owned and is not required to clear the readiness drift gate (consistent with the existing P43/P44 boundary).
- Whether to also rename `preflight_surface: none` rows elsewhere (KMS, scanner-db mirror) for symmetry. **Non-blocking:** those rows are `reproduction_relevant: false`, so they remain advisory regardless and need not change in P53.
