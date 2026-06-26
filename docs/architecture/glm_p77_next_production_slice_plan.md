CONVERGED: YES

## Verdict

The highest-value P77 slice is **Harbor multi-arch manifest child-digest binding**, extending the P76 image-digest binding to correctly handle OCI manifest-list / multi-arch images. This closes a real correctness gap in the current P76 binding logic: today `cross_artifact_audit_image_binding` treats `live_harbor_audit.trial_artifacts[*].image_digest` and `container_image_trust_report.images[].digest` as the same digest namespace, but in a multi-arch Terminal-Bench deployment the trusted image entry records the OCI manifest digest while the executed container records a per-platform child config digest. They are different levels of the digest tree, so the current binding is only correct for single-arch images where manifest digest equals config digest.

Evidence supporting the gap:
- `extract_container_image_trust_report` in `src/self_harness/capture_extract.py` copies `digest` from Harbor discovery `discovered_images[*].digest`, which is the manifest-level digest. Harbor discovery also carries `child_digests` per image (`_HARBOR_IMAGE_FIELDS` already allows it), but those child digests are currently dropped during trust-report extraction.
- `live_harbor_audit.trial_artifacts[*].image_digest` (P76) is the executed container image digest, which for a multi-arch manifest is a child config digest, not the manifest digest.
- `_cross_artifact_audit_image_binding` in `src/self_harness/reproduction_bundle.py` requires exact set equality between audit digests and trust-report digests, which fails (or silently passes only by coincidence) when the two sides describe different levels of the same image tree.

The slice is narrow, offline, additive, follows the P72–P76 skip-on-absent / fail-closed-on-drift pattern, requires no canonical audit hash rotation when the new child-digest field is optional, and requires no live Harbor/Docker/model/PyPI/Sigstore contact. Plan ready to execute.

## Critique

Risks considered:

- **Breaking the P76 single-arch happy path.** Mitigation: make `container_image_trust_report.images[*].child_digests` optional. When `child_digests` is absent on all trust-report images, preserve the P76 behavior verbatim (bind audit digests to manifest `digest` values). When `child_digests` is present on at least one image, bind audit digests to the union of child-digest sets and record the manifest-to-child mapping in check metadata. This keeps existing committed fixtures valid.
- **Operator supplies child_digests on some images but not others.** Mitigation: fail closed when the trust-report image set is mixed (some images declare children, others do not). This is an operator evidence-shape contract, not a silent drift.
- **Audit declares image_digest but trust report has no children and digests differ.** This is the pre-P77 single-arch drift case; preserve P76 fail-closed behavior.
- **Audit omits image_digest but trust report declares children.** Skip the binding (advisory), consistent with P76 skip-when-audit-has-no-digest semantics.
- **Hash rotation.** The new optional `child_digests` field must be rejected by `_reject_unknown_fields` only if added carelessly. The trust-report validator currently has no closed-field check on `images[*]` beyond `name`/`digest` grammar, so adding an optional `child_digests` list is additive without schema bump. Existing fixtures omit it, so existing bundle/report/diff hashes remain stable.

Why not the other deferred P76 option (model preflight `request_sha256`): proposer-side LLM request logs are not currently a captured artifact class, so the binding target does not exist yet. That slice is better sequenced after a proposer-request-log artifact class is introduced, which is a larger scope. Harbor child-digest binding is self-contained inside the existing artifact set.

## Required Changes

1. `src/self_harness/_artifact_shapes.py::_container_image_trust_report`
   - Extend per-image validation to accept an optional `child_digests` field that, when present, must be a non-empty list of `sha256:<64 lowercase hex>` strings with no duplicates.
   - Add a helper `_sha256_image_digest_list(value, label)` mirroring the existing `_sha256_image_digest` grammar.
   - Do not require `child_digests`; existing fixtures without it remain valid.
2. `src/self_harness/capture_extract.py::extract_container_image_trust_report`
   - Thread optional `child_digests` from each Harbor discovered image into the trust-report image row when the discovered image supplies it. Sort child digests deterministically.
   - When the discovered image omits `child_digests`, omit the field on the trust-report row.
3. `src/self_harness/reproduction_bundle.py::_cross_artifact_audit_image_binding`
   - Compute `trust_child_digests` as the union of `child_digests` across trust-report images when at least one image declares children.
   - When trust-report images are mixed (some with children, some without), fail closed with metadata `mixed_child_digest_declarations`.
   - When all trust-report images declare children: bind audit digests against `trust_child_digests`, not the manifest `digest` set. Record `trust_manifest_digests`, `trust_child_digests`, `missing_from_trust_children`, and `extra_in_trust_children` in metadata.
   - When no trust-report image declares children: preserve P76 behavior exactly (bind audit digests against manifest `digest` set).
   - Skip (return `None`) when audit carries no image digests, unchanged from P76.
