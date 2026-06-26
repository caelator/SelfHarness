# P24 Harbor Image Policy Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p24_harbor_image_policy_plan.md` and
`docs/architecture/glm_p24_harbor_image_policy_convergence.md`.

## Purpose

P24 extends the P23 image policy boundary to the experimental
Terminal-Bench/Harbor path without claiming benchmark reproduction. Harbor live
output currently exposes a container digest but not a stable image name, so
image-name policy binding remains operator supplied.

## Implemented

- `terminal-bench --image-policy PATH`.
- `terminal-bench --trust-container-image NAME`.
- `terminal-bench --trust-container-image-digest DIGEST`.
- `terminal-bench --require-image-digest`.
- Pre-engine policy validation for policy schema, image binding, digest format,
  retired or revoked entries, and missing required digests.
- Live Harbor parsed-digest verification against the trusted digest and/or
  policy after each Harbor task invocation.
- Structured exit code 2 failures for pre-engine and live parsed-digest policy
  violations.
- Cleanup of partial round directories after live policy rejection.

## Trust Boundary

Only operator CLI flags choose the trusted image identity, digest, policy file,
Harbor executable, Docker preflight behavior, agent, and model. Terminal-Bench
manifest JSON and Harbor output cannot select policy material. Harbor output can
only provide the observed digest to verify against the operator-pinned trust
inputs.

## Deferred

- Pre-run Harbor image discovery when Harbor exposes stable image names or
  image references.
- Provider-specific registry attestation, SBOM, cosign, and vulnerability
  scanning gates.
- Real live Terminal-Bench reproduction on a provisioned Harbor/Docker host.

## Schema

No audit schema change. Image policy files remain operator-held runtime
material and are not copied into corpus JSON, manifests, or audit JSONL.
