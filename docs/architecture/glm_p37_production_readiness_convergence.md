# Round 1

CONVERGED: YES

## Verdict
The repository context shows P0-P36 implemented with strong offline-testable discipline, explicit release/operator boundaries, and clear "no live infrastructure" guardrails. The remaining work falls into two buckets: (a) genuinely blocked on external infrastructure (live Harbor/Docker/registries/cloud/PyPI/Sigstore/real Trivy), and (b) local contract/evidence work that unblocks the blocked items. The highest-value locally-implementable slice is to land the **provider extension seams** (so cloud-specific wrappers have a stable contract to implement against) plus a **release-candidate evidence aggregator** (so the release decision becomes auditable offline). Both are implementable and testable with fixtures only, respect existing boundaries, and don't touch audit/corpus/readiness schemas.

## Critique
- Evidence: `operator_preflight.py`, `scanner_run.py`, `harbor_discovery.py`, `scanner_db_update.py`, and release provenance tooling already establish a clean pattern of dry-run/replay/live modes with structured JSON and explicit "not reproduction evidence" boundaries. This is the right shape to extend.
- Evidence: External signer protocol (`docs/operations/external_signer.md`, `scripts/example_external_signer.py`) already proves a provider-neutral extension seam exists for signing. The natural next step is to formalize analogous seams for secrets, OAuth, and registry credentials without implementing cloud clients.
- Inference: The brief lists "provider-specific KMS/HSM/hardware-token wrappers" and "provider-specific registry/OAuth/secret-manager helpers" as remaining. We cannot implement those locally, but we *can* define the protocols and ship deterministic in-process stubs that future provider packages implement. This is the contract-first move.
- Inference: "Release-candidate decision evidence" is listed as remaining and is fully offline. Today `make release-smoke` runs gates but produces no single decision artifact. An aggregator that reads the existing JSON outputs and emits a structured decision is high-value and low-risk.
- Risk: Adding protocol seams prematurely could constrain future implementations. Mitigation: protocols are typing-only, documented as extension points, and the in-repo stubs are explicitly non-production.
- Risk: Evidence aggregator could produce false "ready" decisions. Mitigation: aggregator is strict-by-default, fails closed on missing inputs, and never overrides `reproduction_claimed=false`.

## Required Changes
None blocking. Recommended refinements for the implementer:
1. Keep provider seams in a new `self_harness.providers` namespace module; do not extend public `EngineConfig` or audit schemas.
2. Release-candidate evidence aggregator must consume existing JSON outputs only (vuln report, scanner-check result, harbor-discovery result, operator-preflight result, provenance signature sidecar presence) and must not re-run any external tool.
3. Add explicit `boundary` field in every new structured output matching the existing convention.
4. CI: add a `release-candidate-evidence` job that runs the aggregator over checked-in fixture outputs and asserts a deterministic decision document hash.

## Revised Plan

### P37 — Provider extension seams and release-candidate evidence

**Scope (all local, all fixture-tested):**

1. **Provider protocols** under `src/self_harness/providers/`:
   - `SecretResolver` protocol: `resolve(name: str) -> str` with `SecretResolutionError`.
   - `OAuthTokenProvider` protocol: `token(scope: str, deadline: float) -> OAuthToken` with token/expiration/redacted-repr.
   - `RegistryCredentialProvider` protocol: `credentials_for(registry: str) -> RegistryCredential`.
   - `KmsSigner` protocol: aliases the existing external-signer payload contract but exposes a typed Python interface for future in-process KMS adapters.
   - Deterministic in-process stub implementations (`StaticSecretResolver`, `StaticOAuthTokenProvider`, `StaticRegistryCredentialProvider`) for tests and local demos. Documented as non-production.
   - No network, no cloud SDKs, no key material in repo.

2. **Provider registry** (`self_harness.providers.registry`):
   - Process-wide registry mapping provider names to factories.
   - `external-signer` command parsing already exists; add an analogous parser for `--secret-resolver`, `--oauth-provider`, `--registry-credential-provider` that accepts either an in-process name or a subprocess command (reusing the external-signer stdin/stdout contract shape).
   - Wire `corpus-sign`, `scanner_run.py`, `scanner_db_update.py`, and `harbor_discovery.py` to optionally accept these providers via CLI flags; default behavior unchanged.

3. **Release-candidate evidence aggregator** (`scripts/release_candidate_evidence.py`):
   - Inputs: paths to existing JSON artifacts (readiness hash file, vuln report, scanner-check result, harbor-discovery result, operator-preflight result, provenance manifest, provenance signature sidecar).
   - Outputs: structured `release-candidate-evidence.json` with schema `1.0`, per-gate status, aggregate decision (`ready`/`blocked`), explicit `reproduction_claimed=false` invariant, and `boundary` field.
   - Fails closed (exit 2) when any required gate artifact is missing, malformed, or reports failure.
   - Never claims reproduction; never overrides individual gate decisions.

4. **Makefile / CI:**
   - `make release-candidate-evidence` target invoking the aggregator over `dist/` and operator-output paths.
   - New CI job `release-candidate-evidence` running the aggregator over checked-in fixture outputs and asserting a deterministic decision document hash fixture.
   - Add to `release-smoke` dependencies.

5. **Docs:**
   - `docs/operations/provider_seams.md`: protocol contracts, in-process stub policy, and how external provider packages should register implementations.
   - `docs/operations/release_candidate_evidence.md`: aggregator inputs/outputs, boundary statement, and decision semantics.
   - Update `RELEASE.md` release gate section to mention the new aggregator step.

6. **Tests (fixture-only, no network/cloud):**
   - Protocol conformance tests for each stub.
   - Registry registration/lookup tests.
   - CLI wiring tests proving default behavior is unchanged when provider flags are absent.
   - Aggregator tests for: all-pass, missing-artifact, malformed-artifact, gate-failed, and reproduction-claim-violation cases.
   - Deterministic decision-document hash fixture.

**Explicitly out of scope:**
- Real KMS/HSM/YubiKey/cloud-secret-manager implementations.
- Real OAuth/OIDC refresh against any provider.
- Real registry credential acquisition.
- Live Harbor/Docker/Trivy/PyPI/Sigstore contact.
- Any audit schema, corpus schema, manifest schema, or readiness hash change.
- Any reproduction-claim change.

## Remaining Open Questions
1. Should provider protocols live under `self_harness.providers` or a top-level `self_harness_extensions` namespace to keep them out of the core import path? — Recommend `self_harness.providers` with lazy imports; non-blocking.
2. Should the release-candidate evidence aggregator also consume the operator-policy bundle, or only its preflight output? — Recommend preflight output only; bundle is already covered by `operator-check`. Non-blocking.
3. Should the aggregator produce a signed decision document, or rely on the existing release-provenance signature? — Recommend relying on existing release provenance; aggregator output is release/operator material. Non-blocking.

[usage] {"completion_tokens": 2512, "completion_tokens_details": {"reasoning_tokens": 945}, "prompt_tokens": 36377, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 38889}
