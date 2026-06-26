CONVERGED: YES

## Verdict

P72 should be `capture_run_id` cross-artifact binding. This is the highest-value narrow gap: the codebase already stamps `capture_run_id` into several individual artifact outputs (split manifest, two-repeat evaluation report, fixed protocol config, network controls in `capture_extract.py`), and the capture envelope already carries a single `capture_run_id`, but no invariant currently prevents an operator from assembling a reproduction bundle whose artifacts were captured from different runs. Every existing cross-artifact invariant (`cross_artifact_protocol_binding`, `cross_artifact_model_protocol_binding`, `cross_artifact_harbor_version_binding`, `cross_artifact_split_evaluation_coverage`, `cross_artifact_audit_split_coverage`, `cross_artifact_evaluation_audit_outcomes`) checks *internal consistency*, but none checks *provenance identity*. Two artifacts from different live runs could pass every current check as long as their outcomes happen to agree.

Evidence:
- `_LIVE_TWO_REPEAT_EVALUATION_REPORT_FIELDS` in `_artifact_shapes.py` already includes `capture_run_id` but the validator does not enforce non-emptiness.
- `capture_extract.py` `extract_live_two_repeat_evaluation_report` copies `capture_run_id` from the capture envelope into the report payload, and the split manifest / fixed protocol / network controls extractors also stamp it.
- `_live_terminal_bench_split_manifest`, `_live_harbor_audit`, `_live_harbor_preflight_report`, `_fixed_protocol_config`, `_model_backend_preflight_report`, `_network_resource_controls_attestation`, `_container_image_trust_report` validators in `_artifact_shapes.py` do not currently require or even mention `capture_run_id`.
- `reproduction_bundle.py::_cross_artifact_invariants` has no capture-run identity check.
- The capture envelope (`extract_live_two_repeat_evaluation_report`) is the natural single source of truth for a run id.

Inference: live Harbor/Docker/model/PyPI/Sigstore dependencies remain unavailable, so this is the natural next offline-testable, code-level fidelity slice. It strengthens the paper Section 4.1 / Appendix A.1 evidence-contract boundary (single fixed protocol, fixed split, fixed environment, two repeats) by requiring the evidence to come from one run, not a patched-together set.

## Critique

The proposed slice is appropriate. Risks considered and rejected:

1. **Container image binding between `container_image_trust_report` and `live_harbor_audit` trial artifacts.** Higher value in principle, but `live_harbor_audit` trial artifacts do not currently record image digests per trial in the validated shape, so this would require schema evolution on the audit artifact and a richer extractor. That is a larger slice and would force fixture/hash rotation on a wider surface. Defer to P73+.
2. **Network-control attestation binding to Harbor preflight / audit.** Similar issue: no shared field today except `capture_run_id`. Could be added later as an additional cross-artifact check piggy-backing on the P72 `capture_run_id` invariant.
3. **Timestamp / capture-window binding back to the capture manifest's `planned_source.captured_after`/`captured_before`.** This is a P58 capture-manifest-vs-bundle diff concern, not a bundle self-consistency invariant. It belongs in `capture_manifest_diff`, not in `reproduction_bundle`. Defer.
4. **Broadening `capture_run_id` to all artifact classes including `audit_verify_report` and `release_candidate_evidence`.** These are derived post-capture artifacts, not captured live artifacts. They should remain exempt from the run-id binding to avoid implying that derived reports participate in the live-run contract.

The `capture_run_id` slice is narrow, purely additive, mirrors the existing `cross_artifact_*` pattern, and is fully offline-testable with rotated fixtures. It does not require any live dependency, schema break, or readiness-hash rotation of the canonical paper-fidelity audit hash (which is local-toy/dry-run path and unaffected).

## Required Changes

The revised plan below is ready to execute. Required invariants:

- Every artifact class that represents primary captured live evidence MUST carry a non-empty `capture_run_id` string. Primary captured classes: `live_terminal_bench_split_manifest`, `live_two_repeat_evaluation_report`, `fixed_protocol_config`, `live_harbor_preflight_report`, `container_image_trust_report`, `model_backend_preflight_report`, `network_resource_controls_attestation`, `live_harbor_audit`.
- Derived classes (`audit_verify_report`, `release_candidate_evidence`) are explicitly exempt and documented as such.
- A new bundle check `cross_artifact_capture_run_id_binding` MUST fail closed if any two primary captured artifacts disagree on `capture_run_id`, and MUST fail closed if exactly one primary captured artifact is present but missing `capture_run_id` (so a single-artifact bundle cannot silently bypass the invariant).
- The capture extractor MUST derive `capture_run_id` from a single source (the capture envelope) for every class that supports it, so operator happy-path bundles cannot accidentally stamp mismatched ids.
- No change to `reproduction_claimed:false` semantics.
- No change to readiness matrix, readiness catalog, canonical paper-fidelity audit hash, audit schema, corpus schema, or manifest schema.
- Fixture rotation is limited to reproduction-bundle, capture-extract, capture-admit, and reproduction-readiness fixtures that intentionally carry placeholder run ids; rotation must be deterministic and recorded.

## Revised Plan

**P72: cross-artifact capture-run identity binding**

