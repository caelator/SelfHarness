CONVERGED: YES

## Verdict
The P56 promotion-admission verifier plan is architecturally sound and ready to execute. It closes a genuine gap (drift checks verify *already-provisioned* rows; nothing verifies that the transition *to* provisioned was evidence-backed) without introducing live dependencies, mutating audits, or claiming reproduction. All material risks from round 1 are addressed in the revised plan: shared per-surface contract, explicit transition policy, deterministic schema-versioned report, advisory-only release-evidence integration, and no catalog mutation. The remaining open questions have clear recommendations that can be settled during implementation without blocking the slice.

## Critique
- **Reuse vs. duplication:** Round 1 correctly identified that `readiness_drift._evaluate_entry` is the authoritative source of per-surface provisioned-state rules (Docker live, Sigstore crypto-valid, model live). The revised plan extracts these into a shared helper in `readiness_promotion.py` consumed by both modules. This is the right call; rule drift between drift and promotion would be a new correctness hazard.
- **Transition policy is now explicit:** The three-tier classification (always-admitted, evidence-required, rejected) plus the explicit `--allow-demotion` escape hatch and rejection of removal / `preflight_surface` mutation on provisioned rows covers the failure modes I would want tested. Reproduction-claim leakage reuse from `_contains_reproduction_claim` is correct.
- **Integration boundary is conservative:** Advisory-only in `release-candidate-evidence`, standalone `make readiness-promotion-check`, no default `check`/release dependency, no schema bump to release-evidence. This matches the P40/P41 precedent of shipping additive gates as advisory before promoting them to required.
- **No catalog mutation:** Verifier is read-only over baseline/candidate and writes only the report. Operator applies the candidate via filesystem copy after admission. This preserves the audit/release-operator boundary.
- **Report schema:** Mirrors existing `report_hash` + `reproduction_claimed: false` + boundary string pattern. Schema versioning is consistent with prior slices.

No blocking risks remain.

## Required Changes
None beyond the plan as written. The round 1 "Required Changes" list (shared contract, transition policy, schema versioning, standalone Make target, test coverage matrix, no mutation) is fully incorporated into the revised plan.

## Revised Plan
Execute the P56 plan from round 1 as written:

1. `src/self_harness/readiness_promotion.py` — shared `_evaluate_provisioned_surface` extracted from drift; `evaluate_readiness_promotion(baseline, candidate, surface_results)` with per-entry classification `unchanged`/`admitted`/`rejected`/`advisory`; deterministic `report_hash`; `reproduction_claimed: false`.
2. `scripts/readiness_promotion_report.py` — `--baseline-catalog` (default checked-in), `--candidate-catalog`, surface-artifact flags mirroring drift report, `--allow-demotion`, `--out`, `--expected-hash`; exit codes 0/2/3.
3. `Makefile` — `readiness-promotion-check` writing `dist/self-harness-readiness-promotion.json`; not depended on by `check` or `release-candidate-evidence`.
4. Release-candidate evidence — optional `--readiness-promotion-result` recording report hash as advisory metadata; non-blocking.
5. `tests/test_readiness_promotion.py` — full transition matrix plus per-surface evidence rules, demotion, missing-baseline, reproduction-claim leakage, hash determinism.
6. Docs — extend `docs/operations/readiness_matrix.md` with promotion verifier section.

Explicit non-goals: signed baseline snapshots, catalog mutation, live probing, audit/corpus schema change, canonical readiness-hash rotation, benchmark reproduction claim, required default release gate.

## Remaining Open Questions
All non-blocking; recommendations stand:

1. **Baseline authority** — support both `--baseline-catalog` and default to checked-in file. Implementation-time decision.
2. **Multi-step promotion** — per-entry admission with aggregate `ok`. Implementation-time decision.
3. **Operator attestation** — defer signature; P56 verifies transitions only, not catalog provenance.
4. **CI integration** — fixture-only `readiness-promotion-check` CI job analogous to `migration-check`. Implementation-time decision; can land in same slice.

These can be resolved within the implementation without another architect round.
