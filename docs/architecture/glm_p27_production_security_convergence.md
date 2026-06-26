# Round 1

CONVERGED: NO

## Verdict

Initial candidate plan proposed: **P27 = Dependency vulnerability policy gate** (`pip-audit`-backed, with operator-owned policy file, Makefile target, CI job, and offline tests). This is the highest-value, fully locally implementable slice among the remaining production work. The plan is architecturally sound but needs tightening around scope, offline testability, and the relationship to the existing release-signing work before convergence.

## Critique

Evidence (from provided files):
- `productionization_brief.md` lists "vulnerability policy checks" explicitly as remaining production work.
- `RELEASE.md` and `Makefile` show `check`, `readiness`, `release-smoke` gates; no `vuln-check` target exists.
- `image_policy.py` establishes a reusable policy-file pattern (versioned JSON, active/retired/revoked statuses, strict fail-closed) that a vulnerability policy could mirror for consistency.
- `pyproject.toml` declares zero runtime dependencies, so the attack surface is the dev/release/test extras and the package itself.

Inference:
- Among remaining items, KMS/HSM wrappers and registry/OAuth/secret-manager helpers are provider-specific and not credibly testable offline without credentials; building reference stubs risks false coverage claims.
- Pre-run Harbor image discovery requires a live Harbor/Docker context to be meaningful; offline mocks would duplicate existing `build_harbor_run_command` logic without new production value.
- Sigstore/PyPI attestations are implementable and valuable but overlap heavily with P25/P26 release provenance + Ed25519 signing; they add a second signing mechanism rather than a new production-readiness dimension.
- Vulnerability policy checks address a distinct supply-chain risk (known-CVE runtime exposure), are fully offline-testable via pinned `pip-audit` output fixtures, and integrate naturally into `make check` / `make release-smoke`.

Risks in the candidate plan:
1. **Network/DB flakiness in CI**: `pip-audit` hits the PyPI/OSV APIs; non-deterministic failures would undermine the gate.
2. **Policy bypass abuse**: An overly permissive policy file could silently suppress real CVEs, defeating the purpose.
3. **Scope creep into SBOM-to-VEX**: Tempting to add full VEX (Vulnerability Exploitability eXchange) authoring; out of scope for a first slice.
4. **Dependency on `pip-audit` availability**: Adds a dev/release extra; must not affect `import self_harness` core parity (consistent with existing CI `core-import` job).
5. **Determinism vs. evolving CVE data**: The gate cannot be part of the canonical audit hash (which must be byte-stable); it is a release/material gate, not a paper-fidelity artifact.

## Required Changes

1. **Pin the audit mechanism**: Use `pip-audit` (or equivalent) invoked only through a dev dependency under `[project.optional-dependencies].release`; never a runtime dependency. CI `core-import` job must remain green without it.
2. **Offline testability**: Tests must use captured JSON fixtures (sample `pip-audit --format json` outputs) and assert policy decisions deterministically; do not run a real `pip-audit` in unit tests.
3. **Policy file design must mirror `image_policy.py`**: versioned schema, strict fail-closed, explicit allowlist-with-justification entries (CVE id, package, allowed status, justification text, expiry), no free-text suppression.
4. **Gate placement**: Add `make vuln-check` and wire it into `release-smoke` and the Release workflow, but NOT into `make readiness` (which is the paper-fidelity canonical hash gate and must remain byte-stable).
5. **Schema boundary**: Vulnerability policy is release/operator material like the image policy and keyring; it must not alter audit schema, corpus schema, manifest schema, or any reproduction-claim semantics.
6. **Documentation**: Add a `docs/operations/vulnerability_policy.md` describing policy authoring, rotation, and the fail-closed contract.

## Revised Plan

**P27: Dependency vulnerability policy gate**

Files (new):
- `src/self_harness/vulnerability_policy.py` — versioned policy loader, decision API mirroring `image_policy.py` (statuses: `active`/`accepted`, `retired`, `rejected`; fields: `vulnerability_id`, `package`, `justification`, `expires_on`).
- `scripts/vuln_check.py` — orchestrator: runs `pip-audit` against built artifacts or the environment, produces a finding set, evaluates against the policy, emits structured JSON report, exits nonzero on unallowed findings.
- `tests/fixtures/vuln/` — captured `pip-audit` JSON outputs for deterministic test cases (clean, one unpatched CVE, one policy-allowed CVE).
- `tests/vuln_policy_test.py` — policy loading, duplicate rejection, expiry handling, fail-closed behavior, parser tests.
- `docs/operations/vulnerability_policy.md` — operator guide.

