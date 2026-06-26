CONVERGED: YES

## Verdict
The P71 candidate plan is ready to execute. It is a narrow, code-level, offline-testable extension of the existing P65–P70 cross-artifact invariant family in `reproduction_bundle.py`. It does not claim benchmark reproduction, does not add new artifact classes, and preserves the default release path's no-live-contact boundary. The Round 1 review already validated the same plan; no new blocking evidence has emerged.

## Critique
- **Evidence (validated from provided files):**
  - `src/self_harness/_artifact_shapes.py::_live_terminal_bench_split_manifest` does not currently require `harbor_version`, even though:
  - `src/self_harness/capture_extract.py::extract_live_terminal_bench_split_manifest` always emits a non-empty `harbor_version` field, and
  - `_live_harbor_preflight_report` already enforces a non-empty `harbor_version`.
  - No existing invariant in `reproduction_bundle.py::_cross_artifact_invariants` binds these two `harbor_version` values; the current `_cross_artifact_invariants` function wires protocol/model/coverage/outcome checks but not environment version.
  - Paper Appendix A.1 fixes Harbor as the execution environment for all Terminal-Bench-2.0 tasks.
- **Inference:** A divergence between `live_terminal_bench_split_manifest.harbor_version` and `live_harbor_preflight_report.harbor_version` is undetected protocol drift and a defensible production-fidelity gap.
- **Risk:** Tightening the split manifest shape may force fixture hash rotation in operator/release evidence. This is consistent with prior slice behavior (P65–P70) and explicitly bounded by the stop conditions.
- **Architecture risks:** minimal. The change is additive, single-check, and reuses the existing `_pass`/`_fail` check plumbing.

## Required Changes
None blocking. Implementer guidance:
1. In `_artifact_shapes.py::_live_terminal_bench_split_manifest`: add a non-empty `harbor_version` string requirement.
2. In `reproduction_bundle.py`: add `_cross_artifact_harbor_version_binding(bundle)` and call it from `_cross_artifact_invariants` after the existing protocol/model checks. Skip only when both `live_terminal_bench_split_manifest` and `live_harbor_preflight_report` are absent; fail closed when exactly one is present.
3. Update any committed split manifest fixtures to carry a non-empty `harbor_version`; regenerate operator/release evidence fixture hashes if the shape tightening rotates them.
4. Add tests covering: both present and equal (pass), both present and differ (fail), split missing `harbor_version` (fail), exactly one artifact present (fail), both absent (skip).

## Revised Plan
**P71 — Harbor environment version binding verification**

Files:
- `src/self_harness/_artifact_shapes.py`: require non-empty `harbor_version` in `_live_terminal_bench_split_manifest`.
- `src/self_harness/reproduction_bundle.py`: add `_cross_artifact_harbor_version_binding` and invoke it from `_cross_artifact_invariants`.
- `tests/test_reproduction_bundle.py` (or sibling): add the five cases above.
- `tests/fixtures/...`: ensure split manifest fixtures carry `harbor_version`; regenerate committed reproduction bundle report hash if the tightened shape contract forces it.

Invariant:
- `cross_artifact_harbor_version_binding` — when both `live_terminal_bench_split_manifest` and `live_harbor_preflight_report` are bundled, their `harbor_version` strings must be equal and non-empty. Fails closed when exactly one is present.

Stop conditions:
- Exactly one new cross-artifact check; no new artifact class; no readiness matrix, audit schema, or corpus schema change.
- `reproduction_claimed` remains false everywhere.
- No default release-path dependency on live Harbor/Docker/model/PyPI/Sigstore contact.
- `make readiness` canonical paper-fidelity audit hash unchanged; only operator/release fixture hashes may rotate due to shape contract tightening.

## Remaining Open Questions
None blocking. The only pre-edit verification ("do any committed split manifest fixtures lack `harbor_version`?") is implementer-local and does not affect plan soundness.
