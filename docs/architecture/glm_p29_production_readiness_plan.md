CONVERGED: YES
## Verdict
The highest-value locally implementable slice is **P29: Trivy Report-to-Image Digest Binding**. P28 decoupled vulnerability evaluation from image identity, leaving a gap where a clean scanner report could be substituted for a vulnerable allowed image. P23 introduced strict image policy enforcement by digest. Binding the Trivy report's target image digest to the `ImagePolicy` before evaluating `VulnerabilityPolicy` findings closes this gap using entirely offline, fixture-driven logic.

## Critique
The proposed P29 slice aligns perfectly with the repository's trajectory of adding fail-closed provenance and integrity gates without requiring live Docker, Harbor, or network access. The existing `vuln_check.py` CLI already handles Trivy parsing but lacks the context of *what* was scanned. By adding an `--image-policy` option, operators can ensure that the provided Trivy report corresponds exactly to a trusted container image. This requires no changes to the core engine, audit schemas, or corpus formats, preserving paper fidelity and avoiding reproduction claims.

## Required Changes
- None at the architecture level. The plan is self-contained within the release/operations tooling boundaries established in P25-P28.
- Implementation must gracefully handle Trivy reports that lack `Metadata.RepoDigests` by failing closed if `require_digest` is set on the policy.

## Revised Plan
**P29: Trivy Report-to-Image Digest Binding**

1. **Extend Trivy Parsing (`src/self_harness/vulnerability_policy.py`)**:
   - Add a function to extract the target image name and digest from a Trivy JSON report. Trivy reports typically store this under `Metadata.RepoDigests` (e.g., `image-name@sha256:...`).
   - Ensure robust parsing that fails closed on malformed or missing digest metadata if a policy is applied.

2. **Update Vulnerability Check CLI (`scripts/vuln_check.py`)**:
   - Introduce an `--image-policy` argument.
   - When evaluating a `--format trivy` report with an image policy provided:
     - Extract the digest from the report.
     - Evaluate it against the `ImagePolicy` using `evaluate_image_policy` from `src/self_harness/image_policy.py`.
     - If the image is not allowed, output a JSON report indicating the image policy denial and exit with code 2.
     - If allowed, proceed to evaluate the vulnerability findings.

3. **Test Fixtures and Coverage (`tests/test_vulnerability_policy_trivy.py`)**:
   - Add a fixture `trivy_report_with_digest.json` containing a sample digest in the metadata.
   - Add tests verifying:
     - CLI fails closed (exit code 2) when the report's digest is not in the image policy.
     - CLI succeeds when the report's digest matches an active entry in the image policy.
     - CLI handles reports without digests properly when a policy is supplied.

## Remaining Open Questions
- Are there other scanner formats (e.g., Grype) that should be included in this slice? (Inference: No, keep scope tight to Trivy as established in P28).
