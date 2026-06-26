# P34 Harbor Discovery Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p34_production_readiness_plan.md` and
`docs/architecture/glm_p34_production_readiness_convergence.md`.

## Purpose

P34 adds a deterministic pre-run Harbor image discovery seam. Operators can
construct the Harbor API request, replay captured artifact JSON, and bind the
discovered image digest to the existing image-policy evaluator before scanner or
Harbor execution.

## Implemented

- `HarborDiscoveryCommand`, `HarborDiscoveryRequest`,
  `DiscoveredHarborImage`, and `HarborDiscoveryResult` under
  `self_harness.harbor_discovery`.
- `scripts/harbor_discovery.py` for dry-run, replay, and live operator
  execution.
- Strict Harbor artifact JSON parsing for artifact digest, tags, media type,
  and child digests.
- `make harbor-discovery-check` for dry-run request construction and fixture
  replay.
- Tests for request construction, replay parsing, malformed response rejection,
  live auth fail-closed behavior, CLI JSON, and image-policy binding.

## Boundary

This slice does not contact live Harbor in CI, refresh OAuth/OIDC credentials,
parse Docker config, perform registry login, select multi-arch platforms, run
Harbor jobs, or claim Terminal-Bench reproduction.

No audit schema, corpus schema, release provenance schema, readiness hash, or
reproduction-claim semantics are changed.

## Deferred

- Live Harbor execution against a provisioned host.
- OAuth/OIDC refresh flows and provider-specific robot account helpers.
- Multi-architecture platform selection.
- Sigstore attestation retrieval.
