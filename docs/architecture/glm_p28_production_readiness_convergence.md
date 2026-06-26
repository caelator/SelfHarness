# Round 1

CONVERGED: NO
## Verdict
The highest-value implementable slice for P28 is the **Container Image Vulnerability Policy Boundary**. P27 established a robust `VulnerabilityPolicy` and `VulnerabilityFinding` framework for Python dependencies. Extending this offline boundary to parse standard container image scanner reports (like Trivy) moves the "container image vulnerability scanning" production objective forward without requiring live external services, registries, or Docker. It provides immediate, testable security value for release and operations gates.

## Critique
- **Alternative considered: Pre-run Harbor Image Discovery.** While valuable for live runs, it heavily depends on the exact schema of the Terminal-Bench manifest and may require recursive, heuristic JSON searching if the schema doesn't explicitly declare container images. This makes it brittle without strong evidence of the corpus structure.
- **Alternative considered: Sigstore/PyPI Attestations.** These require external OIDC flows or external signing infrastructure that cannot be fully exercised or tested in a purely offline, local environment without mocking the entire Sigstore TUF infrastructure.
- **Risk of P28 Slice:** Trivy JSON output schemas can vary slightly by version. We must target a stable, documented subset of the Trivy JSON schema (typically the standard `Results[].Vulnerabilities[]` structure) and treat missing fields safely.

## Required Changes
- The `VulnerabilityPolicy` framework in P27 must be reused exactly. We are only adding a new ingestion parser (`findings_from_trivy_report`), not altering the policy decision logic.
- The `scripts/vuln_check.py` CLI must be updated to accept a `--format` flag (defaulting to `pip-audit`) to switch between report parsers, preventing any breaking changes to the P27 workflow.

## Revised Plan
**P28: Container Image Vulnerability Scanning Boundary**

1. **Parsing Logic (`src/self_harness/vulnerability_policy.py`)**:
   - Add `findings_from_trivy_report(report: dict[str, Any]) -> tuple[VulnerabilityFinding, ...]`.
   - Map Trivy's `Results[].Vulnerabilities[]` to `VulnerabilityFinding`.
   - Map `PkgName` to `package`, `VulnerabilityID` to `vulnerability_id`, `InstalledVersion` to `installed_version`, `FixedVersion` to `fix_versions`, and `Title`/`Description` to `description`.
   - Include strict validation to fail closed if the Trivy JSON structure is malformed.
2. **CLI Extension (`scripts/vuln_check.py`)**:
   - Add `--format` argument with choices `pip-audit` and `trivy` (default: `pip-audit`).
   - When `--format trivy` is provided, route the loaded JSON through `findings_from_trivy_report` instead of `findings_from_pip_audit_report`.
3. **Fixtures & Tests**:
   - Add `tests/fixtures/vuln/trivy_report.json` containing a realistic, sample Trivy report.
   - Add `tests/test_vulnerability_policy_trivy.py` to test parsing, alias mapping, and policy evaluation against Trivy-shaped findings (clean, unallowed, allowed, and missing policy scenarios).
4. **Documentation (`RELEASE.md`)**:
   - Add a section under "Vulnerability Policy Gate" explaining how operators can scan a container image using `trivy --format json -o report.json <image>` and evaluate it against the Self-Harness policy using `python scripts/vuln_check.py --format trivy --audit-json report.json --policy policy.json`.
5. **Gates**:
   - No changes to `Makefile` targets are strictly required for the default build, but the new parser must be covered by `make check`.
6. **Stop Conditions**:
   - The slice is complete when the Trivy parser handles standard reports, the CLI supports the format flag, and all tests pass offline.

## Remaining Open Questions
- Should we also add support for Grype JSON format in this slice, or defer to a future patch? (Recommendation: Defer to keep the slice focused and tightly scoped).

[usage] {"completion_tokens": 4562, "completion_tokens_details": {"reasoning_tokens": 3695}, "prompt_tokens": 22380, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 26942}

---

# Round 2

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

[usage] {"completion_tokens": 849, "completion_tokens_details": {"reasoning_tokens": 66}, "prompt_tokens": 24142, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 24991}
