CONVERGED: YES

## Verdict

The repo has accumulated strong per-artifact shape validators and two cross-artifact coverage checks (`cross_artifact_split_evaluation_coverage`, `cross_artifact_audit_split_coverage`). The remaining small, high-value, offline-testable invariant gap is **protocol binding**: nothing currently ties the declared `fixed_protocol_config` (model backends, evaluator, tool_set, decoding_budget, `fixed_across_variants`) to the `live_two_repeat_evaluation_report` or `live_harbor_audit` artifacts. An operator could ship a bundle where the evaluation and audit evidence were produced under a different protocol than the one the bundle advertises, and all existing checks would still pass. P68 closes this hole with one new required field, one new cross-artifact check, threaded extractor plumbing, and fixture tests — no live contact, no schema break to existing default paths, no reproduction claim.

## Critique

Evidence (from files shown):
- `_artifact_shapes.py` defines the closed field sets for `live_two_repeat_evaluation_report` and `live_harbor_audit`; neither references the fixed protocol config. Inference: protocol→evidence binding is currently unenforced.
- `reproduction_bundle._cross_artifact_invariants` only binds split/evaluation/audit by task ids; it never reads `fixed_protocol_config`. Inference: protocol identity is decorative today.
- `capture_extract.extract_live_two_repeat_evaluation_report` and `extract_live_harbor_audit` don't accept a protocol input; the boundary already forbids reproduction claims.

This is a true invariant gap, not doc polish. The fix is local, additive, hash-stable, and offline-testable. It does not require Harbor/Docker/model/PyPI/Sigstore. It does not change the canonical audit hash (default release path is unaffected). It does not satisfy any blocked readiness row; it tightens the *future* live path by preventing silent protocol drift in supplied evidence.

Risk to manage: ensure the new required field does not silently invalidate pre-existing P55/P58 fixture bundles. Since those fixtures are operator-authorable and shipped by us as test material, rotating them in the same slice is acceptable and expected, mirroring how P65/P66 rotated aggregation fields.

## Required Changes

1. Add `fixed_protocol_sha256` (lowercase 64-hex) as a required top-level field on both `live_two_repeat_evaluation_report` and `live_harbor_audit` artifact shapes. Compute the hash over the exact canonical bytes of the bundle's `fixed_protocol_config` entry.
2. Extend `_cross_artifact_invariants` in `reproduction_bundle.py` with a new `cross_artifact_protocol_binding` check that:
   - Reads the bundled `fixed_protocol_config` payload, recomputes its canonical sha256.
   - Asserts both `live_two_repeat_evaluation_report.fixed_protocol_sha256` and `live_harbor_audit.fixed_protocol_sha256` equal that hash.
   - Fails closed when either artifact is missing the field, when either is absent while the protocol is present, or on mismatch.
3. Thread the protocol reference through `capture_extract.py`:
   - `extract_live_two_repeat_evaluation_report` accepts an optional `fixed_protocol_result: Mapping` (or `fixed_protocol_sha256: str`) and stamps it; absent input fails closed when the report is being authored for a reproduction bundle (CLI flag `--fixed-protocol-result`).
   - `extract_live_harbor_audit` accepts the same.
4. Update the dispatch in `extract_artifact_from_paths` to plumb `--fixed-protocol-result` / `--fixed-protocol-sha256` for the two affected artifact classes.
5. Rotate P55 bundle-builder fixtures, P58 capture-manifest fixtures, and any committed `dist/` reproduction bundle fixtures so they include and bind the new field.
6. Tests (offline, fixture-backed): protocol hash present and matches; protocol hash missing on evaluation; protocol hash missing on audit; protocol hash mismatch on either; protocol artifact missing entirely while evaluation/audit reference a hash; capture-extract path fails closed when the protocol input is omitted for these two classes; no rotation of the canonical paper-fidelity audit hash.
7. No change to `benchmark_reproduction_requirements.json`, no new readiness dependency, no new artifact class, no change to the default `make check`/`make readiness` hashes for the non-reproduction release path.

## Revised Plan

P68 — Cross-artifact fixed-protocol binding for reproduction evidence.

Files to touch:
- `src/self_harness/_artifact_shapes.py` — extend `_LIVE_TWO_REPEAT_EVALUATION_REPORT_FIELDS`, `_live_two_repeat_evaluation_report`, `_live_harbor_audit`; add shared sha256 helper usage.
- `src/self_harness/reproduction_bundle.py` — add `_cross_artifact_protocol_binding` branch in `_cross_artifact_invariants`; ensure ordering is deterministic (run before or after coverage check, never both emit conflicting `fail`+`pass`).
- `src/self_harness/capture_extract.py` — accept and stamp `fixed_protocol_sha256` for the two extractors; reject unknown-field / reproduction-claim leakage as today.
- `scripts/capture_extract.py`, `src/self_harness/cli.py` (or wherever the installed CLI dispatch lives) — add `--fixed-protocol-result` / `--fixed-protocol-sha256` flags for `live_two_repeat_evaluation_report` and `live_harbor_audit`.
- `tests/fixtures/reproduction/**` and any P55/P58/P64 fixture bundles — regenerate with the new field.
- `tests/test_reproduction_bundle.py`, `tests/test_capture_extract.py`, `tests/test_artifact_shapes.py` — new cases listed above.

Invariants / stop conditions:
- All offline tests pass; mypy/ruff clean; `make readiness` unchanged; `make reproduction-readiness-check` still exits `2` (not ready) for the synthetic local bundle and never exits `0`.
- Bundle verification rejects any bundle where evaluation or audit references a protocol hash that differs from the included `fixed_protocol_config`, and rejects bundles where either artifact omits the field while a protocol is present.
- `reproduction_claimed:false` remains enforced on every new/rotated artifact.
- No live Harbor/Docker/model/PyPI/Sigstore/registry/scanner-db/cloud contact is introduced.

Non-goals (explicit):
- No attempt to verify that live evidence was *actually* produced under the protocol; this is a structural identity binding, not a cryptographic proof of execution lineage.
- No change to default release path, readiness matrix, or canonical paper-fidelity hash.

## Remaining Open Questions

None blocking. Two advisory notes for a future slice, not P68:
- Whether to also bind `fixed_protocol_sha256` into `live_terminal_bench_split_manifest` (today the split is identity-independent of the model/evaluator/tool budget, so binding it would over-constrain; defer unless the paper text requires it).
- Whether future live runs should sign the protocol config and embed the signature rather than only a sha256; that belongs in a later custody slice once a live capture actually exists.
