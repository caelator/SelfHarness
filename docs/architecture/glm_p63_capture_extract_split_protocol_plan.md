CONVERGED: YES

## Verdict
P63 should extend the existing post-capture extraction seam (`src/self_harness/capture_extract.py`) to cover the two remaining reproduction artifact classes that already have shape validators but no extractor: `live_terminal_bench_split_manifest` and `fixed_protocol_config`. This is the correct, lowest-risk seam: validators already exist in `_artifact_shapes.py`, the offline/no-contact boundary is already established by P62, and the default release path remains untouched. The slice improves production readiness (operators can produce all 12 required artifact classes through one fail-closed dispatcher) without claiming benchmark reproduction.

## Critique
- Evidence: `_artifact_shapes.py` already defines and enforces strict shapes for both `live_terminal_bench_split_manifest` (64-case Harbor-sourced split, disjoint held-in/held-out) and `fixed_protocol_config` (`terminal-bench@2.0`, the three paper backends, non-empty evaluator/tool_set, decoding_budget object, `fixed_across_variants=true`).
- Evidence: `capture_extract.py` exposes `EXTRACTABLE_ARTIFACT_CLASSES` covering exactly six classes; both target classes are absent, so the dispatcher will reject them today.
- Evidence: `benchmark_reproduction_requirements.json` lists both classes as required (`terminal_bench_fixed_split`, `fixed_model_evaluator_tool_budget`) and binds them to blocked readiness dependencies (Harbor, Docker, paper model backends), which the extractor must not unblock.
- Inference: The right input contracts are operator-owned JSON files analogous to the existing `network-controls.json` and `capture-envelope.json` patterns: a `--split-manifest-result` raw Harbor split export and a `--fixed-protocol-declaration` operator declaration. Both must be explicit, `mode:"live"`, `reproduction_claimed:false`, and reject injected timestamps / unknown fields.
- Inference: No schema, readiness-hash, canonical-audit-hash, default release path, or reproduction-claim change is required. The work is purely additive inside the established P62 boundary.

## Required Changes
- Add `live_terminal_bench_split_manifest` and `fixed_protocol_config` to `EXTRACTABLE_ARTIFACT_CLASSES`.
- Add `extract_live_terminal_bench_split_manifest(split_result, *, harbor_version)` and `extract_fixed_protocol_config(declaration)` following the P62 strict-transform pattern: reject unknown fields, non-live modes, missing digests, empty lists, count mismatches, overlap, timestamp injection, and `reproduction_claimed:true`.
- Wire new CLI/script flags `--split-manifest-result` and `--fixed-protocol-declaration` (plus reuse `--harbor-version`) through `extract_artifact_from_paths`, the installed `self-harness capture-extract` subcommand, and `scripts/capture_extract.py`.
- Add fixture-backed tests in `tests/test_capture_extract.py` for both classes: happy path validates against `_artifact_shapes.artifact_shape_error_from_payload`, plus fail-closed cases for non-live mode, unknown fields, wrong total count, held-in/out overlap, missing model backend, missing evaluator/tool_set, and reproduction-claim leakage.
- Extend `docs/operations/capture_extract.md` with the two new classes, required input JSON shapes, and the no-reproduction boundary statement.
- Add or extend a `make capture-extract-check` target only if it does not already sweep all extractable classes (it does today; the new tests are picked up automatically).

## Revised Plan
P63: Post-capture extractors for the two remaining required artifact classes.

1. `src/self_harness/capture_extract.py`
   - Extend `EXTRACTABLE_ARTIFACT_CLASSES` with `live_terminal_bench_split_manifest` and `fixed_protocol_config`.
   - Add raw-input field allow-lists (e.g. `_SPLIT_MANIFEST_FIELDS`, `_FIXED_PROTOCOL_FIELDS`) modeled on `_NETWORK_CONTROL_FIELDS`.
   - Implement `extract_live_terminal_bench_split_manifest(split_result, *, harbor_version)`:
     - Require `schema_version=1.0`, `mode=live`, `source=harbor`, `fixed_across_variants=true`, `total_cases=64`.
     - Require non-empty `held_in_task_ids`/`held_out_task_ids`, disjoint, counts summing to 64, and matching `held_in_count`/`held_out_count`.
     - Emit the locked artifact shape with `reproduction_claimed:false` and the existing `CAPTURE_EXTRACT_BOUNDARY`.
   - Implement `extract_fixed_protocol_config(declaration)`:
     - Require `benchmark_protocol=terminal-bench@2.0`, the three paper backends after normalization (reuse `_normal_model_backends`), non-empty `evaluator`, non-empty `tool_set`, object `decoding_budget`, `fixed_across_variants=true`.
     - Emit the locked artifact shape with `reproduction_claimed:false`.
   - Route both through `extract_artifact_from_paths` with explicit `--split-manifest-result` / `--fixed-protocol-declaration` / `--harbor-version` inputs; `_required_*` helpers already exist.

2. CLI / script wiring
   - `src/self_harness/cli.py`: add `--split-manifest-result`, `--fixed-protocol-declaration` to the `capture-extract` subparser; pass through to `extract_artifact_from_paths`.
   - `scripts/capture_extract.py`: mirror the new flags.

3. Tests (`tests/test_capture_extract.py`)
   - Extend `_fixture_paths` with `split_manifest_result` and `fixed_protocol_declaration` fixtures.
   - Add happy-path coverage proving `artifact_shape_error_from_payload` returns `None` for both new classes and that the CLI round-trips to disk.
   - Add fail-closed cases: non-live mode, unknown field, total_cases != 64, held-in/out overlap, missing/extra model backend, missing evaluator/tool_set, decoding_budget not object, `reproduction_claimed:true` leakage, timestamp injection.

4. Docs
   - Update `docs/operations/capture_extract.md` "Supported Classes" list and add input-shape examples for both new classes.
   - Add a one-line note to `docs/architecture/productionization_brief.md` under a new P63 section: scope, boundary, no default release path change, no reproduction claim.

5. Stop conditions
   - No live Harbor/Docker/model/registry/scanner/PyPI/Sigstore/cloud contact.
   - No audit schema, corpus schema, manifest schema, readiness-hash, canonical-audit-hash, or reproduction-claim change.
   - No change to the default `make check` / `make smoke` / release path; `make capture-extract-check` remains an operator-only gate.

Acceptance:
- `make capture-extract-check` passes with the two new classes and all fail-closed cases.
- `self-harness capture-extract --class live_terminal_bench_split_manifest ...` and `--class fixed_protocol_config ...` produce artifacts that pass `artifact_shape_error_from_payload`.
- `dist/self-harness-reproduction-readiness.json` is unchanged unless an operator explicitly supplies the new artifacts; the readiness contract remains fail-closed.

## Remaining Open Questions
- Should the `live_terminal_bench_split_manifest` extractor also accept a signed provenance sidecar (like `audit_verify_live`), or is that out of scope for P63 and reserved for the capture manifest / bundle signing path? Recommendation: out of scope; custody is already enforced at bundle build/sign/verify and capture-manifest diff.
- Should `fixed_protocol_config` derivation be partially automated from `capture_manifest_build` inputs (model-backend, evaluator, tool-set, tool-budget) instead of requiring a separate declaration file? Recommendation: keep P63 strictly post-capture and operator-owned; a future slice could add a `capture-manifest derive-fixed-protocol` helper, but it is not required for this productionization step.
