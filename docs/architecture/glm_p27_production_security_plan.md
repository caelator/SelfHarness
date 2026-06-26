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
