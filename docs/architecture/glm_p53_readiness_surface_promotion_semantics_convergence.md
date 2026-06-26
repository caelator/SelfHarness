# Round 1

CONVERGED: NO

## Verdict
The abstract decision is decidable now and resolves in favor of the stricter surface-specific promotion checks. The repository already establishes the exact architectural pattern needed: `readiness_drift.py::_evaluate_entry` has a special-case branch for `model_backend_preflight` that requires `mode == "live"` when an entry is `provisioned` + `reproduction_relevant`. The P53 ask is the same shape (surface-specific promotion guard) and is appropriate for a project faithful to the Self-Harness paper boundary. The candidate plan in the repo under-specifies this: it wires the surfaces but does not add the promotion guards, so as written it would let a provisioned Docker row clear on a daemon-skipped report and a provisioned Sigstore row clear on structural-only evidence — a regression of the boundary relative to the model-backend precedent.

## Critique
- **Evidence (repo):** `_evaluate_entry` already contains a precedent for surface-specific strictness: `if entry.preflight_surface == "model_backend_preflight" and surface_result.get("mode") != "live": return _fail(...)`. This shows (a) the codebase treats reproduction-relevant provisioned rows as requiring live evidence, and (b) the engine cleanly supports per-surface promotion guards without schema change.
- **Evidence (repo):** `container_preflight.py` already distinguishes `required_for_live` from `status=="skipped"`. An offline `PreflightReport` is therefore honest about *not* having probed the daemon. Treating it as sufficient for a *provisioned reproduction-relevant* Docker row would be inconsistent with treating offline model-backend preflight as insufficient for a provisioned MiniMax/Qwen/GLM row.
- **Evidence (repo):** `attestations.py` emits `cryptographic_valid: null` for structural backend and `cryptographic_valid: bool` for sigstore backend, plus `backend` field. All fields needed to enforce `backend == "sigstore" and cryptographic_valid is True` at drift time already exist.
- **Inference:** "Blocked by default, can consume offline reports without failing the default release path" is satisfied purely by `status: "blocked"` in the catalog (entries that are not `provisioned` are advisory). It does **not** require accepting offline evidence as sufficient for promotion — those are independent requirements and the candidate plan conflates them.
- **Inference:** Without the guards, an operator who flips `status` to `provisioned` for Docker or Sigstore would clear drift with evidence that does not actually demonstrate the capability the catalog claims. That weakens the release-candidate evidence contract and is a boundary regression versus the model_backend row.
- **Risk:** Adding promotion guards is cheap (two new branches in `_evaluate_entry`), is symmetric with existing code, and does not affect the default release path (where both rows stay blocked). Fixture rotation is unavoidable either way.

## Required Changes
1. In `readiness_drift.py::_evaluate_entry`, after the existing required-check pass branch, add promotion guards symmetric to the model-backend live guard:
   - For `preflight_surface == "container_preflight"`: require `surface_result.get("mode") == "live"` AND no skipped check where `required_for_live is True`. (Offline reports may still be passed as evidence while the row is `blocked`, but cannot clear a provisioned reproduction-relevant row.)
   - For `preflight_surface == "attestation_check"`: require `surface_result.get("backend") == "sigstore"` AND `surface_result.get("cryptographic_valid") is True`.
2. Extend `ALLOWED_READINESS_PREFLIGHT_SURFACES` with `container_preflight` and `attestation_check`.
3. In the catalog, set Docker row `preflight_surface: "container_preflight"` and Sigstore row `preflight_surface: "attestation_check"`; both remain `status: "blocked"`.
4. New/updated producers must emit the fields the guards read:
   - `scripts/container_preflight_report.py` must set top-level `mode: "live"` only when daemon+image checks actually ran and `required_for_live` flags reflect reality; offline mode emits `mode: "offline"`.
   - Sigstore drift surface must be sourced from `verify_attestation.py --backend sigstore` output (which sets `backend` and `cryptographic_valid`). Structural-only output remains valid only as long as the row is `blocked`.
5. CLI plumbing: add `--container-preflight-result` and `--attestation-result` flags to `scripts/readiness_drift_report.py` and the corresponding kwargs to `evaluate_readiness_drift`.
6. Tests must cover the **promotion guard matrix** explicitly:
   - Blocked row + offline evidence → advisory (default release path intact).
   - Blocked row + structural attestation → advisory.
   - Provisioned Docker row + offline (`mode: offline` or skipped required-for-live check) → FAIL.
   - Provisioned Docker row + live preflight → PASS.
   - Provisioned Sigstore row + structural (`backend: structural`, `cryptographic_valid: null`) → FAIL.
   - Provisioned Sigstore row + sigstore backend with `cryptographic_valid: false` → FAIL.
   - Provisioned Sigstore row + sigstore backend with `cryptographic_valid: true` → PASS.
   - Reproduction-claim leak test on both new surfaces.
7. Docs: state explicitly that (a) the default release path stays offline and advisory for both rows, and (b) operator promotion flips require live / cryptographically valid evidence, mirroring the model_backend rule.

## Revised Plan
**P53: Offline-default Docker and Sigstore readiness drift surfaces with stricter promotion guards**

- **Catalog:** Docker row → `preflight_surface: "container_preflight"`, Sigstore row → `preflight_surface: "attestation_check"`; both stay `status: "blocked"`, `reproduction_relevant: true`.
- **Engine (`readiness_drift.py`):**
  - Extend `ALLOWED_READINESS_PREFLIGHT_SURFACES`.
  - Add kwargs `container_preflight_result`, `attestation_result`; wire into `surface_results`.
  - Add two promotion guards in `_evaluate_entry`, mirroring the existing model-backend live guard:
    - `container_preflight`: require `mode == "live"` and no skipped `required_for_live` check.
    - `attestation_check`: require `backend == "sigstore"` and `cryptographic_valid is True`.
