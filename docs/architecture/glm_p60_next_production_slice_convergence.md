# Round 1

CONVERGED: YES

## Verdict

The next highest-value implementable offline slice is **P60: Offline Capture Pipeline Rehearsal**. P59 closed the last bespoke-authoring gap in the pre-capture operator loop, but operators still cannot rehearse the full plan→sign→capture→bundle→sign→diff→reproduction-readiness pipeline before spending real Harbor/Docker/model resources. A deterministic offline simulation that materializes a synthetic reproduction bundle from a capture manifest's planned artifact stubs, then runs the existing P55 bundle verification, P58 manifest/bundle diff, and P49 reproduction-readiness gates against it, would catch contract drift, shape mismatches, and integration regressions without any live contact. This is the single remaining offline friction point before the live operator path is end-to-end runnable with confidence.

## Critique

- **Evidence (validated):** P59 planned artifacts already pass `_artifact_shapes` shape validation, and P58 diff catches provider/custody/bundle-id drift between a manifest and a realized bundle. But neither proves that the planned artifacts, once realized, will actually satisfy `reproduction_readiness_report.py` end-to-end. Operators currently discover this only after live capture.
- **Evidence (validated):** `dist/self-harness-reproduction-readiness.json` currently fails on model backend preflight `mode must be live` and audit-verify `mode must be live`. A rehearsal that injects the manifest's planned stubs as the artifact set would surface exactly which shape fields reproduction-readiness additionally enforces beyond `_artifact_shapes` (e.g., `mode: live`, `ok: true`, `reproduction_claimed: false` simultaneously).
- **Evidence (validated):** The Makefile has no `capture-rehearsal` target. `capture-manifest-check` stops at build→sign→verify; it does not chain into bundle build, bundle sign, diff, or reproduction-readiness.
- **Inference:** Operators preparing a costly live run want a single command that proves "if my live capture matches my plan exactly, the entire evidence pipeline accepts it." This is the same rehearsal discipline that P59 applied to manifest authoring.
- **Risk addressed:** The rehearsal surfaces silent contract gaps between capture-manifest planned shapes, reproduction-bundle accepted shapes, and reproduction-readiness required shapes. Today these are three overlapping but not identical validator sets; a rehearsal is the integration test that binds them.
- **Risk addressed:** The rehearsal stays fully offline, injects no clock, sets `reproduction_claimed=false` everywhere, and reuses existing verifiers rather than adding new evidence schemas.

## Required Changes

None blocking. The plan below is additive, offline-only, and rotates no canonical hashes.

## Revised Plan

**P60: Offline Capture Pipeline Rehearsal**

1. **`src/self_harness/capture_rehearsal.py`**
   - New module that materializes a synthetic reproduction bundle from a capture manifest's planned artifacts.
   - Inputs: validated `CaptureManifest` (from P58 loader), explicit `rehearsal_id` and `operator_label` (no clock injection), output directory.
   - For each manifest entry, writes `planned_artifact` bytes to `<out_dir>/<artifact_class>.json`.
   - Builds a `ReproductionBundle` via the existing P55 builder paths (deterministic manifest bytes, SHA-256 digests, byte sizes, relative paths, class-coverage from `benchmark_reproduction_requirements.json`).
   - Runs three existing verifiers against the synthetic bundle and the original manifest:
     - `verify_reproduction_bundle` (P54/P55)
     - `capture_manifest_diff` (P58)
     - `evaluate_reproduction_readiness` (P49) with the synthetic artifacts as the artifact index
   - Returns a `CaptureRehearsalReport` with per-stage `ok`, `report_hash`, and `reproduction_claimed=false`.
   - Refuses any manifest or planned artifact with `reproduction_claimed=true`.
   - Refuses `mode != live` planned artifacts because the rehearsal simulates a live capture; this is the one place we intentionally diverge from the P59 stubs' `mode` values if an operator supplied looser templates.

2. **`scripts/capture_rehearsal.py`**
   - Thin CLI wrapper: `--manifest`, `--manifest-signature` (optional), `--public-key` (optional), `--rehearsal-id`, `--operator-label`, `--out-dir`, `--report-out`.
   - Exit codes: `0` clean rehearsal, `2` rehearsal exposed a contract gap (operator-fixable), `3` corrupt inputs (manifest/schema error).

3. **`self-harness capture-manifest rehearse`** subcommand
   - Mirrors the existing `capture-manifest build|verify|diff` installed-CLI surface.

4. **Makefile targets**
   - `capture-rehearsal` (standalone, fixture-backed, no live contact).
   - Extend `capture-manifest-check` to run build → sign → verify → rehearse → diff against the rehearsal-produced bundle.
   - Extend `capture-manifest-diff-check` to use the rehearsal bundle when no operator-supplied bundle is present.

5. **Tests** (`tests/test_capture_rehearsal.py`)
   - Rehearse the fixture capture manifest from P59; assert all three stages pass and `report_hash` is deterministic.
   - Failure cases: manifest with a planned artifact whose `mode` is not `live`, manifest missing a required class, reproduction-readiness detecting a shape gap the per-class stub intentionally does not satisfy, `reproduction_claimed=true` injection, clock/random injection attempts, and signature mismatch when `--manifest-signature` is supplied.
   - Cross-check: the rehearsal-produced bundle must pass `reproduction_bundle_verify.py` and `capture_manifest_diff.py` standalone.

6. **Docs** (`docs/operations/capture_manifest.md`)
   - Add a `## Rehearse The Plan` section between `## Verify A Plan` and `## Diff Against A Bundle`.
   - Add the rehearsal to the documented operator sequence as step 3.5: "Rehearse the signed plan against a synthetic bundle to catch contract gaps before live capture."

7. **Acceptance criteria**
   - `make capture-manifest-check` runs the full build → sign → verify → rehearse → diff chain offline across Python 3.11/3.12/3.13.
   - The rehearsal report has `ok:true` on the fixture manifest, `reproduction_claimed:false`, and a deterministic `report_hash`.
   - No audit schema, corpus schema, manifest schema, canonical readiness hash, default release path, or reproduction-claim semantics change.
   - No live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud contact is added.

8. **Explicit non-goals**
   - Live artifact capture or execution.
   - Real model-backend preflight (the rehearsal uses the planned stubs).
   - New evidence schema versions (the rehearsal report is `capture_rehearsal/1.0` but reuses all downstream schemas).
   - Hash rotation of any committed release-evidence or reproduction-readiness fixture.
   - Validation of operator-supplied planned-artifact templates beyond what P59 and P54 already enforce.

## Remaining Open Questions

1. **Rehearsal report schema versioning.** Default to `capture_rehearsal/1.0` as a new minor schema. If operators want it ingested by release-candidate evidence later, add it as advisory metadata in a follow-up rather than expanding the release gate now.
2. **Whether to add a `--strict-reproduction-readiness` flag** that fails the rehearsal when reproduction-readiness reports `reproduction_ready:false`. Default: do not fail, because the rehearsal's purpose is to surface gaps, not to require full readiness before live capture. Operators can post-process the JSON if they want stricter gating.
3. **P52 status.** Non-blocking. P52 appears to have been folded into P49/P54 shape validation work. If a future audit surfaces a distinct P52 deliverable, it can be scheduled independently; P60 does not depend on it.

[usage] {"completion_tokens": 2725, "completion_tokens_details": {"reasoning_tokens": 1004}, "prompt_tokens": 39766, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 42491}
