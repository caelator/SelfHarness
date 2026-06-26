# Round 1

CONVERGED: NO

## Verdict
The slice is architecturally sound and fills a genuine gap: readiness drift verifies that *already-provisioned* rows have surface artifacts, but nothing verifies that the *act of changing a row from blocked to provisioned* is evidence-backed. P56 closes this promotion-admission loop without introducing new live dependencies. No blocking decisions remain, but the plan needs refinement on report schema, integration boundary, and failure-mode coverage before execution.

## Critique

**Evidence-backed transitions (strong fit):** The existing `readiness_drift.py` already encodes the promotion contract per-surface (Docker needs `mode: live`, Sigstore needs `cryptographic_valid: true`, model backends need `mode: live`, etc.). A promotion verifier should reuse this exact contract rather than re-deriving rules — otherwise rule drift between "admit promotion" and "verify provisioned state" becomes a new correctness hazard.

**Baseline vs candidate semantics are underspecified in the task:** The brief says "compares a baseline catalog to a candidate catalog" but doesn't say what baseline is authoritative. Options: (a) the checked-in `readiness_matrix.json` is always baseline and candidate is supplied via `--candidate-catalog`; (b) baseline is a signed/promoted snapshot under `docs/operations/promoted_readiness/`. Option (a) is simpler, option (b) gives stronger provenance. Recommend (a) for P56 and defer (b).

**Risk: re-validating catalog shape twice.** `load_readiness_matrix_catalog` already strictly validates entries. The promotion verifier must not re-implement validation; it should load both via existing API and operate on already-typed `ReadinessMatrixCatalog` objects.

**Risk: silent admission of non-transition edits.** A candidate catalog could change `operator_remediation` text, add new blocked entries, or change `preflight_surface` on a still-blocked row. The verifier needs an explicit policy: which field changes are admitted-without-evidence vs evidence-required vs rejected.

**Risk: Make target coupling.** Wiring into `release-candidate-evidence` as a hard gate before P47 drift check may create ordering ambiguity (drift check consumes surface artifacts; promotion check consumes surface artifacts for different purpose). Recommend promotion-check run *before* drift-check and emit its own artifact, with drift-check unchanged.

**Risk: no reproduction claim leakage.** Surface artifacts (e.g., `container_preflight_report` live mode) could carry fields that look like reproduction claims in nested metadata. The promotion verifier should reuse `_contains_reproduction_claim` from `readiness_drift.py`.

## Required Changes

1. **Reuse drift-check's per-surface promotion contract.** Extract the surface-specific provisioned-state rules (Docker live, Sigstore crypto-valid, model live) from `readiness_drift._evaluate_entry` into a shared helper in a new `readiness_promotion.py` module, imported by both drift and promotion verifiers. Do not duplicate.
2. **Define explicit transition policy.** Enumerate which field deltas are: (a) admitted always (new blocked entry, remediation text edit, new `optional` entry), (b) admitted only with evidence (status `blocked`→`provisioned` on reproduction-relevant row), (c) rejected outright (status downgrade `provisioned`→`blocked` without `--allow-demotion`, removal of reproduction-relevant entry, `preflight_surface` change to `none` on provisioned row).
3. **Report schema versioning.** Define `readiness_promotion` schema `1.0` with: `baseline_path`, `candidate_path`, `ok`, `admitted_transitions[]`, `rejected_transitions[]`, `unchanged_count`, `report_hash`, `reproduction_claimed: false`, boundary string. Mirror the existing report-hash pattern.
4. **Standalone Make target, advisory in default release path.** Add `make readiness-promotion-check` writing `dist/self-harness-readiness-promotion.json`. Do *not* add to default `release-candidate-evidence` required gates in P56; add it as optional metadata like attestation. Promote to required in a later slice once operators validate the workflow.
5. **Tests must cover:** no-op diff, valid promotion with evidence, valid promotion missing evidence (reject), demotion rejection, field-only edit on provisioned row, candidate missing baseline entry, candidate adding new blocked entry, reproduction-claim leakage in surface artifact, schema-version mismatch.
6. **No catalog mutation.** Verifier reads baseline and candidate, writes report only. Operator still applies the candidate by copying it into place after admission; the verifier never writes the catalog.