Files (modified):
- `pyproject.toml` — add `vuln = ["pip-audit>=2.7"]` optional extra (kept out of `dev` to preserve lean dev install; CI release job installs it).
- `Makefile` — add `vuln-check` target invoking `scripts/vuln_check.py`; add to `release-smoke` prerequisites.
- `.github/workflows/release.yml` — run `make vuln-check` after `make build` / before provenance signing; gate the release on it.
- `.github/workflows/ci.yml` — add a dedicated `vuln-policy` job installing `[vuln]` extra and running offline tests (do not add to `core-import` or `test` matrix to keep them lean).
- `RELEASE.md` — document `make vuln-check` as a required release gate and the policy-file contract.
- `docs/architecture/productionization_brief.md` — record P27 as implemented when complete.

Tests:
- Policy load: valid, missing, invalid JSON, unsupported version, duplicate entries, malformed expiry.
- Decision logic: clean environment → allowed; unpatched CVE with no policy entry → fail-closed; CVE with active policy entry and non-expired → allowed; expired entry → fail-closed; `retired`/`rejected` status → fail-closed.
- `scripts/vuln_check.py`: subprocess-free pure function path over fixture JSON; asserts exit code 0 on clean, nonzero on unallowed, correct report schema.

Gates / stop conditions:
- `make vuln-check` runs against a fixture-driven offline path in tests.
- `make release-smoke` fails if `vuln-check` fails.
- Core `import self_harness` works without `[vuln]` extra (verified by existing `core-import` job unchanged).
- No audit/corpus/manifest schema change; no reproduction-claim semantics change.
- `make readiness` canonical hash unchanged.

## Remaining Open Questions

1. **Audit tool choice confirmation**: Is `pip-audit` acceptable as the recommended auditor, or should the policy layer be auditor-agnostic (accepting a normalized finding schema from any tool)? Recommend: ship `pip-audit` as the default orchestrator in `scripts/vuln_check.py`, but keep `vulnerability_policy.py` auditor-agnostic. (Non-blocking; default is fine for P27.)
2. **Policy enforcement scope**: Should the gate audit only the built wheel's declared dependencies (static), or also the build/test environment (transitive)? Recommend: P27 audits the built wheel + sdist metadata only; environment auditing is a future slice. (Non-blocking.)
3. **CI database freshness**: Should CI cache the OSV/PyPI advisory database for reproducibility, or accept that vuln-check is inherently time-sensitive and exclude it from the canonical readiness hash? Recommend the latter — vuln-check is a release/material gate, not a determinism gate. (Non-blocking; already reflected in plan.)

These are non-blocking; the plan is ready to execute subject to Codex local verification of the file inventory and the absence of an existing vuln-check target.

[usage] {"completion_tokens": 2659, "completion_tokens_details": {"reasoning_tokens": 915}, "prompt_tokens": 18649, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 21308}

---

# Round 2

CONVERGED: YES

## Verdict

The Round 1 candidate plan (P27 = Dependency vulnerability policy gate) is sound and ready to execute after Codex verifies the local repo facts. It is the highest-value, locally implementable slice among the remaining production work, and it correctly preserves paper-fidelity and the "no false Terminal-Bench reproduction claim" boundary. Remaining open questions are non-blocking and have safe defaults.

## Critique

Evidence (from provided files):
- `productionization_brief.md` explicitly lists "vulnerability policy checks" as remaining production work and describes deeper integration items (KMS/HSM, registry/OAuth, Harbor discovery, Sigstore/PyPI attestations) that are not credibly implementable offline.
- `image_policy.py` establishes the reusable policy-file pattern (versioned JSON, active/retired/revoked statuses, strict fail-closed, duplicate rejection, digest grammar) that the vulnerability policy should mirror.
- `Makefile` has `check`, `readiness`, `release-smoke` gates and no existing `vuln-check`; `readiness` is the paper-fidelity canonical-hash gate and must remain byte-stable.
- `pyproject.toml` declares zero runtime dependencies; dev/release extras are the only places to add `pip-audit` without breaking `core-import`.
- CI `release-smoke` job already installs `[dev,provenance,release]`, which is the natural seam for a `[vuln]` extra.
- `RELEASE.md` documents provenance signing sidecars as release material (not audit schema), establishing the precedent for treating vuln-check as release/operator material.

