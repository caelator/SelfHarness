# Round 1

CONVERGED: NO

## Verdict
The repository has reached a sophisticated offline release-evidence state (P0–P57), but the live-reproduction operator workflow still has one high-value, no-live-service gap: operators have a *post-capture* artifact contract (P54/P55 reproduction bundle) and a *post-evaluation* readiness evaluator (P49/P52), but no *pre-capture* plan/contract that locks the intended live-evidence set, signing custody, source providers, and run parameters before any live Harbor/Docker/model run begins. This forces operators to discover shape and coverage mismatches only after expensive live runs. A versioned **operator capture manifest** that can be authored, validated, and signed offline — and later diffed against the realized reproduction bundle — is the highest-value implementation slice available now without live services and without overclaiming reproduction. Inference: P52 is referenced in docs but missing from the brief's implemented list; this should be confirmed but is non-blocking for this slice.

## Critique
- **Strong layering exists.** Release-candidate evidence, readiness matrix/drift/promotion, reproduction readiness, and bundle build/sign/verify already form a coherent offline gate stack. The next slice must compose with these rather than duplicate.
- **Gap is operator-facing, not engine-facing.** The engine, audit, and corpus surfaces appear mature; value is now in operator reproduction enablement, not core algorithm work.
- **Bundle-only contract is reactive.** Today an operator learns about missing classes, wrong source providers, or unsigned artifacts only after capture. A pre-capture manifest lets them fail fast, offline, before consuming live infrastructure time.
- **Risk of over-engineering.** The capture manifest must remain a *plan*, not a parallel artifact repository. It must reuse P54/P55 shape validators verbatim and must not introduce a new live-evidence schema family.
- **Reproduction-claim discipline is well-enforced.** Any new surface must inherit the existing `reproduction_claimed=false` invariants and the no-live-contact boundary.
- **No evidence the default release path or canonical audit hash should rotate.** The slice must remain operator-only, like P54/P55.

## Required Changes
1. **Scope as additive operator tooling.** New module + script + optional Make target only; no audit, corpus, manifest, readiness-catalog, or release-candidate-evidence schema change; no rotation of canonical audit/readiness hashes.
2. **Reuse existing validators.** The capture manifest must validate planned entries against the same class-specific shape validators used by `reproduction_bundle`/`reproduction_readiness`, so plan-time and capture-time semantics cannot drift.
3. **One source of truth for required classes.** Drive required-class coverage from `docs/operations/benchmark_reproduction_requirements.json`; do not hard-code class lists.
4. **Signed and offline.** Support the same local-PEM / external-signer custody as P26/P55, with deterministic `report_hash` and `reproduction_claimed=false`.
5. **Plan-vs-actual diff.** Provide a verifier that compares a signed capture manifest to a realized P54 bundle and reports missing, extra, source-drift, signing-custody-drift, and parameter-drift findings without mutating either side.
6. **Boundary documentation.** Explicit operator doc stating this is a plan/contract, not live evidence, and cannot satisfy any reproduction requirement on its own.
7. **Tests with no live contact.** Fixture-backed unit tests covering valid plan, missing class, malformed shape, drift against a fixture bundle, signing custody mismatch, and reproduction-claim leakage.

## Revised Plan
**P58 — Operator live-evidence capture manifest contract**

1. `src/self_harness/capture_manifest.py`:
   - `CaptureManifest` schema `1.0` with `manifest_id`, `operator_label`, `planned_run` (run id, planned mode `live`, planned benchmark protocol `terminal-bench@2.0`, planned model backends, planned evaluator, planned tool budget, planned outbound cap, planned mirrored resources), `entries[]` (required class, planned source provider, planned captured-at window, planned signing custody), and `reproduction_claimed=false`.
   - `load_capture_manifest`, `evaluate_capture_manifest` (validates required-class coverage, per-entry class-specific *planning* shape via the existing validators reused in plan mode, signing custody shape, no reproduction claim).
2. `src/self_harness/capture_manifest_diff.py`:
   - `diff_capture_manifest_to_bundle(manifest, bundle)` producing a deterministic diff report (matched, missing-in-bundle, extra-in-bundle, source-provider-drift, custody-drift, parameter-drift) with `report_hash` and `reproduction_claimed=false`.
3. CLI + scripts:
   - `self-harness capture-manifest verify` and `scripts/capture_manifest_verify.py` with optional detached Ed25519 signature requirement reusing the existing signing sidecar conventions.
   - `scripts/capture_manifest_diff.py` writing `dist/self-harness-capture-manifest-diff.json`.
4. Make targets:
   - `make capture-manifest-check` (offline fixture plan verification) and `make capture-manifest-diff-check` (offline diff against a fixture bundle), both standalone and not part of default `check` or `release-smoke`.
5. Docs:
   - `docs/operations/capture_manifest.md` with authoring rules, required-class derivation, signing custody, plan-vs-actual semantics, and explicit non-reproduction boundary language.
