CONVERGED: YES

## Verdict
The P28 candidate plan (Container Image Vulnerability Scanning Boundary) is the highest-value offline slice and is ready to execute. It advances the "container image vulnerability scanning" production objective without requiring live registries, Docker, or external trust services, and it reuses the P27 `VulnerabilityPolicy` decision framework unchanged.

## Critique
- Evidence (validated from repo context): P27 introduced `VulnerabilityPolicy`, `VulnerabilityFinding`, `findings_from_pip_audit_report`, `evaluate_vulnerability_policy`, `decision_to_jsonable`, and `scripts/vuln_check.py` with `--audit-json` and `--wheel` modes. The plan correctly extends these without altering policy semantics or audit/corpus schemas.
- Inference: Trivy JSON schema drift is a real risk. The plan's mitigation (parse only `Results[].Vulnerabilities[]`, fail closed on malformed structure) is consistent with P27's fail-closed behavior and is acceptable.
- Alternatives correctly rejected: Harbor image discovery needs real manifest schema evidence; Sigstore/PyPI attestations need live OIDC/TUF that cannot be exercised offline.
- Schema boundary respected: no changes to audit schema, corpus schema, readiness hash, or reproduction-claim semantics. Operator policy file remains operator-owned release material, consistent with P27's RELEASE.md treatment.

## Required Changes
- Add `findings_from_trivy_report(report)` in `src/self_harness/vulnerability_policy.py`, mapping `Results[].Vulnerabilities[]` to `VulnerabilityFinding` (`PkgName`→package, `VulnerabilityID`→vulnerability_id, `InstalledVersion`→installed_version, `FixedVersion`→fix_versions, `Status`/severity/`Title`/`Description`→description). Fail closed on non-object/non-list shapes via `VulnerabilityPolicyError`.
- Add `load_trivy_report(path)` alongside `load_pip_audit_report(path)`.
- Extend `scripts/vuln_check.py` with `--format {pip-audit,trivy}` (default `pip-audit`), routing the `--audit-json` path through the chosen parser. Keep `--wheel` flow pip-audit-only to avoid scope creep.
- Add fixture `tests/fixtures/vuln/trivy_report.json` and `tests/test_vulnerability_policy_trivy.py` covering: clean report, unallowed finding (missing policy), allowed finding (active policy), expired policy, non-active policy, malformed report, alias mapping, multi-Result aggregation.
- Add RELEASE.md subsection under "Vulnerability Policy Gate" describing `trivy --format json -o report.json <image>` then `python scripts/vuln_check.py --format trivy --audit-json report.json --policy policy.json`. Do not modify readiness canonical hash.
- Gates: existing `make check` covers new tests; CI `vuln-policy` job may add a Trivy-fixture offline invocation if desired but not required for convergence.

## Revised Plan
P28: Container Image Vulnerability Scanning Boundary
1. Parser + loader in `vulnerability_policy.py`.
2. CLI `--format` flag in `scripts/vuln_check.py`.
3. Fixture + pytest module for Trivy-shaped parsing and policy evaluation.
4. RELEASE.md operator documentation.
5. Stop condition: all offline tests pass; default pip-audit workflow unchanged; no schema/hash rotation.

## Remaining Open Questions
- Grype JSON support: defer (non-blocking). Keeping P28 tightly scoped to Trivy preserves the offline boundary and leaves a clean extension point.
- Whether CI `vuln-policy` job should exercise the Trivy fixture path: optional polish, not required for correctness.
