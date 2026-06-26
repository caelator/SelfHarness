# Round 1

# CONVERGED: NO

## Verdict
Initial architecture plan drafted and revised for P41. A concrete, locally implementable slice has been identified that addresses a real production-readiness gap in release-material consistency. However, one round of refinement is warranted to finalize the verification surface and confirm it doesn't overlap with existing P37/P40 logic before declaring convergence.

## Critique
**Initial plan critique**: My first instinct was a broad "release trust graph" that cross-verifies release provenance, operator promotion, operator bundle, corpus keyring, and trusted keys. This overreached in three ways:
1. **Redundancy risk**: P37's `release_candidate_evidence.py` already aggregates gate artifacts and checks for reproduction-claim leakage. A generic "trust graph" may duplicate rather than extend.
2. **Undefined scope**: "Cross-artifact consistency" was too vague to be actionable as a single slice.
3. **Verification gap not proven**: Without inspecting `self_harness/operator_promotion.py` and the operator bundle loader, I cannot confirm whether digest-level cross-binding already exists between promotion manifests and bundle-referenced policy files.

**Specific gap identified (inference, needs evidence)**: Based on the P36/P40 descriptions, the operator policy bundle (`OperatorPolicyBundle`) references policy files by path, while the operator promotion manifest binds policy files to SHA-256 digests. There appears to be no offline verification that:
- The promotion manifest's SHA-256 digest for a named policy matches the actual file content at the bundle's path
- The promotion manifest references the complete set of policies that the bundle requires
- A release build cannot pass with a promotion manifest that omits a policy present in the bundle (or vice versa)

This is a real production-readiness gap: two offline gates could pass with materially different operator policy material, and the release evidence aggregator wouldn't detect it.

## Required Changes
1. **Scope P41 narrowly** to "operator policy digest binding verification between promotion manifests and operator bundles" — not a broad trust graph.
2. **Label evidence vs inference** explicitly in the plan, since the exact overlap with P40's `verify_promotion_manifest` cannot be confirmed from the brief alone.
3. **Add explicit stop conditions** forbidding schema changes, readiness hash rotation, reproduction-claim semantics changes, and new live service dependencies.
4. **Define the exact verification inputs**: promotion manifest path, operator bundle path, and optionally a trusted public key for the promotion manifest signature.
5. **Specify the failure modes** the verification must catch: missing policy in promotion manifest, digest mismatch, extra policy in promotion manifest not in bundle, malformed bundle/promotion references.

## Revised Plan

### P41: Operator policy digest binding verification

**Goal**: Add an offline cross-gate verification that the operator policy promotion manifest and the operator policy bundle reference the same operator-owned policy files with matching SHA-256 digests. This closes a consistency gap where `make operator-check` and `make operator-promotion-check` could pass with divergent operator material.

**Evidence basis**: P36 describes `OperatorPolicyBundle` as path-only schema 1 with image policy, freshness policy, vulnerability policy, scanner DB freshness policy, and trusted public key references. P40 describes promotion manifests binding operator-owned release-policy files to SHA-256 digests, byte sizes, policy kinds, and lifecycle states. The brief does not describe any existing cross-binding verification.

**Source files**:
- `src/self_harness/operator_policy_binding.py` — `verify_policy_binding(bundle_path, promotion_path, trusted_public_key=None) -> PolicyBindingReport` with structured checks:
  - Every policy kind present in the bundle has exactly one matching `active` entry in the promotion manifest
  - Every `active` promotion entry's SHA-256 digest matches the actual file at the bundle's referenced path
  - No extra `active` promotion entries reference files not in the bundle
  - Promotion manifest signature verification (if trusted public key supplied) matches existing P40 verification semantics
- `scripts/operator_policy_binding_verify.py` — CLI wrapper writing structured JSON report to `dist/`

**Tests** (`tests/test_operator_policy_binding.py`):
- Clean case: bundle and promotion manifest align, digests match
- Missing policy in promotion manifest fails closed
- Digest mismatch fails closed
- Extra active promotion entry fails closed
- Retired promotion entries are ignored
- Malformed bundle or promotion manifest fails closed
- Signature verification passes when supplied, fails when tampered
- Reproduction-claim boundary asserted in report

**Docs** (`docs/operations/operator_policy_binding.md`):
- Operator workflow showing bundle and promotion manifest must be authored together
- Release-gate boundary: this check is release/operator material, not audit evidence
- Relationship to `make operator-check`, `make operator-promotion-check`, and `make release-candidate-evidence`

**Make/CI wiring**:
- `make operator-policy-binding-check` target invoking the CLI with fixture bundle and promotion manifest
- `.github/workflows/ci.yml`: add step to `operator-preflight` job
- Optional: extend `release_candidate_evidence.py` to accept `--operator-policy-binding-result` and block on failure (schema-minor bump to release-candidate evidence schema 1.1 — this is release/operator material, not audit schema)

**Stop conditions**:
- No live Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact
- No audit schema change
- No corpus schema change
- No readiness hash rotation
- No reproduction-claim semantics change
- No provider SDK dependencies
- No operator bundle or promotion manifest schema change (read-only verification of existing schemas)
- Release-candidate evidence schema bump is additive only (new optional field), gated behind explicit flag

## Remaining Open Questions
1. **Does `self_harness/operator_promotion.py` already expose digest verification helpers that P41 can reuse without duplication?** This determines whether P41 is a thin composition layer or needs its own digest computation logic. (Evidence needed: inspect `verify_promotion_manifest` implementation.)
2. **Does the `OperatorPolicyBundle` loader expose the policy file paths in a structured way suitable for cross-referencing?** If paths are only stored as free-form strings, P41 may need a small adapter. (Evidence needed: inspect bundle loader.)
3. **Should the release-candidate evidence schema bump to 1.1 be in-scope for P41, or deferred to P42?** Including it makes P41's value visible in the release gate; deferring keeps P41 purely additive. (Decision needed.)
4. **Are there existing tests covering operator bundle + promotion manifest fixture alignment?** If yes, P41 may be able to extend them rather than add a new test file. (Evidence needed: inspect `tests/fixtures/operator_bundle/` and `tests/fixtures/operator_promotion/`.)

These questions are non-blocking for proceeding to round 2; they determine implementation detail, not whether the slice is architecturally sound. The slice as revised is concrete, offline, schema-preserving, and addresses a material production-readiness gap.

[usage] {"completion_tokens": 3422, "completion_tokens_details": {"reasoning_tokens": 1944}, "prompt_tokens": 22909, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 26331}

---

# Round 2

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

[usage] {"completion_tokens": 1405, "completion_tokens_details": {"reasoning_tokens": 245}, "prompt_tokens": 25893, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 27298}