Inference:
- Vulnerability policy is the only remaining item that is (a) offline-testable with fixtures, (b) a distinct supply-chain dimension from existing P23-P26 image/provenance/signing work, (c) safely excludable from the canonical readiness hash, and (d) cheap to keep out of `core-import`.
- Auditor-agnostic policy + pip-audit default orchestrator is the right separation; it avoids vendor lock-in while shipping a working default.
- Static wheel-metadata auditing is the right P27 scope; environment/transitive auditing can be a later slice.

Architecture risks, all addressed by the plan:
1. Network/DB flakiness → tests use fixtures, CI gate is separate from `test`/`readiness`.
2. Suppression abuse → strict allowlist schema with justification + expiry, fail-closed default.
3. Determinism contamination → vuln-check excluded from `readiness` canonical hash.
4. Core import surface → `pip-audit` lives in optional extra only; `core-import` job unchanged.
5. Schema scope creep → no audit/corpus/manifest schema change; policy file is release material like image policy and keyring.

## Required Changes

None beyond what Round 1 already specified. The plan satisfies all required tightening:
- Optional-extra isolation, not runtime dep.
- Fixture-driven offline tests.
- Policy schema mirrors `image_policy.py`.
- Gate placement: `release-smoke` + Release workflow, not `readiness`.
- No schema boundary crossings.
- Operator docs included.

## Revised Plan

**P27: Dependency vulnerability policy gate** — implement as specified in Round 1:

New files:
- `src/self_harness/vulnerability_policy.py` — auditor-agnostic versioned policy loader + decision API mirroring `image_policy.py` (statuses: `active`/`accepted`, `retired`, `rejected`; fields: `vulnerability_id`, `package`, `justification`, `expires_on`).
- `scripts/vuln_check.py` — orchestrator using `pip-audit` against built wheel/sdist metadata, evaluates findings against policy, emits JSON report, exits nonzero on unallowed findings; pure-function path over fixture JSON for tests.
- `tests/fixtures/vuln/*.json` — captured pip-audit outputs (clean, unpatched CVE, policy-allowed CVE, expired-entry case).
- `tests/vuln_policy_test.py` — load/decision/orchestrator tests.
- `docs/operations/vulnerability_policy.md` — operator guide.

Modified files:
- `pyproject.toml` — add `vuln = ["pip-audit>=2.7"]` extra.
- `Makefile` — add `vuln-check` target; add as `release-smoke` prerequisite.
- `.github/workflows/release.yml` — run `make vuln-check` after build, before provenance signing/publish.
- `.github/workflows/ci.yml` — add dedicated `vuln-policy` job installing `[vuln]` extra and running offline tests; do not touch `core-import` or `test`.
- `RELEASE.md` — document `make vuln-check` as required release gate + policy contract.
- `docs/architecture/productionization_brief.md` — mark P27 implemented when complete.

Stop conditions:
- `make vuln-check` runs offline against fixtures in tests.
- `make release-smoke` fails on vuln-check failure.
- `import self_harness` works without `[vuln]` extra (unchanged `core-import` job).
- No audit/corpus/manifest schema change; `make readiness` canonical hash unchanged.

## Remaining Open Questions

All non-blocking with defaults already chosen:
1. **Auditor choice** — ship pip-audit as default orchestrator, keep policy layer auditor-agnostic. Default is fine.
2. **Enforcement scope** — P27 audits built wheel + sdist metadata only; environment/transitive audit is future work. Default is fine.
3. **CI DB freshness** — vuln-check is inherently time-sensitive; exclude from canonical readiness hash. Default is fine.

Codex should verify locally: no existing `vuln-check` target, no existing `vulnerability_policy.py`, and the `image_policy.py` pattern is as shown before implementing.

[usage] {"completion_tokens": 1285, "completion_tokens_details": {"reasoning_tokens": 36}, "prompt_tokens": 22162, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 23447}
