CONVERGED: YES

## Verdict

The next highest-value narrow slice is **P76: per-trial container image digest binding** between `live_harbor_audit.trial_artifacts` and `container_image_trust_report.images`. Evidence: `_artifact_shapes.py::_live_harbor_audit` already records per-trial `task_id`, `captured`, `verifier_outcome`, and two attempt rows, and `container_image_trust_report` carries a closed `images` list with `name` and `sha256:` digests — but nothing today proves that the trusted image set actually executed for the captured Terminal-Bench trials. This is a real paper-fidelity gap for the paper's "Docker/Harbor execution" requirement (arXiv 2606.09498 v1, Section 4.1/Appendix A.1) because an operator could ship a `container_image_trust_report` for image A while the captured Harbor trials actually ran image B.

The slice is narrow, offline, additive, follows the P72–P75 pattern (skip-on-absent, fail-closed-on-drift), and requires no canonical audit hash rotation if the new check skips when either side lacks the binding material. Plan ready to execute.

## Critique

Evidence supporting convergence:
- `src/self_harness/_artifact_shapes.py::_live_harbor_audit` already iterates `trial_artifacts` with closed per-trial field validation; adding an optional `image_digest` field per trial is additive and does not break the schema-1.0 contract.
- `src/self_harness/_artifact_shapes.py::_container_image_trust_report` already enforces non-empty `images` with `name` and `sha256:` digest strings, giving the binding a stable target set.
- `src/self_harness/reproduction_bundle.py::_cross_artifact_invariants` is the natural home and already follows the skip-when-both-absent, fail-when-exactly-one-present pattern (see `_cross_artifact_model_protocol_binding`).
- The `live_harbor_audit` extractor (`src/self_harness/capture_extract.py::extract_live_harbor_audit`) already discovers trials via `discover_trials` and stamps shared fields; threading the executed image digest from operator-owned raw trial metadata is the natural capture-side seam.
- `CaptureManifestDiffReport` and `ReproductionBundleReport` schemas are `1.0` with open check/finding lists, so no schema bump is required.

Risks considered and rejected:
- **Forcing fixture hash rotation.** Mitigation: make the new check advisory-skip when both the audit trials and the trust report omit the binding material, and fail closed only when at least one side carries a digest. Existing fixtures in `tests/test_reproduction_readiness.py::_class_shaped_payloads` omit the field, so existing committed hashes remain stable. Operators who supply the new field get the stronger binding.
- **Multiple executed images per task.** The paper's fixed protocol runs one harness image per trial; modeling per-trial single `image_digest` keeps the binding closed. A future slice can extend to multi-image trials if needed.
- **Digest aliasing (registry path drift).** Mitigation: bind on the literal `sha256:<64 hex>` string from both sides without name normalization; the trust report already pins the executed image name and digest together.

## Required Changes

1. `src/self_harness/_artifact_shapes.py`:
   - Extend `_live_harbor_audit` per-trial validation to accept an optional `image_digest` field that, when present, must be a `sha256:`-prefixed digest matching the same grammar as `container_image_trust_report.images[].digest`. When absent, no error (keeps existing fixtures valid).
   - Add a small `_container_digest` helper or reuse the existing digest-prefix check already used by `_container_image_trust_report`.
2. `src/self_harness/capture_extract.py`:
   - Extend `discover_trials` consumption in `extract_live_harbor_audit` to optionally read an `image_digest` field from the operator-owned trial metadata (e.g., `metadata.json` field `image_digest`). When the raw trial metadata supplies it, stamp it into the per-trial artifact row. When absent, omit the field. No new CLI flag is required; the field flows from operator-owned Harbor run-dir material only.
3. `src/self_harness/reproduction_bundle.py`:
   - Add `_cross_artifact_audit_image_binding(bundle, audit_entry, trust_entry) -> ReproductionBundleCheck | None` mirroring `_cross_artifact_model_protocol_binding` semantics:
     - Skip (return `None`) when both `live_harbor_audit` and `container_image_trust_report` are absent, or when neither side carries any `image_digest` (advisory no-op preserving existing fixture hashes).
     - Fail closed when exactly one side is present or exactly one side carries digests.
     - Fail closed when any audit trial's `image_digest` is not in the trust report's digest set. Metadata must include `audit_digests` (sorted unique), `trust_digests` (sorted unique), `missing_from_trust` (audit digests not covered), and `extra_in_trust` (trust digests never executed).
   - Wire into `_cross_artifact_invariants` alongside `_cross_artifact_model_protocol_binding`.
