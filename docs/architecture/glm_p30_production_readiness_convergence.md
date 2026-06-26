# Round 1

CONVERGED: NO

## Verdict
The highest-value locally implementable P30 slice is **scanner report freshness validation**. It directly extends the P28/P29 offline scanner evaluation boundary (which currently has a known gap: a clean report can be replayed indefinitely) and closes a security hole without requiring live Harbor, Docker, registries, or Sigstore. Other remaining items (KMS/HSM wrappers, registry/OAuth helpers, Harbor discovery, Sigstore) are either provider-specific, require unavailable live services, or are blocked on future schema migrations.

## Critique
**Evidence:**
- `docs/architecture/productionization_brief.md` explicitly lists "automated scanner execution/report freshness checks" as remaining work and suggests scanner report freshness as a candidate.
- `src/self_harness/vulnerability_policy.py` and `scripts/vuln_check.py` currently accept any Trivy JSON report regardless of when it was generated. A clean report from months ago is accepted as current release evidence, which contradicts the operational note in `docs/operations/vulnerability_policy.md`: "Treat old reports as historical release records, not current safety proof."
- P29 introduced `TrivyImageReference` extraction from `Metadata.RepoDigests`. The same `Metadata` object in Trivy reports typically contains `CreatedAt` timestamps (inference based on standard Trivy schema; requires verification against fixtures).

**Risks:**
1. **Timestamp source ambiguity**: Trivy's exact field name for report creation time must be verified. If `Metadata.CreatedAt` is absent or formatted inconsistently, the freshness check must fail closed.
2. **Policy schema evolution**: Adding a freshness policy introduces a new operator-owned policy dimension. It must remain separate from the vulnerability policy (package-level allowances) and the image policy (allowlist) to avoid mixing concerns.
3. **Clock skew / reproducibility**: Tests must use fixed `--today` overrides (already supported via `--today` in `vuln_check.py`) to keep CI deterministic.
4. **Boundary creep**: This slice must remain offline parser tooling. It must not install Trivy, run Docker, or pull images.

## Required Changes
1. **Verify Trivy timestamp field**: Before implementing, confirm the actual field name and format in `tests/fixtures/vuln/trivy_report.json` (e.g., `Metadata.CreatedAt`, RFC3339 format). If the existing fixtures lack timestamps, new fixtures must be added.
2. **Fail closed on missing timestamps**: If a report lacks a timestamp, and a freshness policy is active, the check must fail (exit 2, `ok: false`).
3. **Separate freshness policy file**: Do not overload `vulnerability-policy.json`. Use a dedicated `--freshness-policy` flag pointing to a small JSON schema (e.g., `{"max_age_days": 7}` or `{"not_before": "2026-06-01"}`).

## Revised Plan

**P30: Scanner Report Freshness Validation**

**Goal:** Close the stale-report replay gap in offline Trivy evaluation. Operators can require that supplied Trivy reports are no older than a policy threshold.

**Files (Expected):**
- `src/self_harness/freshness_policy.py` (new): `FreshnessPolicy` dataclass, `load_freshness_policy`, `evaluate_freshness_policy`, `trivy_report_timestamp`.
- `scripts/vuln_check.py` (modified): Add `--freshness-policy` CLI flag; wire into `_evaluate_image_policy_report` or a sibling function; emit `freshness` block in JSON report.
- `tests/fixtures/vuln/trivy_report_with_timestamp.json` (new or existing fixture verified).
- `tests/test_freshness_policy.py` (new): Unit tests for parsing, policy evaluation, fail-closed on missing/malformed timestamp, age threshold logic.
- `tests/test_vulnerability_policy_trivy.py` (modified): Add CLI integration tests for `--freshness-policy`.
- `docs/operations/vulnerability_policy.md` (modified): Document freshness policy schema and workflow.
- `docs/architecture/p30_scanner_report_freshness_brief.md` (new): Architecture brief.

**Schema Boundaries:**
- **No change** to: audit schemas, corpus schema, manifest schema, readiness hash, reproduction-claim semantics, release provenance schema.
- **New operator policy file**: `freshness-policy.json` schema version `1`. Example: `{"policy_version": "1", "max_age_days": 7}`.
- **Report schema**: `scripts/vuln_check.py` JSON output adds a `freshness` block: `{"required": bool, "allowed": bool, "code": str, "message": str, "report_timestamp": str | null, "evaluated_at": str}`. Report schema version remains `1.0` (additive field).

**Tests:**
1. Parser extracts `Metadata.CreatedAt` (or verified field) into ISO date.
2. Policy `max_age_days: 7` accepts a report timestamped today; rejects one timestamped 30 days ago.
3. Policy `not_before: 2026-06-01` accepts/rejects based on absolute date.
4. Missing timestamp with active policy fails closed (`code: "missing-timestamp"`).
5. Malformed timestamp fails closed (`code: "malformed-timestamp"`).
6. Freshness policy not supplied = behavior unchanged (backward compatible).
7. CLI integration: `--freshness-policy` with `--format trivy --audit-json` produces expected JSON block and exit code.

**Gates:**
- `make check` (ruff, mypy, pytest).
- `make readiness` (must remain green; no audit hash change).
- No new CI jobs required; covered by existing matrix.

**Stop Conditions:**
- If Trivy fixtures in repo lack timestamp metadata and cannot be synthesized deterministically, block and request fixture regeneration from a real Trivy run.
- If freshness policy schema proves ambiguous (e.g., need both `max_age_days` and `not_before`), scope to `max_age_days` only for P30 and defer complex policies.

