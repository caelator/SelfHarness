# P23 Container Image Policy Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p23_container_image_policy_plan.md` and
`docs/architecture/glm_p23_container_image_policy_convergence.md`.

## Purpose

P23 adds an operator-owned image allowlist to the trusted container verifier
boundary. The policy gate applies to `container-demo` in both dry-run and live
modes before Docker preflight or engine rounds can run.

## Implemented

- `ImagePolicy`, `ImagePolicyEntry`, `ImagePolicyDecision`, and
  `ImagePolicyError`.
- Pure stdlib JSON policy loading with `policy_version="1"`.
- Status values `active`, `retired`, and `revoked`; only active entries allow
  execution.
- Optional digest pinning with strict `sha256:<64 lowercase hex>` validation.
- `container-demo --image-policy PATH` and `--require-image-digest`.
- Fail-closed behavior for missing policy entries, digest mismatch, retired or
  revoked entries, invalid policy JSON, and missing/invalid required digests.
- Tests proving policy-allowed dry-run execution, live rejection before Docker
  invocation, and required digest rejection before audit rounds.

## Trust Boundary

Only operator-supplied CLI arguments can select the policy file, image, digest,
container command, Docker executable, environment files, or Docker config.
Corpus JSON remains unable to select image policy, images, digests, commands,
entrypoints, Docker args, registry auth, secrets, TLS material, or headers.

## Deferred

- Pre-run Harbor image discovery when Harbor exposes stable image names or
  image references.
- Vulnerability scanning, SBOM validation, and cosign or registry attestation
  checks.
- Provider-specific registry/OAuth/secret-manager helpers.

## Schema

No audit schema change. Image policy files are operator-held runtime material;
they are not copied into corpus JSON, manifests, or audit JSONL.
