# Round 1

CONVERGED: NO

## Verdict
The highest-value local-implementable slice is **P36: Operator Policy Bundle & Consolidated Offline Preflight**. It consolidates the currently scattered operator-owned policy files (image, freshness, vuln, scanner-db-freshness, trusted public keys) into a single versioned bundle and provides one CLI/make entrypoint that runs all offline/dry-run/replay validations against that bundle. This hardens the release/operator boundary, reduces flag-wiring errors, and requires no live Harbor/Docker/Trivy/cloud. The plan is architecturally sound but should pass one more review cycle to confirm scope boundaries against future breaking-schema migration needs.

## Critique
- **Initial idea:** Add provider-specific KMS/HSM mock wrappers or Docker-config parsers.
- **Critique:** High secret-handling risk, low marginal value over the existing external signer protocol, and easily mishandled without real provider SDKs. Deferred.
- **Initial idea:** Formalize breaking-schema migration shims now.
- **Critique:** No concrete breaking change is planned; adding empty hooks is speculative. The current additive metadata migrator is sufficient until a breaking field is required.
- **Chosen direction:** A policy bundle is purely additive, local, and directly improves production operator UX. The main risk is over-stuffing the bundle schema; it should start minimal (paths + metadata) and avoid duplicating policy evaluation logic.

## Required Changes
- Keep the bundle schema minimal: versioned JSON with paths to existing policy files and optional trusted public key references.
- Do not embed secret material or private keys in the bundle.
- The preflight script must reuse existing `scanner_run.py`, `scanner_db_update.py`, `harbor_discovery.py`, and `vuln_check.py` logic via imports or subprocess dry-run modes—do not duplicate policy parsing.
- Add explicit "release/operator material, not benchmark reproduction evidence" boundary language to all new artifacts and docs.

## Revised Plan
**P36: Operator Policy Bundle & Consolidated Offline Preflight**

1. **Schema & Loader**
   - Add `src/self_harness/operator_bundle.py` with `OperatorPolicyBundle` dataclass and schema `1.0`.
   - Fields: `bundle_version`, `owner`, `expires_on`, and optional path references to `image_policy`, `freshness_policy`, `vulnerability_policy`, `scanner_db_freshness_policy`, and `trusted_public_keys` (list).
   - Strict loader: reject missing files, malformed JSON, unknown versions, expired bundles.

2. **Preflight Orchestrator**
   - Add `scripts/operator_preflight.py`.
   - Loads bundle, then runs:
     - Scanner dry-run (via `build_trivy_command`).
     - Scanner DB update dry-run (via `build_trivy_db_update_command`).
     - Harbor discovery dry-run/replay (if Harbor config present).
     - Policy file parse validation for all referenced policies.
   - Emits a structured JSON report (`ok`, `checks[]`, `boundary`) with exit code 2 on any failure.

3. **Tests**
   - Bundle parsing: valid, missing file, expired, malformed.
   - Preflight: end-to-end dry-run using fixtures, policy rejection propagation, missing-config behavior.

4. **Docs & Gates**
   - `docs/operations/operator_bundle.md` describing authoring, validation, and boundary.
   - `make operator-check` target.
   - CI job running `operator-check` with a fixture bundle.

5. **Boundaries**
   - No new external dependencies.
   - No mutation of audit schemas, corpus schemas, readiness hashes, or reproduction claims.
   - Bundle is release/operator material only.

## Remaining Open Questions
- Should the bundle support inline (embedded) policy JSON for small artifacts, or strictly file paths? (Recommendation: paths only for v1 to avoid secret/bloat risks.)
- Should `operator-preflight` be exposed as `self-harness operator-preflight` or remain a script? (Recommendation: script for now, consistent with other operator tooling.)

[usage] {"completion_tokens": 3055, "completion_tokens_details": {"reasoning_tokens": 2204}, "prompt_tokens": 29986, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 33041}

---

# Round 2

CONVERGED: YES

## Verdict
The P36 plan (Operator Policy Bundle & Consolidated Offline Preflight) is the highest-value local-implementable slice and is now architecturally sound. It consolidates already-shipped operator-owned policy files behind a versioned bundle, adds a single offline preflight entrypoint that reuses existing scanner/discovery/policy code, and adds explicit release/operator boundary language. It introduces no external dependencies, no audit/corpus/readiness/reproduction changes, and is fully testable offline with fixtures.

## Critique
- The earlier candidate plan correctly deferred KMS/HSM wrappers, breaking-schema migration shims, and live Harbor/Docker/Trivy work; those remain out of scope.
- Embedding inline policy JSON or secrets in the bundle was correctly rejected. Paths-only v1 keeps the surface minimal and avoids secret/bloat risk.
- The preflight orchestrator must not reimplement parsing. The plan now mandates reuse via `scanner_run`, `scanner_db_update`, `harbor_discovery`, and `vuln_check`, which is consistent with the existing script/Makefile structure.
- The plan correctly treats the bundle as release/operator material only, matching the existing boundary language in `RELEASE.md`, `docs/operations/*`, and the audit migration copy-only model.
- Open questions are non-blocking: paths-only for v1 and script-style preflight are both consistent with current conventions and can be revisited in a later minor slice if needed.

## Required Changes
None beyond what the revised plan already captures. Before implementation, the slice must:
- Keep `OperatorPolicyBundle` schema minimal (`bundle_version`, `owner`, `expires_on`, optional path references to existing policy files, optional `trusted_public_keys` list).
- Strict loader: reject missing files, malformed JSON, unknown versions, expired bundles.
- Preflight reuses existing logic via imports/subprocess dry-run; no policy parsing duplication.
- No secrets, private keys, or inline policy content in the bundle.
- New docs/artifacts carry explicit "release/operator material, not benchmark reproduction evidence" language.
- `make operator-check` and a CI job exercise the fixture bundle offline.

## Remaining Open Questions
- Paths-only vs inline policy JSON: resolved for v1 as paths-only. Can be revisited in a future minor if a small inline policy proves necessary.
- `self-harness operator-preflight` CLI vs script: resolved for now as a script under `scripts/`, consistent with sibling operator tooling. Promoting to a CLI subcommand can be a later additive change.

These are non-blocking and do not prevent implementation.

[usage] {"completion_tokens": 597, "completion_tokens_details": {"reasoning_tokens": 56}, "prompt_tokens": 31713, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 32310}