## Remaining Open Questions
1. **Does the existing Trivy fixture (`tests/fixtures/vuln/trivy_report.json`) contain `Metadata.CreatedAt`?** If not, the implementation must add a new fixture or the slice is blocked on fixture availability. (To be verified locally by Codex.)
2. **Should freshness apply to pip-audit reports too?** P28/P29 focused on Trivy. pip-audit reports also have freshness concerns. Recommendation: defer pip-audit freshness to P31 to keep P30 tightly scoped to container scanner reports, but confirm this is acceptable.
3. **Is `max_age_days` sufficient, or do operators need business-day calendars?** Recommendation: use calendar days for P30 simplicity; defer business-day logic unless evidence shows demand.

**Next Action for Codex:** Verify the Trivy fixture timestamp field structure locally. If present, proceed with implementation. If absent, synthesize a fixture based on documented Trivy JSON schema and proceed.

[usage] {"completion_tokens": 2083, "completion_tokens_details": {"reasoning_tokens": 588}, "prompt_tokens": 18025, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 20108}

---

# Round 2

CONVERGED: YES

## Verdict
The P30 scanner report freshness validation slice is architecturally sound and ready to execute. It is the highest-value locally implementable production-readiness work after P29, directly closing a known security gap (stale report replay) while respecting all hard constraints: no live Harbor/Docker/registries/PyPI/Sigstore, no paper-fidelity regression, no false reproduction claims, no readiness hash change. The candidate plan separates concerns cleanly, fails closed on ambiguity, and preserves backward compatibility.

## Critique
**Evidence-supported strengths:**
- `vulnerability_policy.py` already parses Trivy `Metadata` (P29 uses `Metadata.RepoDigests`), so `Metadata.CreatedAt` extraction is a natural extension of existing parsing patterns.
- `scripts/vuln_check.py` already supports `--today` for deterministic date evaluation, eliminating clock-skew test flakiness.
- `docs/operations/vulnerability_policy.md` explicitly warns: "Treat old reports as historical release records, not current safety proof." P30 operationalizes this warning.
- Existing report schema is additive-friendly (image_policy block was added in P29 without version bump).

**Inference-based confirmations:**
- Standard Trivy JSON schema includes `SchemaVersion`, `CreatedAt`, `NextUpdate` under `Metadata`. The plan's fail-closed stance handles any field-name variance safely.
- `freshness-policy.json` as a separate operator-owned file mirrors the established `image-policy.json` / `vulnerability-policy.json` pattern.

**Material risks addressed:**
1. Timestamp source ambiguity → fail-closed with explicit `missing-timestamp` / `malformed-timestamp` codes.
2. Schema mixing → dedicated `freshness-policy.json` with schema version `1`.
3. Clock skew → reuse existing `--today` override.
4. Boundary creep → explicitly offline parser-only, no Trivy/Docker/registry invocation.

## Required Changes
None blocking. Minor refinements for implementation clarity:
1. **Fixture strategy**: If existing fixtures lack `Metadata.CreatedAt`, synthesize new fixtures rather than mutating existing ones (preserve P28/P29 test invariants).
2. **Report schema field naming**: Use `freshness` as top-level report key (parallel to `image_policy`), not nested under a new parent object.
3. **Exit code semantics**: Document that freshness failure produces exit code 2 with `ok: false`, consistent with image-policy denial behavior.

## Revised Plan

**P30: Scanner Report Freshness Validation** — proceed as specified in round 1 with the above clarifications. Key elements confirmed:

- **New file**: `src/self_harness/freshness_policy.py` — `FreshnessPolicy`, `load_freshness_policy`, `evaluate_freshness_policy`, `trivy_report_timestamp`.
- **Modified**: `scripts/vuln_check.py` — add `--freshness-policy`, emit `freshness` block in report.
- **New tests**: `tests/test_freshness_policy.py` covering max_age_days accept/reject, not_before accept/reject, missing-timestamp fail-closed, malformed-timestamp fail-closed, backward compatibility (no policy = no change).
- **Modified tests**: `tests/test_vulnerability_policy_trivy.py` — CLI integration for `--freshness-policy`.
- **Docs**: Update `docs/operations/vulnerability_policy.md` and add `docs/architecture/p30_scanner_report_freshness_brief.md`.
- **Schema boundaries**: No changes to audit/corpus/manifest/readiness-hash/provenance. New operator-owned `freshness-policy.json` schema v1. Additive `freshness` block in vuln-check report (schema stays `1.0`).
- **Gates**: `make check`, `make readiness` (must remain green).
- **Stop conditions**: If Trivy fixtures lack timestamp and cannot be deterministically synthesized, halt and request real Trivy fixture regeneration. Scope to `max_age_days` only if `not_before` proves ambiguous.
- **Deferred**: pip-audit freshness (P31 candidate), business-day calendars, multi-scanner freshness unification.

## Remaining Open Questions
None blocking convergence. Implementation-time decisions for Codex:
1. Whether to also extract `Metadata.NextUpdate` (advisory database freshness) — recommend defer to P31; P30 focuses on report creation time only.
2. Whether `freshness-policy.json` should support multiple rules (e.g., per-scanner) — recommend single global rule for P30 simplicity.
3. Exact JSON field name verification (`CreatedAt` vs `created_at`) — handled by fail-closed parser; Codex verifies against fixtures during implementation.

[usage] {"completion_tokens": 1350, "completion_tokens_details": {"reasoning_tokens": 369}, "prompt_tokens": 21039, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 22389}