1. **`src/self_harness/_artifact_shapes.py`**
   - Add `capture_run_id` to the closed field sets of the eight primary captured artifact classes listed above.
   - In each of those eight validators, require `capture_run_id` to be a non-empty string.
   - Do NOT add the field to `audit_verify_report` or `release_candidate_evidence`.
   - Update the `benchmark_reproduction_readiness.md` shape table to document `capture_run_id` as required for primary captured artifacts and exempt for derived reports.

2. **`src/self_harness/capture_extract.py`**
   - Extend `extract_live_terminal_bench_split_manifest`, `extract_live_harbor_preflight_report`, `extract_container_image_trust_report`, `extract_model_backend_preflight_report`, `extract_fixed_protocol_config`, `extract_network_resource_controls_attestation`, and `extract_live_harbor_audit` to accept and stamp a single `capture_run_id` sourced from the capture envelope (or equivalent explicit operator input).
   - For `extract_live_harbor_audit`, add a new required `capture_run_id` parameter (sourced from the same envelope) and stamp it on the output.
   - Re-validate through `_validated(...)` so any missing/malformed value fails closed at extraction time, not at bundle verification time.
   - Update `extract_artifact_from_paths` signature and the raw-flag allow-list in `capture_admit.py::_reject_unknown_flags` to permit a `capture_run_id` raw flag only when it is the single shared envelope value.

3. **`src/self_harness/reproduction_bundle.py`**
   - Add `_cross_artifact_capture_run_id_binding(bundle)` modeled exactly on `_cross_artifact_harbor_version_binding`:
     - Skip only when zero primary captured artifacts are present.
     - Fail closed when one or more primary captured artifacts are present but any is missing `capture_run_id`.
     - Fail closed when two or more primary captured artifacts disagree.
     - Emit metadata listing observed ids per artifact class.
   - Wire it into `_cross_artifact_invariants` after the existing checks.
   - No change to bundle schema, signature schema, or report schema.

4. **`src/self_harness/capture_admit.py`**
   - When extracting fixed-protocol-bound classes (`live_harbor_audit`, `live_two_repeat_evaluation_report`), also inject the shared `capture_run_id` from the capture envelope into the raw extraction call so all artifacts produced by one admission share the same id.
   - Document in the boundary string that admission produces a single-run bundle.

5. **Tests**
   - `tests/test_reproduction_bundle.py` (or equivalent): add cases for (a) all primary artifacts share `capture_run_id` and pass; (b) one primary artifact missing `capture_run_id` fails closed; (c) two primary artifacts disagree fails closed with metadata listing both ids; (d) bundle with only derived artifacts (`audit_verify_report`, `release_candidate_evidence`) skips the check.
   - `tests/test_capture_extract.py`: add cases proving every primary extractor stamps the envelope-provided `capture_run_id` and rejects an empty one.
   - `tests/test_capture_admit.py`: add a case proving a single admission run produces a bundle where all primary artifacts share one id.
   - `tests/test_artifact_shapes.py` (or co-located): add cases proving the eight primary validators reject missing/empty `capture_run_id` and that the two derived validators still accept payloads without it.

6. **Fixtures**
   - Update every fixture under `tests/fixtures/reproduction_bundle/`, `tests/fixtures/capture_extract/`, `tests/fixtures/capture_admit/`, and any reproduction-readiness fixture that materializes primary captured artifacts, to carry a single shared deterministic `capture_run_id` such as `"fixture-capture-run-p72"`.
   - Regenerate `dist/self-harness-reproduction-bundle.json`, `dist/self-harness-capture-admission.json`, `dist/self-harness-reproduction-readiness.json`, and `dist/self-harness-release-candidate-evidence.json` fixture hashes via the existing Make targets.
   - Do NOT regenerate the canonical paper-fidelity audit hash.

7. **Docs**
   - Update `docs/operations/benchmark_reproduction_readiness.md` required-shape table to note `capture_run_id` requirement for the eight primary captured classes and the explicit exemption for `audit_verify_report` and `release_candidate_evidence`.
   - Update `docs/operations/release_candidate_evidence.md` (if it enumerates bundle checks) to list `cross_artifact_capture_run_id_binding`.
   - Add a P72 entry to `docs/architecture/productionization_brief.md` following the existing slice format, including the explicit "no live contact, no schema break, no canonical hash rotation, no reproduction claim" boundary language.

**Stop conditions**

- All new and updated tests pass under `make check`, `make readiness`, `make reproduction-bundle-check`, `make capture-admit-check`, and `make capture-extract-check`.
- `make release-candidate-evidence` succeeds and the fixture hash rotation is recorded in the brief.
- Canonical paper-fidelity audit hash is unchanged.
- No artifact validator accepts a primary captured artifact missing `capture_run_id`.
- No bundle verifier accepts two primary captured artifacts with different `capture_run_id` values.
- Reproduction readiness still reports `reproduction_ready: false` by default.

## Remaining Open Questions

None blocking. Two follow-ups for future slices, explicitly out of scope for P72:

1. Should `live_harbor_audit` trial artifacts record per-trial container image digests so a future P73+ can bind `container_image_trust_report` images to the actual executed images? (Inference: yes, but it requires audit artifact schema evolution and richer Harbor trial ingest. Defer.)
2. Should the capture-manifest diff (`capture_manifest_diff.py`) additionally bind the realized bundle's shared `capture_run_id` back to `planned_run.run_id`? (Inference: yes as a P73 advisory diff check, but it is a plan-vs-realized concern, not a bundle self-consistency invariant, and belongs in the diff layer, not the bundle verifier.)
