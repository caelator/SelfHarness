# P29 Trivy Report Image Binding Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p29_production_readiness_plan.md` and
`docs/architecture/glm_p29_production_readiness_convergence.md`.

## Purpose

P29 binds supplied Trivy scanner reports to trusted container image policy
entries before vulnerability findings are evaluated. This closes the offline
substitution gap where a clean report for one image could be provided while
promoting a different trusted verifier or Harbor image.

## Implemented

- `TrivyImageReference`, `trivy_image_references`, and
  `load_trivy_image_references` extract `Metadata.RepoDigests` entries from
  Trivy JSON reports.
- `scripts/vuln_check.py --format trivy --audit-json report.json
  --image-policy policy.json` requires at least one `image@sha256:<digest>`
  reference to satisfy the image policy.
- The JSON report includes an `image_policy` block with selected image, digest,
  allow/deny code, message, and candidate decisions.
- Tests cover matching image/digest, digest mismatch, and missing report digest.

## Boundary

This slice is still offline tooling. It does not install Trivy, run Docker,
pull images, contact registries, discover Harbor images, or prove that an
operator-generated report is fresh. It only verifies that the report declares an
image digest accepted by the operator-owned image policy.

No audit schema, corpus schema, release provenance schema, readiness hash, or
reproduction-claim semantics are changed.

## Deferred

- Automated scanner execution.
- Scanner report freshness or timestamp policy, implemented later in P30.
- Grype or other scanner formats.
- Live Harbor image discovery.
