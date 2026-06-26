CONVERGED: YES

## Verdict

P74 should be **network-control attestation binding in `capture_manifest_diff`**. Evidence: the capture manifest's `planned_run` already declares `outbound_bandwidth_cap_bps` and `mirrored_resources` as planned controls (`src/self_harness/capture_manifest.py::_planned_run` validates them as positive int and non-empty string list), and the realized bundle's `network_resource_controls_attestation` artifact carries the matching fields (`src/self_harness/_artifact_shapes.py::_network_resource_controls_attestation`). Today `capture_manifest_diff.py::_source_findings` only compares per-entry `source.captured_at` to the planned window; nothing binds the realized network-control attestation back to `planned_run`. This is the natural plan-vs-realized seam to close after P73 closed the capture-run identity seam. It is narrow, offline, additive, requires no schema bump, stays inside the existing diff boundary string, and rotates no canonical hashes. Plan ready to execute.

## Critique

Evidence supporting convergence:
- `CaptureManifest.planned_run` is parsed and validated; `outbound_bandwidth_cap_bps` is a positive int and `mirrored_resources` is a non-empty string list (`capture_manifest.py`).
- `network_resource_controls_attestation` artifact shape (`_artifact_shapes.py`) requires `ok:true`, `mode:"live"`, non-empty `capture_run_id`, positive `outbound_bandwidth_cap_bps`, and a non-empty `mirrored_resources` string list — symmetric to the planned side.
- `capture_manifest_diff.py` already reads primary captured artifacts indirectly via `primary_capture_run_ids(bundle)` from `reproduction_bundle.py`, so reading one more primary artifact is consistent with the existing architecture. Reusing `reproduction_bundle.resolve_bundle_entry_path` and the existing `_read_json_object`-style loader in `reproduction_bundle` keeps the read contract in one module.
- `CaptureManifestDiffReport` schema is `1.0`; adding a finding does not require a schema bump (findings are an open list).
- The boundary string in `capture_manifest_diff.py` already constrains the report to comparing local manifest and bundle metadata without contacting external services; reading one local artifact JSON is in scope.

Risks considered and rejected:
- **Duplicating artifact-reading logic.** Mitigation: reuse `reproduction_bundle.resolve_bundle_entry_path` and `reproduction_bundle.load_reproduction_bundle`'s internal JSON read helper (or add a small public `read_artifact_payload(bundle, artifact_class) -> dict` helper if needed). Do not fork the path-resolution contract.
- **Skipping semantics.** The P72/P73 pattern skips a check when the relevant primary captured artifacts are absent. P74 should skip when the bundle has no `network_resource_controls_attestation` entry (advisory no-op), and fail closed when the entry exists but the artifact cannot be loaded or parsed — mirrors P73 fail-closed behavior.
- **Optional vs required binding.** Treating this as required (fail closed on drift) matches the existing capture-run-id-binding contract; operators expect the diff to be authoritative for plan-vs-realized drift.

## Required Changes

1. `src/self_harness/reproduction_bundle.py`: add a small public helper `read_artifact_payload(bundle, artifact_class) -> dict[str, object]` (or reuse an existing internal read path if exposed). The helper resolves the entry path via `resolve_bundle_entry_path`, reads the JSON object, and returns it; raise `ReproductionBundleError` on missing entry, missing file, or non-object JSON. This avoids duplicating read logic across `primary_capture_run_ids`, `_cross_artifact_*` checks, and the new diff check.
2. `src/self_harness/capture_manifest_diff.py`:
   - Add `_network_control_findings(manifest, bundle) -> list[CaptureManifestDiffFinding]`.
   - Look up the `network_resource_controls_attestation` entry via `bundle.entries`. If absent, return no findings (advisory skip, matching the P73 "no primary artifacts" skip semantics).
   - If present, load the artifact payload via the new helper. On `OSError`/`ReproductionBundleError`, return a single fail finding with category `network-control-binding` and the error string.
   - Compare `payload["outbound_bandwidth_cap_bps"]` to `manifest.planned_run["outbound_bandwidth_cap_bps"]`. Fail closed with category `network-control-binding` on mismatch; metadata includes `expected`/`actual`.
   - Compare `set(payload["mirrored_resources"])` to `set(manifest.planned_run["mirrored_resources"])`. Fail closed on set difference; metadata includes `expected`, `actual`, `missing`, `extra`.
   - Pass finding when both match.
   - Wire into `diff_capture_manifest_to_bundle` inside the `manifest is not None and bundle is not None` block, alongside `_capture_run_id_findings`.
