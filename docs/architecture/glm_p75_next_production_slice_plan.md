CONVERGED: YES

## Verdict

The next highest-value narrow slice is **P75: planned `fixed_protocol_config` core-hash binding in `capture_manifest_diff`**. The P74 plan explicitly deferred this as an open question. Today, the capture manifest's `planned_artifact` for `fixed_protocol_config` and the realized bundle's `fixed_protocol_config` are both validated for shape, but nothing compares the *content* of the planned protocol to the realized protocol. This allows an operator to plan one protocol and capture another. 

This slice closes that plan-vs-realized seam without any schema changes by computing a deterministic hash over the core protocol fields (`benchmark_protocol`, normalized `models`, `evaluator`, `tool_set`, `decoding_budget`, `fixed_across_variants`) for both sides and failing closed on drift. It is narrow, offline, additive, and requires no canonical hash rotation. Plan ready to execute.

## Critique

Evidence supporting convergence:
- `capture_manifest_diff.py` already reads the bundle's primary captured artifacts via `read_artifact_payload` (introduced in P74) and has direct access to `manifest.entries`.
- The capture manifest builder (`capture_manifest_build.py`) and extractor (`capture_extract.py`) both produce a `fixed_protocol_config` artifact with the same core fields, but the realized artifact may include extra metadata like `boundary` or `operator_label` that should not contribute to a protocol-drift check.
- A core-fields-only hash ensures that paper-irrelevant metadata drift does not trigger false positives, while normalizing `models` via `_normal_model_backends` ensures backend aliasing (e.g., `minimax` vs `MiniMax-M2.5`) does not trigger false negatives.
- `CaptureManifestDiffReport` schema is `1.0`; adding a finding does not require a schema bump (findings are an open list).

Risks considered and rejected:
- **Byte-level hash fragility.** Hashing the entire JSON file would fail if the realized artifact includes `boundary` or `operator_label` but the planned stub does not. Mitigation: extract only the six paper-defined protocol fields into a stable dictionary and hash that.
- **Unnormalized model lists.** The planned and realized artifacts might list the paper backends in different orders or use different aliases. Mitigation: reuse the existing `_normal_model_backends` helper to produce a deterministic sorted tuple before hashing.

## Required Changes

1. `src/self_harness/capture_manifest_diff.py`:
   - Import `_normal_model_backends` from `self_harness._artifact_shapes` and `stable_json_dumps` (already imported).
   - Add a private helper `_protocol_core_hash(payload: Mapping[str, object]) -> str` that extracts `benchmark_protocol`, `models`, `evaluator`, `tool_set`, `decoding_budget`, and `fixed_across_variants`, normalizes `models` via `_normal_model_backends`, and returns the SHA-256 of the canonical JSON string.
   - Add `_fixed_protocol_findings(manifest: CaptureManifest, bundle: ReproductionBundle) -> list[CaptureManifestDiffFinding]`.
   - Look up the `fixed_protocol_config` entry in `manifest.entries` and `bundle.entries`. If either is missing, return no findings (the missing-class drift is already caught by `_entry_findings`).
   - If both exist, compute the planned hash from the manifest's `planned_artifact` and the realized hash from the bundle's artifact payload (via `read_artifact_payload`).
   - Fail closed with category `fixed-protocol-binding` if the hashes differ. Metadata should include `expected` (planned hash) and `actual` (realized hash).
   - Pass finding when they match.
   - Wire into `diff_capture_manifest_to_bundle` alongside `_network_control_findings`.
2. `tests/test_capture_manifest.py`:
   - Extend `test_capture_manifest_diff_matches_realized_bundle` to assert a `fixed-protocol-binding` pass finding exists.
   - Add `test_capture_manifest_diff_reports_fixed_protocol_drift`: modify the planned artifact in the manifest to change the `evaluator` or `tool_set` before writing it, then assert `report.ok is False` and a `fixed-protocol-binding` fail finding with expected/actual metadata.
3. Docs:
   - `docs/operations/capture_manifest.md` "Diff Against A Bundle" section: add `fixed-protocol drift` to the enumerated drift list.
   - `docs/architecture/productionization_brief.md`: add a P75 slice entry with standard boundary language.
   - `docs/architecture/schema_changelog.md`: add a P75 entry noting the new diff finding. No audit/corpus/readiness/manifest/bundle schema version change.

Stop conditions:
- All new and updated tests pass under `make capture-manifest-diff-check` and existing `make capture-manifest-check`, `make reproduction-bundle-check`.
- `report.reproduction_claimed is False` for all diff reports.
- No canonical audit hash rotation.
- No change to `CaptureManifest` schema, `ReproductionBundle` schema, readiness catalog, or release-candidate evidence inputs.

## Revised Plan

**P75: plan-vs-realized fixed-protocol binding in capture_manifest_diff**

1. `src/self_harness/capture_manifest_diff.py`:
   - Implement `_protocol_core_hash` extracting the 6 core fields, normalizing models, and hashing.
   - Implement `_fixed_protocol_findings` comparing planned vs realized hashes, skipping if the class is missing, failing on drift.
   - Call it from `diff_capture_manifest_to_bundle` within the manifest-and-bundle-loaded branch.
2. `tests/test_capture_manifest.py`:
   - Extend happy-path test to assert the pass finding.
   - Add drift test modifying the manifest's planned `fixed_protocol_config` artifact.
3. Docs updates as enumerated above.

## Remaining Open Questions

None blocking. One follow-up noted for future slices, explicitly out of scope for P75:

- Should `live_harbor_audit.trial_artifacts` record per-trial container image digests so a future slice can bind the `container_image_trust_report.images` set to the actually executed images? This requires audit artifact schema evolution and richer Harbor trial ingest; defer to P76+.
