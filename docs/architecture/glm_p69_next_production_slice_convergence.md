# Round 1

CONVERGED: NO

## Verdict

Initial plan proposed: P69 adds `cross_artifact_model_protocol_binding` to reproduction bundle verification, requiring `model_backend_preflight_report.backends` (normalized) to equal `fixed_protocol_config.models` (normalized). This is a narrow, offline-testable, code-level invariant in the same style as P66–P68. It closes a real fidelity gap (the paper fixes the three model backends across variants in Section 4.1, and Appendix A.1 binds each backend to concrete provisioning), and it does not require any artifact-schema change, readiness-hash rotation, or live dependency. The plan is sound but not yet fully converged: fixture-rotation strategy, exact test matrix, and edge-case semantics need one more round before execution.

## Critique

Strengths of the proposed slice:
- **No schema change.** Unlike adding held-in/held-out per-split aggregates to `live_two_repeat_evaluation_report` (which would force a closed-field-set bump and rotate the readiness hash), this only adds a new cross-artifact check in `reproduction_bundle._cross_artifact_invariants`. P68 precedent (`cross_artifact_protocol_binding`) is followed exactly.
- **Offline and deterministic.** Reuses `_normal_model_backends` already defined in `_artifact_shapes.py` and `capture_manifest_build.py`. No new dependency on Harbor/Docker/Trivy/PyPI/Sigstore/models.
- **Closes a real invariant gap.** Currently `model_backend_preflight_report` and `fixed_protocol_config` can independently claim different backend sets (e.g., preflight checks {minimax, qwen, glm} while protocol declares {minimax, qwen, glm, sonnet}) and bundle verification still passes. This violates the paper's fixed-protocol contract.
- **Inherits through capture admission.** `capture_admit` runs `verify_reproduction_bundle`, so the new check is enforced end-to-end without an additional code path.

Weaknesses to address before convergence:
1. **Normalization semantics are duplicated.** `_normal_model_backends` exists in two modules already (`_artifact_shapes.py`, `capture_manifest_build.py`). Adding a third call site is fine, but the plan should explicitly state the canonical home and ensure both compared artifacts use the same normalization (alias sets for `minimax-m2.5`, `qwen3.5-35b-a3b`, `glm-5`).
2. **Missing-artifact behavior must be specified.** P68's `cross_artifact_protocol_binding` returns `_fail` if either bound artifact is missing. For P69, if `model_backend_preflight_report` is missing but `fixed_protocol_config` is present (or vice versa), the check should fail only when *both* are absent is the no-op case. P68 used `if protocol_entry is None and evaluation_entry is None and audit_entry is None: return None`. P69 must pick an analogous rule. Recommendation: skip the check only when *both* `model_backend_preflight_report` and `fixed_protocol_config` are absent; fail if exactly one is present.
3. **Order independence.** `reproduction_bundle._cross_artifact_invariants` currently calls `_cross_artifact_protocol_binding` first, then split/audit checks. P69 should be appended after the protocol-binding check so failures in protocol binding short-circuit before model-backend binding (the latter depends on the same `fixed_protocol_config` artifact).
4. **Fixture rotation impact.** Adding a new check changes the deterministic `report_hash` of `ReproductionBundleReport` for every existing fixture bundle. The plan must explicitly enumerate which committed fixture files will rotate: at minimum `tests/fixtures/reproduction_bundle/*.json` report fixtures, and any `tests/fixtures/release_candidate/*` artifacts that embed bundle report hashes. Canonical paper-fidelity audit hash must NOT rotate (it is independent of bundle reports).
5. **Test matrix is underspecified.** Need explicit cases: (a) matching normalized sets, (b) alias-equivalent sets (e.g., `minimax-m2.5` vs `minimax`), (c) subset/superset mismatch, (d) missing preflight artifact, (e) missing protocol artifact, (f) both missing (check skipped).
6. **Documentation drift.** `docs/operations/benchmark_reproduction_readiness.md` lists required artifact shapes; the new invariant should be mentioned in the `live_harbor_audit` / `live_two_repeat_evaluation_report` rows where `cross_artifact_protocol_binding` is already cited, and a new line should appear under the bundle-verifier rejection list.

## Required Changes

Before P69 is execution-ready, the revised plan must:
1. Pin the canonical normalization function (recommend: re-export `_normal_model_backends` from `_artifact_shapes.py` and import it into `reproduction_bundle.py`; do not duplicate the alias table a third time).
2. Specify missing-artifact semantics: skip iff both `model_backend_preflight_report` and `fixed_protocol_config` are absent; fail closed if exactly one is present.
3. Enumerate the exact fixture files whose `report_hash` will rotate, and assert in CI that the canonical paper-fidelity audit hash (`make readiness` hash) is unchanged.
4. Define the test matrix with six cases listed above, plus an alias-equivalence case to lock in the normalization contract.
5. Add the new invariant name to the bundle-verifier rejection list in `docs/operations/benchmark_reproduction_readiness.md`.
6. Specify the stop condition: slice is complete when `make reproduction-bundle-check`, `make capture-admit-check`, and the existing paper-fidelity readiness gate all pass with rotated fixture hashes and unchanged canonical audit hash.

