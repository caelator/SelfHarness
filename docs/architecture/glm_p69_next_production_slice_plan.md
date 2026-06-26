CONVERGED: YES

## Verdict

The revised P69 plan is execution-ready. It adds a single narrow, additive, offline-testable cross-artifact invariant (`cross_artifact_model_protocol_binding`) to `reproduction_bundle._cross_artifact_invariants`, modeled exactly on the P68 precedent. It closes a real fidelity gap (paper Section 4.1 / Appendix A.1 fix the three model backends across variants), introduces no schema change, requires no live dependency, and inherits end-to-end through `capture_admit` → `verify_reproduction_bundle`. All round-1 blockers have been resolved with concrete decisions rather than deferrals.

## Critique

Evidence (verified against provided repository context):

- `_normal_model_backends` and `PAPER_MODEL_BACKENDS` already exist in `src/self_harness/_artifact_shapes.py` and are used by both `_fixed_protocol_config` and `_model_backend_preflight_report` validators. Importing them into `reproduction_bundle.py` is a pure re-use, no third copy.
- `_cross_artifact_invariants` currently dispatches `_cross_artifact_protocol_binding` first, then split/evaluation, then audit coverage. Appending the new check after protocol binding (and gating on both artifacts absent) preserves short-circuit ordering and matches the stated missing-artifact semantics.
- `ReproductionBundleReport.report_hash` is a deterministic hash over the check list, so any existing fixture bundle report that exercises a full bundle will rotate. The plan correctly enumerates the rotation class and correctly excludes the canonical paper-fidelity readiness hash (which is independent of bundle reports).
- `capture_admit.run_capture_admission` calls `verify_reproduction_bundle` unconditionally after bundle build, so the new invariant is enforced on the operator admission path with no additional wiring.

Inference:

- The proposed skip rule (skip iff both `model_backend_preflight_report` and `fixed_protocol_config` are absent; fail closed if exactly one is present) is consistent with P68's three-way skip rule and is the correct fail-closed default for a binding check.
- Deferring normalization-home refactor, alias-table trimming, promotion-admission re-verification, and `audit_verify_live.py` integration is the right call: each is a behaviorally distinct slice and would expand P69 beyond the "narrow invariant" brief.

Risks are low and bounded:

- **Fixture rotation blast radius** is the only material execution risk. It is mitigated by the explicit CI assertion that `make readiness` canonical hash is byte-identical pre/post P69.
- **Alias-table divergence** between `_artifact_shapes.py` and `capture_manifest_build.py` already exists pre-P69; P69 does not worsen it because both compared artifacts are validated through `_artifact_shapes._normal_model_backends`.

## Required Changes

None blocking. The plan as revised satisfies all round-1 required changes:

1. Canonical normalization pinned to `_artifact_shapes._normal_model_backends` (import, do not duplicate).
2. Missing-artifact semantics specified (skip iff both absent; fail closed on exactly-one).
3. Fixture rotation enumerated; canonical readiness hash asserted invariant.
4. Seven-case test matrix specified, including alias-equivalence lock-in.
5. Docs update specified for `docs/operations/benchmark_reproduction_readiness.md`.
6. Stop conditions specified (`reproduction-bundle-check`, `capture-admit-check`, `release-candidate-evidence`, `readiness` all green; canonical hash unchanged; no new imports beyond stdlib/existing package).

## Revised Plan

Execute the plan as specified in the round-1 "Revised Plan" section without further amendment:

- Modify `src/self_harness/reproduction_bundle.py`: import `_normal_model_backends` and `PAPER_MODEL_BACKENDS` from `_artifact_shapes`; add `_cross_artifact_model_protocol_binding(bundle, protocol_entry, preflight_entry)`; wire into `_cross_artifact_invariants` immediately after `_cross_artifact_protocol_binding`, gated on `if preflight_entry is None and protocol_entry is None: skip`.
- Update `docs/operations/benchmark_reproduction_readiness.md`: add the invariant to the bundle-verifier rejection list and note it under `fixed_protocol_config` / `model_backend_preflight_report` rows.
- Add `tests/test_reproduction_bundle_model_protocol_binding.py` with the seven enumerated cases.
- Extend `tests/test_capture_admit.py` (or sibling) with one divergent-backend admission case.
- Rotate `tests/fixtures/reproduction_bundle/*.json` and any `tests/fixtures/release_candidate/*` fixtures that embed bundle report hashes; assert canonical readiness hash unchanged.
- Stop conditions: all four Make targets green, canonical hash unchanged, seven-case test file passes, `git grep` confirms no new live-dependency imports.

## Remaining Open Questions

All four open questions from round 1 remain explicitly deferred with rationale and are non-blocking for P69:

1. Normalization-home refactor to a shared `self_harness._model_backends` module — defer to a future cleanup slice.
2. Alias-table trim to paper-attested spellings only — defer; P69 preserves existing alias set to remain purely additive.
3. Promotion-admission re-verification of the invariant — defer; bundle verification is the single chokepoint, document the boundary instead.
4. `audit_verify_live.py` multi-artifact model-backend binding — defer to a potential P70.

None of these affect P69 executability, fidelity, or offline testability.
