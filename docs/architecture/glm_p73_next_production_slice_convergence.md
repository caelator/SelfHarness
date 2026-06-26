# Round 1

CONVERGED: YES

## Verdict

The candidate slice (P73: bind realized bundle capture_run_id to capture_manifest.planned_run.run_id) is the correct highest-value next slice. P72 closed intra-bundle capture-run identity but left the plan-vs-realized seam explicitly open (see `docs/architecture/glm_p72_next_production_slice_convergence.md` Remaining Open Questions #2). This slice closes that seam in the diff layer, exactly where P72 deferred it. It is narrow, offline, additive, requires no schema bump, and stays within the existing boundary string. Plan ready to execute.

## Critique

Evidence supporting convergence:
- `reproduction_bundle.py::_cross_artifact_capture_run_id_binding` (P72) already defines `_PRIMARY_CAPTURED_ARTIFACT_CLASSES` and reads each primary artifact's `capture_run_id` from disk. This is reusable logic.
- `capture_manifest_diff.py` currently performs only metadata-level binding checks (`_binding_findings`, `_custody_findings`) and per-entry source window checks. It does NOT currently read artifact file contents.
- `capture_manifest.py::_planned_run` validates `run_id` as a non-empty string, so the comparison target is well-defined.
- `CaptureManifestDiffReport` schema is `1.0`; adding a finding does not require a schema bump (findings are an open list).
- The boundary string in `capture_manifest_diff.py` already constrains the report to "compares existing local manifest and bundle metadata without contacting Harbor, Docker, registries..." — reading local artifact JSON files to extract a stamped id is consistent with this boundary.

Design decision validated: the check belongs in the diff layer (plan-vs-realized), not in bundle self-consistency. P72's placement was correct; this is the complementary plan-binding check.

Risk considered and rejected: duplicating artifact-reading logic across `reproduction_bundle.py` and `capture_manifest_diff.py`. Mitigation — extract a small shared helper `primary_capture_run_ids(bundle) -> dict[class, str]` (or similar) into `reproduction_bundle.py` and reuse from both call sites. This keeps the artifact-reading contract in one module.

## Required Changes

1. Extract `primary_capture_run_ids(bundle: ReproductionBundle) -> tuple[dict[str, str], list[str]]` (or equivalent) from the body of `_cross_artifact_capture_run_id_binding` in `reproduction_bundle.py` so the diff layer reuses the exact same read-and-extract contract. Return both the observed `{artifact_class: capture_run_id}` map and the list of classes missing the field.
2. In `capture_manifest_diff.py`, add `_capture_run_id_findings(manifest, bundle)`:
   - Skip (return no findings) when the bundle has zero primary captured artifacts (mirrors P72 skip semantics).
   - When primary artifacts exist, read their `capture_run_id` via the shared helper.
   - Fail closed with category `capture-run-id-binding` if any primary artifact is missing `capture_run_id` (the bundle verifier should already have failed, but the diff must fail closed independently to remain authoritative when run without prior bundle verification).
   - Fail closed with category `capture-run-id-binding` if the shared `capture_run_id` != `manifest.planned_run["run_id"]`. Metadata must include `expected` (planned_run.run_id), `actual` (observed shared id), and `capture_run_ids_by_artifact`.
   - Pass finding when they match.
   - Wire into `diff_capture_manifest_to_bundle` inside the `manifest is not None and bundle is not None` block, before or after `_custody_findings` (order is non-binding).
3. Tests in `tests/test_capture_manifest.py`:
   - Extend `test_capture_manifest_diff_matches_realized_bundle` to assert a `capture-run-id-binding` pass finding exists and that the shared id equals the fixture's planned `run_id`. This requires the fixture bundle's primary artifacts to stamp a `capture_run_id` matching the manifest's `planned_run.run_id` (`"terminal-bench-2.0-live-001"` in the current fixture helper). If existing fixtures do not yet stamp this value, the fixture writer `_write_class_shaped_artifacts` / `_write_reproduction_bundle` must be extended to stamp it.
   - Add `test_capture_manifest_diff_reports_capture_run_id_drift`: construct a bundle whose primary artifacts share a `capture_run_id` different from the manifest's `planned_run.run_id`, assert `report.ok is False` and a `capture-run-id-binding` fail finding with expected/actual metadata.
   - Add a case proving a bundle with zero primary captured artifacts skips the check (advisory pass, no fail) — only meaningful if such a bundle is constructible in fixtures; otherwise document as not-applicable.
4. Fixture rotation: any reproduction-bundle / capture-admit / capture-extract fixture used by `make capture-manifest-diff-check` whose primary artifacts do not currently stamp `capture_run_id` equal to the paired manifest's `planned_run.run_id` must be rotated. Deterministic value (e.g., copy the manifest's planned_run.run_id). Record rotation in `docs/architecture/schema_changelog.md` under a new P73 entry.
5. Docs:
   - `docs/operations/capture_manifest.md` "Diff Against A Bundle" section: add `capture-run-id drift` to the enumerated drift list (currently lists source provider, operator-label, signing-custody, bundle id, capture-window).
   - `docs/architecture/productionization_brief.md`: add P73 slice entry with the standard boundary language ("no live contact, no schema break, no canonical hash rotation, no reproduction claim").
   - `docs/architecture/schema_changelog.md`: add P73 entry noting the new diff finding and any fixture hash rotation. No audit/corpus/readiness schema change.