4. `src/self_harness/capture_manifest_diff.py`:
   - Add `_audit_image_findings(manifest, bundle)` mirroring `_network_control_findings` / `_fixed_protocol_findings` skip-on-absent semantics. Look up the planned `live_harbor_audit` planned artifact in the manifest and the realized artifact in the bundle. Skip when the planned artifact's trial rows omit `image_digest` and the bundle has no `container_image_trust_report`. Otherwise compare planned audit digests, realized audit digests, and realized trust report digests as a three-way binding. Fail closed on drift; pass on match.
   - Wire into `diff_capture_manifest_to_bundle` alongside `_fixed_protocol_findings`.
5. `tests/test_reproduction_readiness.py`:
   - Extend `_class_shaped_payloads` so `live_harbor_audit.trial_artifacts[*]` may optionally carry `image_digest`. Do **not** add it to the default fixture (preserves committed hashes). Add a separate fixture builder path for the new binding tests.
6. `tests/test_capture_extract.py`:
   - Add a test where the Harbor run-dir trial `metadata.json` carries `image_digest` and assert the extracted `live_harbor_audit` trial row records it.
   - Add a test where the trial metadata digest is malformed and assert extraction fails closed.
7. `tests/test_reproduction_readiness.py` (or a new `tests/test_reproduction_bundle_image_binding.py`):
   - Add `test_reproduction_bundle_audit_image_binding_passes` supplying both artifacts with matching digests; assert the new cross-artifact check passes.
   - Add `test_reproduction_bundle_audit_image_binding_rejects_drift` supplying an audit digest not present in the trust report; assert fail with `missing_from_trust` metadata.
   - Add `test_reproduction_bundle_audit_image_binding_skips_when_both_absent` proving existing default fixtures still verify.
8. `tests/test_capture_manifest.py`:
   - Add `test_capture_manifest_diff_reports_audit_image_drift`.
   - Extend the existing happy-path diff test only if the default manifest is updated to include the field (recommend leaving the default fixture unchanged to avoid rotation; instead assert absence of the finding).
9. Docs:
   - `docs/operations/benchmark_reproduction_readiness.md`: extend the `live_harbor_audit` shape row to document the optional per-trial `image_digest` and the new `cross_artifact_audit_image_binding` invariant.
   - `docs/operations/capture_extract.md`: document that operator-supplied Harbor trial `metadata.json` may carry `image_digest`.
   - `docs/operations/capture_manifest.md`: add `audit-image drift` to the enumerated drift list.
   - `docs/architecture/productionization_brief.md`: add a P76 slice entry with the standard boundary language.
   - `docs/architecture/schema_changelog.md`: add a P76 entry noting the new optional field and new diff/bundle finding. No audit/corpus/readiness/manifest/bundle schema version change.

Stop conditions:
- All new and updated tests pass under `make capture-manifest-diff-check`, `make reproduction-bundle-check`, `make capture-extract-check`, and existing `make check`.
- `report.reproduction_claimed is False` for all diff and bundle reports.
- No canonical audit hash rotation.
- No change to `CaptureManifest`, `ReproductionBundle`, readiness catalog, or release-candidate evidence schemas.
- Existing committed fixture hashes in `tests/fixtures/release_candidate/` remain unchanged because the new field and new check are skip-on-absent.

## Revised Plan

**P76: per-trial container image digest binding for live Harbor audit evidence**

1. `src/self_harness/_artifact_shapes.py`: accept optional `image_digest` per `live_harbor_audit` trial row with the same `sha256:` grammar as `container_image_trust_report.images[].digest`.
2. `src/self_harness/capture_extract.py`: thread optional `image_digest` from operator-owned Harbor trial metadata into the extracted `live_harbor_audit` trial row.
3. `src/self_harness/reproduction_bundle.py`: add `_cross_artifact_audit_image_binding` with skip-on-absent and fail-closed-on-drift semantics; wire into `_cross_artifact_invariants`.
4. `src/self_harness/capture_manifest_diff.py`: add `_audit_image_findings` with the same skip/fail semantics; wire into `diff_capture_manifest_to_bundle`.
5. Tests covering extraction, bundle verification (pass / drift / skip), and capture-manifest diff (drift / skip).
6. Doc updates enumerated above.

## Remaining Open Questions

None blocking. Two follow-ups noted for future slices, explicitly out of scope for P76:

1. Should `live_harbor_audit.trial_artifacts[*]` also record an optional `harbor_artifact_digest` to bind against Harbor discovery `child_digests` for multi-arch manifest scenarios? Defer to P77+.
2. Should `model_backend_preflight_report.checks[*].metadata` carry per-check `request_sha256` so future slices can bind live model preflight evidence to captured proposer-side LLM request logs? Defer to P77+.