## Revised Plan

**P56: Offline readiness-catalog promotion admission verifier**

1. `src/self_harness/readiness_promotion.py`:
   - Extract surface-specific provisioned contract from `readiness_drift.py` into shared `_evaluate_provisioned_surface(entry, surface_result)` used by both modules.
   - `evaluate_readiness_promotion(baseline: ReadinessMatrixCatalog, candidate: ReadinessMatrixCatalog, surface_results: Mapping[str, Mapping[str, object] | None]) -> ReadinessPromotionReport`.
   - Per-entry transition classification: `unchanged`, `admitted`, `rejected`, `advisory`.
   - Strict rules: status downgrade requires `--allow-demotion`; new `provisioned`+`reproduction_relevant` row requires matching surface artifact with the same per-surface rules as drift check; removal of any existing entry is rejected; `preflight_surface` mutation on provisioned reproduction-relevant row is rejected.
   - Deterministic `report_hash` via existing `stable_json_dumps` pattern; `reproduction_claimed: false`.

2. `scripts/readiness_promotion_report.py`:
   - `--baseline-catalog`, `--candidate-catalog`, optional surface artifact flags mirroring `readiness_drift_report.py` (`--operator-preflight-result`, `--scanner-result`, etc.), `--allow-demotion`, `--out`, `--expected-hash`.
   - Exit codes: 0 clean, 2 rejected transitions present, 3 corrupt inputs.

3. `Makefile`:
   - New `readiness-promotion-check` target consuming the same surface artifacts as `readiness-drift-check`, writing `dist/self-harness-readiness-promotion.json`.
   - Default `check`/`release-candidate-evidence` do **not** depend on it.

4. Release-candidate evidence: add optional `--readiness-promotion-result` flag that, when supplied, records the report hash as advisory metadata. Do not block on it.

5. Tests under `tests/test_readiness_promotion.py` covering all transition classes, per-surface evidence rules, demotion rejection, missing baseline entry, reproduction claim leakage, and report hash determinism.

6. Docs: extend `docs/operations/readiness_matrix.md` with promotion verifier section; no changes to `productionization_brief.md` until implementation lands.

**Non-goals (explicit):** no signed baseline snapshots, no catalog mutation, no live probing, no audit/corpus schema change, no rotation of canonical readiness hash, no benchmark reproduction claim, no required gate in default release path.

## Remaining Open Questions

1. **Baseline authority:** should the checked-in `readiness_matrix.json` always be baseline, or should operators be able to supply `--baseline-catalog` pointing at a different file (e.g., last release's promoted snapshot)? *Recommendation: support both, default to checked-in file.*
2. **Multi-step promotion:** if a candidate advances two rows (e.g., Docker and Sigstore) in one edit, should the verifier admit atomically (all-or-nothing) or per-entry? *Recommendation: per-entry admission with aggregate `ok` flag; report lists each transition independently.*
3. **Operator attestation:** should the candidate catalog carry an operator signature like promotion manifests (P40), or is file-system custody sufficient for P56? *Recommendation: defer signature to a later slice; P56 only verifies transitions, not catalog provenance.*
4. **CI integration:** should CI run `readiness-promotion-check` against a fixture baseline/candidate pair as a structural test? *Recommendation: yes, as a fixture-only CI job analogous to `migration-check`.*

[usage] {"completion_tokens": 1987, "completion_tokens_details": {"reasoning_tokens": 233}, "prompt_tokens": 27967, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 29954}

---

# Round 2

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

[usage] {"completion_tokens": 976, "completion_tokens_details": {"reasoning_tokens": 47}, "prompt_tokens": 31501, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 32477}