3. `tests/test_capture_manifest.py`:
   - Extend `test_capture_manifest_diff_matches_realized_bundle` to assert a `network-control-binding` pass finding exists and that `expected == actual` for both `outbound_bandwidth_cap_bps` and `mirrored_resources`. This requires the fixture's `_class_shaped_payloads` `network_resource_controls_attestation` entry to carry the same values as the capture-manifest default `planned_run` (2_000_000 bps and `["https://resources.example/terminal-bench"]`). If the existing fixture helper does not align, extend `_class_shaped_payloads` / `_write_capture_manifest` defaults so they agree.
   - Add `test_capture_manifest_diff_reports_network_control_drift`: construct a bundle whose `network_resource_controls_attestation` artifact has a different `outbound_bandwidth_cap_bps` and a different `mirrored_resources` set; assert `report.ok is False` and a `network-control-binding` fail finding with the expected metadata.
   - Add (or document) a no-op case proving a bundle without `network_resource_controls_attestation` skips the check rather than failing.
4. Docs:
   - `docs/operations/capture_manifest.md` "Diff Against A Bundle" section: add `network-control drift` to the enumerated drift list (currently lists source provider, operator-label, signing-custody, bundle id, capture-run-id, capture-window).
   - `docs/architecture/productionization_brief.md`: add a P74 slice entry with the standard boundary language ("no live contact, no schema break, no canonical hash rotation, no reproduction claim").
   - `docs/architecture/schema_changelog.md`: add a P74 entry noting the new advisory-to-required diff finding. No audit/corpus/readiness/manifest/bundle schema version change.

Stop conditions:
- All new and updated tests pass under `make capture-manifest-diff-check` and the existing `make capture-manifest-check`, `make reproduction-bundle-check`.
- `report.reproduction_claimed is False` for all diff reports.
- No canonical audit hash rotation.
- No change to `CaptureManifest` schema, `ReproductionBundle` schema, readiness catalog, or release-candidate evidence inputs.
- The artifact-payload read helper is shared (no logic fork between bundle verification and diff layer).

## Revised Plan

**P74: plan-vs-realized network-control attestation binding in capture_manifest_diff**

1. `src/self_harness/reproduction_bundle.py`: add `read_artifact_payload(bundle, artifact_class) -> dict[str, object]` that resolves and reads the primary captured artifact JSON for one class, raising `ReproductionBundleError` on missing entry/file/malformed payload. Reuse from `primary_capture_run_ids` where natural, but behavior change is not required for convergence.
2. `src/self_harness/capture_manifest_diff.py`:
   - Import the helper (or the existing reproduction_bundle public name).
   - Add `_network_control_findings(manifest, bundle)` implementing the skip-on-absent and fail-closed-on-drift semantics above.
   - Call it from `diff_capture_manifest_to_bundle` within the manifest-and-bundle-loaded branch.
3. `tests/test_capture_manifest.py`:
   - Align fixture helpers so `_class_shaped_payloads()["network_resource_controls_attestation"]` and `_write_capture_manifest`'s default `planned_run` agree on `outbound_bandwidth_cap_bps` and `mirrored_resources`.
   - Extend the happy-path diff test to assert the new pass finding.
   - Add a drift test for bandwidth-cap and mirrored-resources mismatch.
4. Docs updates as enumerated above.

## Remaining Open Questions

None blocking. Two follow-ups noted for future slices, explicitly out of scope for P74:

1. Should the diff also bind the realized `fixed_protocol_config` byte hash to a planned hash recorded in the capture manifest, so that plan-vs-realized protocol drift is caught before bundle verification? Today the manifest stores the planned shape but not a planned byte hash; adding one would be a manifest-schema additive field and a separate slice.
2. Should `live_harbor_audit.trial_artifacts` record per-trial container image digests so a future slice can bind the `container_image_trust_report.images` set to the actually executed images? This requires audit artifact schema evolution and richer Harbor trial ingest; defer to P75+.