Stop conditions:
- All new and updated tests pass under `make capture-manifest-diff-check` and the existing `make capture-manifest-check`, `make reproduction-bundle-check`.
- `report.reproduction_claimed is False` for all diff reports.
- No canonical audit hash rotation.
- No change to `CaptureManifest` schema, `ReproductionBundle` schema, readiness catalog, or release-candidate evidence inputs.
- The shared capture-run-id extraction helper is used by both `reproduction_bundle.py::_cross_artifact_capture_run_id_binding` and `capture_manifest_diff.py::_capture_run_id_findings` (no logic fork).

## Revised Plan

**P73: plan-vs-realized capture_run_id binding in capture_manifest_diff**

1. `src/self_harness/reproduction_bundle.py`: refactor `_cross_artifact_capture_run_id_binding` to call a new module-private helper `_read_primary_capture_run_ids(bundle) -> tuple[dict[str, str], list[str]]` returning (observed-by-class, missing-classes). No behavior change to bundle verification.
2. `src/self_harness/capture_manifest_diff.py`:
   - Import the helper (export it if necessary; prefer a `reproduction_bundle` public name like `read_primary_capture_run_ids`).
   - Add `_capture_run_id_findings(manifest, bundle) -> list[CaptureManifestDiffFinding]` implementing the fail-closed semantics above.
   - Call it from `diff_capture_manifest_to_bundle` within the manifest-and-bundle-loaded branch.
3. `tests/test_capture_manifest.py`:
   - Extend fixture builders (`_write_class_shaped_artifacts`, `_write_reproduction_bundle`, `_write_capture_manifest`) so the realized bundle's primary artifacts stamp `capture_run_id = manifest.planned_run.run_id` by default, enabling the happy-path diff test to assert the new pass finding.
   - Add drift test as specified above.
4. Fixtures under `tests/fixtures/reproduction_bundle/`, `tests/fixtures/capture_admit/`, `tests/fixtures/capture_extract/` (any consumed by capture-manifest-diff-check): stamp the shared `capture_run_id` to match the paired manifest's `planned_run.run_id`. Rotate affected deterministic dist hashes via existing Make targets.
5. Docs updates as enumerated above.

## Remaining Open Questions

None blocking. One follow-up noted for a future slice (out of scope for P73):

- Should the diff also bind the bundle's primary-artifact `captured_at` timestamps back to the manifest's `planned_source.captured_after`/`captured_before` window at the bundle level (not just per-source-metadata as today)? The current `_source_findings` only checks the bundle entry's `source.captured_at` metadata, not the artifact's internally-stamped timestamp. This is a P74+ advisory tightening; defer.

[usage] {"completion_tokens": 2651, "completion_tokens_details": {"reasoning_tokens": 828}, "prompt_tokens": 30508, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 33159}