- **Producers:**
  - `scripts/container_preflight_report.py` (new): offline mode default; emits `mode`, real `docker_cli_present`, `skipped` daemon/image checks with `required_for_live: false`. Live mode (operator-owned) actually probes daemon and image.
  - Sigstore: reuse `scripts/verify_attestation.py --backend sigstore` output; structural output is admissible only while the row is `blocked`.
- **Default release path integrity:** Both rows stay blocked; offline/structural evidence is accepted only for advisory entries. No change to audit hash behavior beyond routine fixture rotation.
- **Boundary preservation:** No new live contact in default path; no schema change beyond `ALLOWED_READINESS_PREFLIGHT_SURFACES`; `reproduction_claimed=false` invariant maintained.
- **Verification:** `make container-preflight`, `make attestation-check`, `make readiness-drift-check`, `pytest tests/test_readiness_drift.py tests/test_readiness_matrix.py`.

## Remaining Open Questions
- Whether to also extend `preflight_surface` for the KMS / scanner-db-mirror `none` rows. **Non-blocking:** both are `reproduction_relevant: false`, so they remain advisory and are out of P53 scope.
- Whether `container_preflight` live guard should additionally require a specific image digest reference (vs. just `mode: live`). **Non-blocking:** the existing `PreflightReport` carries image info; a stricter digest-match rule can be a follow-up and does not affect P53 convergence.
- Exact CLI flag names (`--container-preflight-result` vs. `--container-preflight-report`). **Non-blocking:** cosmetic, settle in implementation.

[usage] {"completion_tokens": 2069, "completion_tokens_details": {"reasoning_tokens": 354}, "prompt_tokens": 12874, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 14943}

---

# Round 2

CONVERGED: YES

## Verdict
The revised candidate plan is ready to execute. Round 1 correctly identified the missing promotion guards, and the plan now incorporates them symmetrically with the existing `model_backend_preflight` precedent in `readiness_drift.py::_evaluate_entry`. All material risks are addressed: default release path integrity preserved (both rows stay `blocked`), boundary preserved (no new live contact, `reproduction_claimed=false` invariant maintained), fixture rotation is routine, and the engine changes are additive branches in existing code shapes. Remaining open questions are explicitly non-blocking.

## Critique
- **Evidence (repo):** `_evaluate_entry` already implements a per-surface promotion guard for `model_backend_preflight` requiring `mode == "live"`. The P53 plan's two new guards are the same shape, so no new architectural primitive is required.
- **Evidence (repo):** `attestations.py` already emits `backend` and `cryptographic_valid` (null for structural, bool for sigstore). The Sigstore promotion guard reads fields that already exist in the wire format.
- **Evidence (repo):** `container_preflight.py` already distinguishes `required_for_live` from `status=="skipped"`, and `PreflightReport` already carries the per-check metadata needed to detect a skipped required-for-live check. The offline producer is therefore honest and the live-vs-offline distinction is enforceable.
- **Evidence (repo):** Catalog currently has both Docker and Sigstore rows as `preflight_surface: "none"` + `status: blocked`. Plan correctly keeps them blocked while changing the surface, so default-path behavior stays advisory.
- **Inference:** Accepting offline/structural evidence only for advisory (non-provisioned) rows is independent of permitting promotion. The plan correctly separates these; the round-1 conflation concern is resolved.
- **Inference:** Hash rotation on `readiness_drift_result.json` is unavoidable because the catalog changes the entry surface values; this is routine and does not affect any release contract.
- **Risk:** All identified risks are bounded — promotion guards are two branches, schema change is one enum extension, and the default release path is unaffected because both rows remain blocked.

## Required Changes
None beyond what the revised candidate plan already specifies. The plan is internally consistent and the required changes from round 1 are fully incorporated.

## Revised Plan
Adopt the revised candidate plan as written:
- Catalog: Docker → `container_preflight`, Sigstore → `attestation_check`; both stay `status: blocked`, `reproduction_relevant: true`.
- Engine: extend `ALLOWED_READINESS_PREFLIGHT_SURFACES`; add `container_preflight_result` and `attestation_result` kwargs; add the two promotion guards mirroring the `model_backend_preflight` live guard.
- Producers: new offline-default `scripts/container_preflight_report.py` emitting `mode`; reuse `verify_attestation.py --backend sigstore` for the Sigstore surface.
- CLI: add `--container-preflight-result` and `--attestation-result` flags.
- Tests: cover the promotion guard matrix (blocked+offline→advisory; provisioned+offline→fail; provisioned+live→pass; structural→fail; sigstore+crypto_valid=false→fail; sigstore+crypto_valid=true→pass) plus reproduction-claim leak tests on both surfaces.
- Docs: state the offline-default invariant and the operator promotion requirements.

## Remaining Open Questions
- Extending `preflight_surface` for KMS / scanner-db-mirror `none` rows. **Non-blocking:** both are `reproduction_relevant: false`, out of P53 scope.
- Whether the `container_preflight` live guard should additionally require an image digest match. **Non-blocking:** existing `PreflightReport` carries image info; can be a follow-up.
- CLI flag naming (`--container-preflight-result` vs. `--container-preflight-report`). **Non-blocking:** cosmetic, settle in implementation.

[usage] {"completion_tokens": 908, "completion_tokens_details": {"reasoning_tokens": 71}, "prompt_tokens": 16328, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 17236}
