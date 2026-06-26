# Round 1

CONVERGED: NO

## Verdict
The repository already has robust seams for scanner execution, DB freshness, and image policy evaluation offline. The highest-value, locally testable next slice is **P34: Pre-run Harbor image discovery orchestration**. This completes the loop from "which image will Harbor run?" to "what is its digest, and does it satisfy the image policy?" without requiring a live Harbor deployment, Docker daemon, or registry credentials. It preserves paper fidelity by keeping all discovery artifacts as release/operator material outside the audit schemas.

## Critique
- **Current Gap:** P23/P24 enforce image policies, and P28/P29 bind Trivy reports to policies via digests. However, obtaining the digests for a Harbor run currently requires an operator to manually inspect Harbor or run external scripts. A deterministic discovery seam makes the policy end-to-end testable offline.
- **Risk:** Harbor REST API responses vary slightly across Harbor versions. The parser must be strict about the fields it requires (`tags`, `digest`, `manifest.mediaType`, `extra_attrs`) and fail closed on malformed data, similar to the existing Trivy parser.
- **Risk:** Authentication for live discovery is complex (Basic, Bearer, Docker config). Live execution cannot be unit-tested in CI. Therefore, the implementation must explicitly separate dry-run (command construction), replay (fixture parsing), and live (stdlib HTTP) paths, and only unit-test the first two.

## Required Changes
- Introduce a `HarborDiscoveryCommand` dataclass and `HarborDiscoveryResult` schema `1.0` in a new `src/self_harness/harbor_discovery.py` module.
- Implement `run_harbor_discovery` with `dry-run`, `replay`, and `live` modes, mirroring the `scanner_run.py` architecture.
- Live mode must use only stdlib (`urllib`, `http.client`) and fail closed if credentials are missing.
- Add `scripts/harbor_discovery.py` as the operator CLI.
- Ensure discovered digests can be consumed by the existing image-policy evaluation logic (e.g., writing an image-policy file or returning a digest object that `scripts/vuln_check.py` can use).
- Explicitly document that discovery artifacts are release/operator material and do not affect readiness hashes or reproduction claims.

## Revised Plan
### Slice: P34 – Pre-run Harbor image discovery orchestration

**Files to Add:**
- `src/self_harness/harbor_discovery.py`: Core domain logic.
  - `HarborDiscoveryCommand(url, project, repository, reference, auth)`
  - `HarborDiscoveryResult(schema_version="1.0", ok, discovered_images, source, reason)`
  - `HarborDiscoveryError`
  - `build_harbor_discovery_request()`: Constructs URL and headers (dry-run).
  - `parse_harbor_artifact_response()`: Strict parser for Harbor v2 artifact JSON.
  - `run_harbor_discovery()`: Orchestrator for dry-run, replay, live modes.
- `scripts/harbor_discovery.py`: Operator CLI.
- `tests/fixtures/harbor/`: Replay fixtures (e.g., `harbor_artifact_valid.json`, `harbor_artifact_missing_digest.json`).
- `docs/operations/harbor_discovery.md`: Runbook.

**Files to Modify:**
- `Makefile`: Add `harbor-discovery-check` target running dry-run and replay.
- `.github/workflows/ci.yml`: Add fixture replay to the `vuln-policy` (or a new `operator-tools`) job.

**Acceptance Tests:**
1. `test_harbor_discovery_dry_run`: Verifies URL construction and header assembly without network calls.
2. `test_harbor_discovery_replay`: Parses a valid fixture and returns correct image/digest pairs.
3. `test_harbor_discovery_malformed`: Fails closed on missing digest, invalid JSON, or wrong schema.
4. `test_harbor_discovery_image_policy_binding`: Discovered digests are accepted by the P23/P24 image-policy evaluation logic.

**Deferrals:**
- Live Harbor HTTP execution against a real server.
- OAuth2 / OIDC token refresh flows (will require provider-specific helpers later).
- Sigstore attestation retrieval (will require network services).

## Remaining Open Questions
- Should discovery support returning multiple digests for multi-arch manifests, or just resolve to the specific platform digest required by Harbor? (Inference: defer multi-arch selection logic to the caller; return the top-level manifest digest and list of child digests).
- Should the discovery CLI be added to the main `self-harness` command group or remain in `scripts/`? (Inference: keep in `scripts/` to match scanner tooling).

[usage] {"completion_tokens": 2467, "completion_tokens_details": {"reasoning_tokens": 1462}, "prompt_tokens": 17672, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 20139}

---

# Round 2

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

[usage] {"completion_tokens": 802, "completion_tokens_details": {"reasoning_tokens": 54}, "prompt_tokens": 19707, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 20509}