6. Tests:
   - Valid plan, missing-class, malformed-shape, drift scenarios, signing custody mismatch, reproduction-claim leakage, and determinism of `report_hash` for plan and diff reports.
7. Release-candidate evidence integration: explicitly **not** required for the default release path. The capture manifest remains operator advisory material; it may be supplied as optional advisory metadata but must not block package release.

## Remaining Open Questions
1. **P52 status.** The brief's implemented list omits P52, yet docs reference it. Confirm whether P52 was implemented-and-undocumented or genuinely missing; if missing, it may need a slice before P58. Non-blocking for planning P58 because P58 reuses the post-P52 shape validators that the docs already describe.
2. **Capture manifest vs. promotion manifest relationship.** Should a signed capture manifest be admissible as advisory input to `readiness_promotion` for the reproduction-relevant rows, or kept strictly outside the promotion surface? Recommend keeping it outside promotion for now; revisit once operators have produced a real signed manifest for a real run.
3. **Parameter-drift tolerance.** Should minor drift (e.g., captured-at timestamp outside the planned window by minutes) be advisory or blocking in the diff? Recommend advisory with explicit finding detail; operators decide via their own wrapper. Confirm with first operator user.
4. **Single manifest per bundle, or N:1?** Recommend 1:1 binding enforced by `bundle_id`/`manifest_id` cross-reference to keep the audit story simple; revisit if operators need iteration.

[usage] {"completion_tokens": 2881, "completion_tokens_details": {"reasoning_tokens": 1392}, "prompt_tokens": 30067, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 32948}

---

# Round 2

CONVERGED: YES

## Verdict
The P58 operator live-evidence capture manifest contract is execution-ready. It is additive operator tooling, works fully offline, reuses the existing reproduction-bundle shape validators, inherits the signing custody and `reproduction_claimed=false` discipline, and supplies the only material gap in the pre-capture operator workflow. The remaining open questions are advisory with defensible default recommendations and do not block implementation.

## Critique
- **Evidence (validated):** P54/P55 already define the post-capture bundle contract and class-specific shape validators; P49 defines the live reproduction requirements catalog; P26/P40/P55 already establish detached Ed25519 custody conventions. P58 composes with these rather than introducing a new evidence schema family.
- **Evidence (validated):** The default release path (`make check`, `make release-smoke`, `make release-candidate-evidence`) must not rotate the canonical audit/readiness hash. The plan explicitly keeps capture-manifest Make targets standalone and advisory.
- **Inference:** Operators currently fail only post-capture; a pre-capture, signed, offline-validatable plan closes the workflow loop without touching live services. This is the highest-value no-live-service slice available.
- **Risk addressed:** Plan-vs-actual diff is non-mutating and deterministic, so the capture manifest cannot become a parallel artifact repository or quietly rewrite bundle semantics.
- **Risk addressed:** Required-class coverage is driven from `docs/operations/benchmark_reproduction_requirements.json`, preventing class-list drift between plan and evaluation.
- **Non-blocking open questions:** P52 status, promotion admissibility, drift tolerance, and 1:1 vs N:1 manifest/bundle binding all have safe default recommendations and can be resolved during or after implementation without re-architecting the slice.

## Required Changes
None blocking. The plan as written in Round 1 already satisfies:
1. Additive operator-only scope; no schema/hash rotation.
2. Reuse of P54/P55 class-specific shape validators in plan mode.
3. Required-class derivation from the single existing requirements catalog.
4. Local-PEM / external-signer custody with deterministic `report_hash` and `reproduction_claimed=false`.
5. Non-mutating plan-vs-bundle diff with explicit finding categories.
6. Boundary documentation forbidding reproduction satisfaction.
7. Fixture-backed, no-live-contact tests including drift and reproduction-claim leakage.

## Recommended Next Moves
- Implement P58 as specified: `capture_manifest.py`, `capture_manifest_diff.py`, CLI/scripts, standalone Make targets, operator doc, and the fixture test matrix.
- While implementing, confirm P52 status in passing (the plan does not depend on it; the reused validators are documented as post-P52).
- Adopt the recommended defaults for the open questions: keep capture manifest outside promotion; treat minor parameter drift as advisory with detailed findings; enforce 1:1 manifest/bundle binding via cross-reference fields. Revisit after the first real operator run.

## Remaining Open Questions
1. **P52 status.** Confirm implementation vs. documentation gap; non-blocking because P58 reuses the documented validators regardless.
2. **Promotion admissibility.** Defer; keep capture manifest strictly advisory and outside `readiness_promotion` until a real signed manifest exists.
3. **Parameter-drift tolerance.** Default to advisory findings with detail; let operators wrap with their own blocking policy.
4. **Manifest-to-bundle cardinality.** Default to 1:1 via `manifest_id`/`bundle_id` cross-reference; revisit if operator iteration demands N:1.

[usage] {"completion_tokens": 921, "completion_tokens_details": {"reasoning_tokens": 173}, "prompt_tokens": 33071, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 33992}
