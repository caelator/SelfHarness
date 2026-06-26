# P20 Trusted Container Verifier Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p20_container_verifier_plan.md`.

## Purpose

P19 added a trusted HTTP verifier boundary. P20 adds the container verifier
execution seam without requiring Docker in local readiness gates. The default
mode is deterministic dry-run; live execution is guarded by a machine-readable
Docker preflight.

## Implemented

- `ContainerVerifierTaskAdapter` and `ContainerVerifierRunner`.
- `ContainerCommandSpec` and deterministic `docker run` command construction.
- `container-demo CORPUS --trust-container-image IMAGE` CLI with existing corpus
  signature/keyring gates.
- Dry-run fixture replay through `--fixture-dir`.
- Live-mode preflight that writes `preflight.json` and exits before engine
  rounds when Docker is unavailable.
- Fake-docker tests for live structured JSON parsing and digest-pinned command
  construction.
- Canonical container dry-run audit hash fixture in
  `tests/fixtures/canonical_container_audit_hash.txt`.

## Trust Boundary

Only operator flags can choose the container image, digest, command, Docker
executable, environment, and mode. Corpus metadata may include
`verifier_selector` and `workspace_template`, but it may not include image,
digest, command, entrypoint, or Docker argument fields.

## Deferred

- OAuth, secret-manager integrations, and provider-specific registry helpers.
- Image vulnerability scanning, SBOM validation, and attestation enforcement.
- Async or distributed container execution.
- Full Harbor/Docker benchmark reproduction.

## Schema

No audit schema change. Container command details remain in memory on
`RunRecord.trace` and are not written to audit JSONL.
