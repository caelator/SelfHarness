CONVERGED: YES

## Verdict
Round 1 produced an architecturally sound, narrowly scoped P41 slice that closes a real production-readiness gap: operator policy material referenced by two independent offline gates (operator bundle paths vs promotion manifest digests) is currently not cross-verified. The proposed solution is offline-only, schema-preserving, follows existing operator/release patterns, and has explicit stop conditions. The remaining open questions are implementation-detail (helper reuse, fixture alignment) rather than architecture-blocking, and the highest-value call (whether to extend release-candidate evidence) can be answered now without further evidence.

## Critique
- **Gap is real and material**: Without digest binding, `make operator-check` and `make operator-promotion-check` could both pass against materially different operator policy files. This is exactly the class of release-trust inconsistency the productionization brief has been closing in P25–P40.
- **Scope discipline is correct**: Restricting to cross-binding (not a broader "trust graph") avoids duplication with P37's aggregator and keeps the slice implementable in a single step.
- **Stop conditions match the brief**: No live services, no schema mutation, no readiness-hash rotation, no reproduction-claim change, no provider SDKs, no bundle/promotion schema change. All align with stated constraints.
- **Verification surface is well-defined**: Inputs (bundle path, promotion manifest path, optional trusted public key), failure modes (missing, mismatch, extra, malformed, tampered), and ignored cases (retired entries) are explicit and testable.
- **Only residual architectural decision**: Whether release-candidate evidence aggregation should consume the new gate in P41 itself, or defer to P42. Prefer deferral: P41 then stays purely additive (no release-evidence schema bump, no aggregation wiring churn), and the aggregator extension becomes a small, separately reviewable follow-up.

## Required Changes
- Defer the `release_candidate_evidence.py` schema 1.1 bump and `--operator-policy-binding-result` aggregator wiring to P42. Keep P41 as a standalone gate with its own Make target and CI job step. This minimizes blast radius and keeps P41's value visible at the operator level without entangling release-evidence schema policy.
- Document the deferral explicitly in P41 docs so the next slice is pre-scoped.

## Revised Plan

### P41: Operator policy digest binding verification (standalone)

**Goal**: Offline cross-gate verification that the operator policy bundle paths and the operator policy promotion manifest digests reference the same operator-owned release-policy files with matching SHA-256 digests.

**Scope boundaries**:
- New standalone gate; no release-candidate evidence schema change in this slice.
- No audit/corpus/manifest schema change.
- No readiness-hash rotation.
- No reproduction-claim semantics change.
- No live services (Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model).
- No provider SDKs.
- No operator bundle or promotion manifest schema change (read-only verification of existing schemas).

**Source files**:
- `src/self_harness/operator_policy_binding.py` — `verify_policy_binding(bundle_path, promotion_path, trusted_public_key=None) -> PolicyBindingReport` with structured checks:
  - Every policy kind present in the bundle has exactly one matching `active` entry in the promotion manifest.
  - Every `active` promotion entry's SHA-256 digest matches the actual file at the bundle-referenced path.
  - No extra `active` promotion entries reference files not present in the bundle.
  - Promotion manifest signature verification (when trusted public key supplied) reuses P40 semantics.
- `scripts/operator_policy_binding_verify.py` — CLI wrapper writing structured JSON report to `dist/`.

**Tests** (`tests/test_operator_policy_binding.py`):
- Clean alignment passes.
- Missing policy in promotion manifest fails closed.
- Digest mismatch fails closed.
- Extra `active` promotion entry fails closed.
- `retired` promotion entries are ignored.
- Malformed bundle or promotion manifest fails closed.
- Signature verification passes when supplied; fails on tamper.
- Report explicitly asserts `reproduction_claimed=false` boundary.

**Docs** (`docs/operations/operator_policy_binding.md`):
- Operator workflow: bundle and promotion manifest must be authored together.
- Release/operator boundary language (not audit evidence).
- Relationship to `make operator-check`, `make operator-promotion-check`, and `make release-candidate-evidence`.
- Note that aggregator wiring is intentionally deferred to P42.

**Make/CI wiring**:
- `make operator-policy-binding-check` invoking the CLI with fixture bundle + fixture promotion manifest.
- `.github/workflows/ci.yml`: add step to `operator-preflight` job.
- Do **not** modify `release_candidate_evidence.py` or its fixtures in P41.

**Stop conditions**:
- No live Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact.
- No audit/corpus/manifest schema change.
- No readiness hash rotation.
- No reproduction-claim semantics change.
- No provider SDK dependencies.
- No operator bundle or promotion manifest schema change.
- No release-candidate evidence schema change (deferred to P42).

## Remaining Open Questions
- All remaining open questions from round 1 (helper reuse, bundle loader structure, existing fixture coverage) are implementation-detail and non-blocking for execution. They will be resolved naturally during implementation by inspecting `self_harness/operator_promotion.py`, the `OperatorPolicyBundle` loader, and `tests/fixtures/operator_{bundle,promotion}/`.

The plan is ready to execute.
