# P22 Verifier Auth and mTLS Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p22_verifier_auth_plan.md`.

## Purpose

P19 and P20 added trusted HTTP and container verifier boundaries. P22 hardens
those boundaries for production deployments that need private verifier
endpoints, private registry images, or mutual TLS, while keeping the same
operator-controlled trust model.

Corpus JSON still cannot choose endpoints, images, commands, credentials,
headers, TLS files, env-files, or Docker config paths. The operator supplies
those at the CLI/API boundary.

## Implemented

- HTTP verifier support for custom CA bundles and client certificate/key pairs
  through stdlib `ssl` and `urllib`.
- HTTP TLS failures map to closed verifier environment outcomes instead of raw
  exceptions.
- Container verifier `extra_env` values are written to a per-attempt `0600`
  env-file instead of `docker run -e KEY=VALUE` argv entries.
- Container verifier `--env-file` and `--docker-config` operator controls.
- Docker registry config is passed through the parent process `DOCKER_CONFIG`
  environment, not through container argv.
- Container command traces redact env-file paths and never include env values.
- Extended corpus metadata guardrails reject TLS, registry, auth, secret, and
  header-shaped keys.
- Local tests cover mTLS handshake, TLS failure mapping, registry/env redaction,
  Docker config passthrough, metadata rejection, and dry-run determinism.

## Deferred

- OAuth/OIDC, bearer-token refresh, and secret-manager integrations.
- Provider-specific registry login helpers.
- Image vulnerability scanning and policy enforcement.
- Async or distributed verifier execution.
- Full Harbor/Docker benchmark reproduction.

## Schema

No audit schema change. TLS, env-file, and Docker config inputs remain
operator-held runtime material and are not written to signed corpora, manifests,
or audit artifacts.