## Revised Plan

**P69 — Model-backend protocol binding verification**

Files to modify:
- `src/self_harness/reproduction_bundle.py`
  - Re-export or import `_normal_model_backends` (and `PAPER_MODEL_BACKENDS`) from `_artifact_shapes`.
  - Add `_cross_artifact_model_protocol_binding(bundle, protocol_entry, preflight_entry)` returning `ReproductionBundleCheck | None`, modeled on `_cross_artifact_protocol_binding`.
  - Wire it into `_cross_artifact_invariants` immediately after the existing protocol-binding check, gated on `if preflight_entry is None and protocol_entry is None: skip`.
- `docs/operations/benchmark_reproduction_readiness.md`
  - Add `cross_artifact_model_protocol_binding` to the bundle-verifier rejection list.
  - Note the invariant under the `fixed_protocol_config` and `model_backend_preflight_report` shape rows.

Files to add (tests):
- `tests/test_reproduction_bundle_model_protocol_binding.py`
  - Case 1: matched canonical backend names → pass.
  - Case 2: alias-equivalent names (`minimax-m2.5`, `qwen3.5-35b-a3b`, `glm-5` in protocol; `minimax`, `qwen`, `glm` in preflight) → pass.
  - Case 3: preflight missing one backend → fail.
  - Case 4: preflight has extra backend → fail.
  - Case 5: only `fixed_protocol_config` present, no preflight → fail.
  - Case 6: only `model_backend_preflight_report` present, no protocol → fail.
  - Case 7: both absent → check skipped, does not appear in `checks`.
- Extend `tests/test_capture_admit.py` (or sibling) with one admission case where preflight and protocol backends diverge; assert admission fails with the new check name in the bundle verification subreport.

Fixtures to rotate:
- `tests/fixtures/reproduction_bundle/*.json` (bundle report fixtures) — any file that exercises a full bundle.
- `tests/fixtures/release_candidate/release_candidate_evidence_result.json` and the `release-candidate-evidence-reproduction` fixture if it embeds a bundle report hash.
- Assert in CI: canonical paper-fidelity readiness hash printed by `make readiness` is byte-identical to its pre-P69 value.

Invariants:
- `model_backend_preflight_report.backends` normalized via `_normal_model_backends` must equal `fixed_protocol_config.models` normalized via the same function.
- Both must equal `{"minimax", "qwen", "glm"}`.
- The check emits metadata: `{"protocol_backends": [...], "preflight_backends": [...]}`.

Stop conditions:
- `make reproduction-bundle-check`, `make capture-admit-check`, `make release-candidate-evidence`, `make readiness` all green.
- Canonical readiness hash unchanged.
- New test file passes all seven cases.
- No new live dependency introduced (verified by grep on imports).

## Remaining Open Questions

1. **Normalization home.** Should `_normal_model_backends` move from `_artifact_shapes.py` to a shared `self_harness._model_backends` module to make it the obvious canonical source? Inference: probably yes for long-term hygiene, but not required for P69. Recommend deferring to a future cleanup slice to keep P69 narrow.
2. **Alias stability.** The alias set currently includes spellings like `minimax-m25` and `qwen35-35b-a3b` that do not appear in the paper. Should P69 trim the alias table to only paper-attested spellings (`minimax`, `minimax-m2.5`, `qwen`, `qwen3.5-35b-a3b`, `glm`, `glm-5`)? Inference: yes for fidelity, but it is a behavioral change to existing validators and would rotate additional fixtures. Recommend deferring; P69 should preserve the existing alias set to remain a pure additive invariant.
3. **Promotion admission integration.** `readiness_promotion.py` reuses the provisioned-surface contract but does not currently re-run bundle verification. Should P69's invariant also be enforced at promotion admission? Inference: not required — promotion admission operates on readiness reports, not raw bundles, and bundle verification is the single chokepoint. Recommend documenting this boundary in `docs/operations/operator_promotion.md` instead of changing code.
4. **Should the check be added to `audit_verify_live.py`?** That path verifies a single live Harbor audit; it does not currently see `model_backend_preflight_report`. Inference: out of scope for P69; deferred to a future P70 if a multi-artifact live verifier is needed.

[usage] {"completion_tokens": 3607, "completion_tokens_details": {"reasoning_tokens": 1418}, "prompt_tokens": 56322, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 59929}

---

# Round 2

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

[usage] {"completion_tokens": 1281, "completion_tokens_details": {"reasoning_tokens": 150}, "prompt_tokens": 60726, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 62007}
