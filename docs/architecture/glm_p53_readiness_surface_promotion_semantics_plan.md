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