4. `src/self_harness/capture_manifest_diff.py::_audit_image_findings`
   - Mirror the bundle-side child-digest logic for planned-vs-realized-vs-trust three-way binding.
   - When planned or realized audit image digests are present and the realized trust report declares children, bind realized audit digests to realized trust child digests.
   - Preserve skip-when-both-absent semantics.
5. `tests/test_reproduction_readiness.py`
   - Do not modify `_class_shaped_payloads` defaults (preserves committed hashes).
   - Add a fixture-builder helper `_rewrite_container_image_trust_children(artifact_dir, child_digests)` that attaches `child_digests` to the trust-report image and rewrites the audit image_digest to one of the children.
6. `tests/test_reproduction_readiness.py` (or new `tests/test_reproduction_bundle_image_children.py`)
   - `test_reproduction_bundle_audit_image_binding_passes_with_child_digests`: trust report declares manifest digest + child_digests; audit image_digest is a child; assert `cross_artifact_audit_image_binding` passes with child-digest metadata.
   - `test_reproduction_bundle_audit_image_binding_rejects_child_digest_drift`: audit image_digest is not in any child_digests set; assert fail with `missing_from_trust_children`.
   - `test_reproduction_bundle_audit_image_binding_rejects_mixed_child_declarations`: trust report has one image with children and one without; assert fail with `mixed_child_digest_declarations`.
   - `test_reproduction_bundle_audit_image_binding_single_arch_still_passes`: trust report omits child_digests; audit image_digest matches manifest digest; assert P76 path still passes and metadata has no child-digest keys.
7. `tests/test_capture_extract.py`
   - Add a test where Harbor discovery `discovered_images[*].child_digests` is populated and assert the extracted trust-report image row carries `child_digests`.
   - Add a test where Harbor discovery child_digests contain a malformed digest and assert extraction fails closed.
8. `tests/test_capture_manifest.py`
   - Add `test_capture_manifest_diff_binds_audit_image_child_digests` covering planned/realized/trust child-digest pass.
   - Add `test_capture_manifest_diff_reports_audit_image_child_digest_drift`.
9. Docs:
   - `docs/operations/benchmark_reproduction_readiness.md`: extend the `container_image_trust_report` shape row to document optional per-image `child_digests` and the multi-arch binding semantics.
   - `docs/operations/capture_extract.md`: document that Harbor discovery `child_digests` flow into the trust report.
   - `docs/operations/capture_manifest.md`: extend the `audit-image-binding` drift description to cover multi-arch child-digest drift.
   - `docs/architecture/productionization_brief.md`: add a P77 slice entry with the standard boundary language.
   - `docs/architecture/schema_changelog.md`: add a P77 entry noting the new optional trust-report field and refined binding semantics. No schema version bump.

Stop conditions:
- All new and updated tests pass under `make capture-manifest-diff-check`, `make reproduction-bundle-check`, `make capture-extract-check`, and existing `make check`.
- `report.reproduction_claimed is False` for all diff and bundle reports.
- No canonical audit hash rotation.
- No change to `CaptureManifest`, `ReproductionBundle`, readiness catalog, or release-candidate evidence schemas.
- Existing committed fixture hashes in `tests/fixtures/release_candidate/` remain unchanged because the new field and refined check are skip-on-absent.

## Revised Plan

**P77: Harbor multi-arch manifest child-digest binding for container image trust evidence**

1. `src/self_harness/_artifact_shapes.py`: accept optional per-image `child_digests` list on `container_image_trust_report.images[]` with the same `sha256:<64 lowercase hex>` grammar and no duplicates.
2. `src/self_harness/capture_extract.py`: thread optional `child_digests` from Harbor discovery into the extracted trust-report image rows.
3. `src/self_harness/reproduction_bundle.py`: refine `_cross_artifact_audit_image_binding` to bind audit image digests to trust-report child-digest union when children are declared, fail closed on mixed declarations, and preserve P76 single-arch behavior when children are absent.
4. `src/self_harness/capture_manifest_diff.py`: refine `_audit_image_findings` with the same child-digest three-way binding semantics.
5. Tests covering extraction, bundle verification (child pass / child drift / mixed declaration / single-arch preservation), and capture-manifest diff (child pass / child drift).
6. Doc updates enumerated above.

## Remaining Open Questions

None blocking. One follow-up noted for future slices, explicitly out of scope for P77:

1. Should the project introduce a `proposer_llm_request_log` artifact class so that model-backend preflight `request_sha256` values can be bound to captured proposer-side LLM traffic? This is the deferred P76 model-preflight request-binding option and is better sequenced after that artifact class exists. Defer to P78+.
