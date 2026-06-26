# Round 1

CONVERGED: YES

## Verdict
The next highest-value implementable offline slice is **P59: Capture Manifest Authoring (Build/Init)**. P58 delivered verification, signing, and diffing of operator-owned pre-capture manifests, but left operators to hand-author a complex multi-class JSON document with planned-run metadata, per-class planned sources, planned artifact shape stubs, and signing custody. This is the single remaining ergonomic gap before the live capture workflow is end-to-end runnable by an operator without bespoke scripting. The slice is additive, offline-only, preserves `reproduction_claimed=false`, rotates no canonical hashes, and directly prepares the live operator path by removing the last bespoke-JSON step from the pre-capture loop.

## Critique
- **Evidence (validated):** P54/P55 established the pattern for operator-facing builders (deterministic, explicit metadata, no clock/random injection, class-specific shape validation). P58 imported the post-capture shape validators into the pre-capture verifier but did not provide a symmetric builder. Operators currently must hand-write the manifest against `_MANIFEST_FIELDS`, `_PLANNED_RUN_FIELDS`, `_ENTRY_FIELDS`, `_PLANNED_SOURCE_FIELDS`, and `_SIGNING_CUSTODY_FIELDS` in `src/self_harness/capture_manifest.py`.
- **Evidence (validated):** `docs/operations/capture_manifest.md` documents verify/sign/diff but has no build/init command, unlike `docs/operations/benchmark_reproduction_readiness.md` which documents `reproduction_bundle_build.py`.
- **Evidence (validated):** The Makefile exposes `reproduction-bundle-build`/`reproduction-bundle-sign`/`reproduction-bundle-check` but no equivalent `capture-manifest-build`/`capture-manifest-init` target.
- **Inference:** Without a builder, operators will inevitably write ad-hoc scripts to assemble the manifest. This is exactly the bespoke-scripting failure mode P55 was designed to eliminate for bundles. Closing the loop symmetrically is the highest-leverage offline move remaining.
- **Risk addressed:** The builder enforces 1:1 manifest/bundle binding via the required `bundle_id` field, class-coverage from `benchmark_reproduction_requirements.json`, and planned-shape validation against the same validators the verifier uses — so a built manifest cannot silently drift from what the verifier will accept.
- **Risk addressed:** The builder remains non-reproduction evidence (`reproduction_claimed:false`), never injects a clock, and never contacts live services, preserving the P58 boundary contract.
- **Non-blocking open questions:** Output cardinality (one manifest per planned run vs. one manifest reused across reruns), whether to support a `--from-prior-bundle` template mode, and whether to emit a dry-run companion report are all advisory with safe defaults and can be revisited after first operator iteration.

## Required Changes
None blocking. The plan below satisfies the same additive, offline, no-hash-rotation, no-reproduction-claim discipline as P55/P58.

## Revised Plan
**P59: Capture Manifest Authoring**

1. **`src/self_harness/capture_manifest_build.py`**
   - Deterministic manifest builder mirroring `reproduction_bundle_build.py` patterns.
   - Inputs: `--manifest-id`, `--bundle-id`, `--operator-label`, `--created-at` (explicit, no clock), `--run-id`, `--mode live` (hardcoded paper-protocol value), `--benchmark-terminal-bench-2.0`, `--model-backend` (repeatable; must cover `minimax`/`qwen`/`glm`), `--evaluator`, `--tool-budget-json`, `--outbound-bandwidth-cap-bps`, `--mirrored-resource` (repeatable), `--signing-provider`, `--key-id`, `--fingerprint`, plus per-class planned-source and planned-artifact inputs.
   - Derives required artifact classes from `docs/operations/benchmark_reproduction_requirements.json` (single source of truth shared with the verifier).
   - For each required class, accepts a planned-source spec (`provider`, `captured_after`, `captured_before`, `operator_label`) and an optional planned-artifact JSON template path. If no template is supplied, synthesizes a minimal valid planned shape stub via a documented factory per class.
   - Runs the same class-specific shape validators as the verifier against every planned artifact before writing.
   - Writes `reproduction_claimed:false` and refuses any input that would set it otherwise.
   - Refuses `captured_after > captured_before` per entry.

2. **`scripts/capture_manifest_build.py`**
   - Thin CLI wrapper over the builder with the same path/style as `scripts/reproduction_bundle_build.py`.
   - Exit codes: `0` clean, `2` invalid input, `3` corrupt requirements catalog.

3. **`self-harness capture-manifest build`** subcommand
   - Mirrors the installed-CLI surface already established by `self-harness capture-manifest verify|diff`.

4. **Makefile targets**
   - `capture-manifest-build` (standalone, fixture-backed, no live contact).
   - Extend `capture-manifest-check` to build → sign → verify the fixture manifest end-to-end, mirroring `reproduction-bundle-check`.

5. **Tests** (`tests/test_capture_manifest_build.py`)
   - Build a complete fixture manifest from synthetic planned sources and per-class templates; assert it verifies clean under the P58 verifier.
   - Failure cases: missing required class, unknown class, duplicate class, `captured_after > captured_before`, missing paper model backend, non-live `mode`, non-terminal-bench-2.0 protocol, injected `reproduction_claimed:true`, invalid planned artifact shape, and clock/random injection attempts.
   - Deterministic hash assertion on the built manifest bytes for a fixed input set.

6. **Docs** (`docs/operations/capture_manifest.md`)
   - Add a `## Author A Plan` section with a copy-pasteable `scripts/capture_manifest_build.py` invocation and Make variable list.
   - Add a one-paragraph runbook that chains build → sign → verify → (operator live run) → `reproduction_bundle_build` → `capture_manifest_diff`.

7. **Acceptance criteria**
   - `make capture-manifest-check` builds, signs, and verifies the fixture manifest offline across Python 3.11/3.12/3.13.
   - The built fixture manifest passes the P58 verifier with `ok:true` and a deterministic `report_hash`.
   - No audit schema, corpus schema, manifest schema, canonical readiness hash, default release path, or reproduction-claim semantics change.
   - No live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud contact is added.

8. **Explicit non-goals**
   - Live artifact capture.
   - Execution of any live run.
   - N:1 manifest-to-bundle binding (default remains 1:1 via `bundle_id`).
   - Hash rotation of any committed release-evidence fixture.
   - Promotion admissibility changes (capture manifest remains advisory outside `readiness_promotion`).

## Remaining Open Questions
1. **Output cardinality.** Default to one manifest per planned run; revisit if operators want a reusable baseline manifest for variant runs.
2. **`--from-prior-bundle` template mode.** Defer; first iteration only supports explicit planned inputs. If operator demand appears, add a read-only template extractor that never copies live material.
3. **Dry-run companion report.** Defer; the verifier already produces a deterministic report. If operators want a pre-write dry-run preview, add `--dry-run` to the builder in a follow-up.
4. **P52 status.** Non-blocking; the builder reuses the same post-P52 validators already imported by P58.

[usage] {"completion_tokens": 2005, "completion_tokens_details": {"reasoning_tokens": 363}, "prompt_tokens": 38924, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 40929}
