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
