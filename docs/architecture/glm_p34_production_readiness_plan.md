CONVERGED: YES

## Verdict
P34 (Pre-run Harbor image discovery orchestration) is execution-ready. It is the highest-value locally implementable slice, fits the existing offline-first scanner execution seam, preserves paper fidelity by keeping discovery artifacts as operator/release material, and explicitly avoids the live Harbor/OAuth/registry dependencies that cannot be unit-tested. Material risks (strict parsing, fail-closed behavior, replay/dry-run separation, digest-to-policy binding) are addressed in the plan. Remaining open questions are non-blocking with sensible default inferences.

## Critique
- Evidence: productionization_brief explicitly lists "pre-run Harbor image discovery" as remaining production work and confirms P23/P24/P28-P33 are implemented. RELEASE.md and Makefile confirm operator-tool patterns (`scripts/scanner_run.py`, `scripts/scanner_db_update.py`, `make scanner-check`) that P34 mirrors cleanly.
- Inference: the slice is locally implementable with no new external dependencies. The dry-run/replay/live split matches the existing scanner orchestrator, ensuring testability without Harbor credentials.
- Risk addressed: strict Harbor v2 JSON parsing with fail-closed semantics is specified, consistent with existing Trivy parser discipline.
- Risk addressed: live path is stdlib-only and deferred from CI; CI exercises only dry-run and replay.
- Risk addressed: no audit schema, corpus schema, manifest schema, readiness hash, or reproduction-claim impact.

## Required Changes
- None blocking. Implement as specified in the revised plan.

## Revised Plan
### Slice: P34 – Pre-run Harbor image discovery orchestration

**Files to Add:**
- `src/self_harness/harbor_discovery.py`
  - `HarborDiscoveryCommand(url, project, repository, reference, auth)`
  - `HarborDiscoveryResult(schema_version="1.0", ok, discovered_images, source, reason)`
  - `HarborDiscoveryError`
  - `build_harbor_discovery_request()` (dry-run URL/header construction)
  - `parse_harbor_artifact_response()` (strict fail-closed parser for Harbor v2 artifact JSON)
  - `run_harbor_discovery()` (dry-run/replay/live orchestrator; stdlib-only live path)
- `scripts/harbor_discovery.py` (operator CLI mirroring `scripts/scanner_run.py`)
- `tests/fixtures/harbor/` (`harbor_artifact_valid.json`, `harbor_artifact_missing_digest.json`, malformed JSON)
- `docs/operations/harbor_discovery.md`

**Files to Modify:**
- `Makefile`: add `harbor-discovery-check` running dry-run + replay.
- `.github/workflows/ci.yml`: add fixture replay invocation under `vuln-policy` job.

**Acceptance Tests:**
1. Dry-run URL/header construction, no network.
2. Valid fixture parse returns correct image/digest pairs.
3. Malformed/missing-digest/invalid-JSON fail closed.
4. Discovered digests accepted by P23/P24 image-policy evaluation path.

**Deferrals:**
- Live Harbor HTTP execution.
- OAuth2/OIDC refresh flows.
- Multi-arch platform selection (return top-level + child digests; caller selects).
- Sigstore attestation retrieval.

## Remaining Open Questions
- Multi-arch manifest handling: default to returning top-level digest plus child digests, defer platform selection to caller. Non-blocking.
- CLI location (`scripts/` vs `self-harness` group): default to `scripts/harbor_discovery.py` for consistency with scanner tooling. Non-blocking.
