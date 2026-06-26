# Release-Candidate Evidence

`scripts/release_candidate_evidence.py` aggregates existing offline gate outputs
into one release-candidate decision document. It consumes artifacts already
written by the release process; it does not run scanners, Docker, Harbor, PyPI,
Sigstore, registries, or cloud providers.

The evidence document is release/operator material. It is not benchmark
reproduction evidence and cannot override individual gate failures.

## Inputs

The aggregator requires:

- canonical readiness hash file;
- audit verification report JSON;
- vulnerability policy report JSON;
- scanner execution result JSON;
- scanner DB update result JSON;
- Harbor discovery result JSON;
- operator preflight result JSON;
- operator promotion preflight result JSON;
- operator policy binding result JSON;
- readiness matrix report JSON;
- readiness drift report JSON;
- reproducible build result JSON;
- optional attestation verification report JSON;
- release provenance manifest JSON;
- optional provenance signature sidecar JSON.

Every required JSON gate must contain `"ok": true`. The audit verification
report, operator policy binding report, readiness matrix report, and readiness
drift report contribute deterministic `report_hash` values to gate metadata.
The readiness matrix also contributes `live_execution_blocked` and dependency
counts. A valid readiness matrix report may still declare live execution
blocked; that status is operator information, not a release-candidate failure.
The readiness drift report contributes its check count and fails only on
catalog/preflight inconsistency that is actionable for provisioned,
reproduction-relevant dependencies. When supplied, the attestation report also
contributes `cryptographic_valid` and any deterministic `report_hash` to gate
metadata. The reproducible build report proves the release wheel can be rebuilt
byte-for-byte from the release sdist without build isolation or network
contact. The provenance manifest must use schema `1.0` and contain at least one
artifact. Any artifact that sets `reproduction_claimed` to `true` blocks the
decision.

The release-smoke status JSON is consumed by readiness drift through the
`release_smoke` preflight surface; it is not a separate release-candidate gate.
It proves offline wheel installability and artifact parity only. It does not
validate PyPI trusted publishing, TestPyPI publishing, OIDC configuration, or
benchmark reproduction.

Benchmark reproduction readiness is optional advisory input. When
`--reproduction-readiness-result` is supplied, the aggregator records its
`reproduction_ready` value and `report_hash` in a `reproduction_readiness` gate.
A well-formed report with `reproduction_ready: false` does not block the
default non-reproduction release-candidate decision. Operators can add
`--require-reproduction-readiness` to make the same gate hard-fail unless the
paper reproduction contract is satisfied.

The hard reproduction path also requires `--reproduction-bundle-result`. That
report binds the operator-supplied live artifact set to a bundle manifest with
SHA-256 and byte-size checks, and applies cross-artifact invariants including
`cross_artifact_protocol_binding`, `cross_artifact_model_protocol_binding`,
`cross_artifact_harbor_version_binding`,
`cross_artifact_capture_run_id_binding`,
`cross_artifact_split_evaluation_coverage`,
`cross_artifact_audit_split_coverage`, and
`cross_artifact_evaluation_audit_outcomes`. The Make hard gate requires the
bundle manifest to be signed. The default non-reproduction release path does
not require or read this report.

The output schema remains `1.0`; the `gates` array is the extension point for
additional release/operator evidence.

## Output

The output schema is `1.0`:

```json
{
  "schema_version": "1.0",
  "ok": true,
  "decision": "ready",
  "reproduction_claimed": false,
  "gates": [],
  "evidence_sha256": "...",
  "boundary": "..."
}
```

`decision` is `blocked` when any required gate is missing, malformed, failed,
or claims reproduction.

## Local Gates

`make release-candidate-evidence` runs the normal offline release gates, writes
their JSON artifacts under `dist/`, and then writes
`dist/self-harness-release-candidate-evidence.json`.

The Makefile target supplies `dist/self-harness-readiness-matrix.json`,
produced by `make readiness-matrix`, and
`dist/self-harness-readiness-drift.json`, produced by
`make readiness-drift-check`, as required readiness evidence. It also supplies
`dist/self-harness-attestation.json`, produced by `make attestation-check`, as
optional attestation evidence. Direct script users can omit
`--attestation-result` when that material does not exist yet, but must always
supply `--readiness-matrix-result`, `--readiness-drift-result`, and
`--reproducible-build-result`.

`make reproducible-build-check` writes
`dist/self-harness-reproducible-build.json` by rebuilding the wheel from the
source distribution and comparing it with the published wheel. See
`docs/operations/reproducible_build.md`.

`make readiness-drift-check` reads the installed-wheel smoke status written by
`make smoke` at `dist/self-harness-release-smoke.json`. `make release-smoke`
includes the release-candidate evidence target and the installed-wheel smoke
test.

`make readiness-promotion-check` writes
`dist/self-harness-readiness-promotion.json` by comparing
`READINESS_BASELINE_CATALOG` and `READINESS_CANDIDATE_CATALOG`. Supplying the
result to `scripts/release_candidate_evidence.py` records advisory metadata.
The default release path now runs the baseline-equals-candidate promotion check
and includes that report as an advisory gate; rejected promotion transitions do
not block package release unless operators explicitly choose a stricter wrapper.

`make reproduction-readiness-check` writes
`dist/self-harness-reproduction-readiness.json` as a standalone paper
reproduction readiness report. The default `make release-candidate-evidence`
target does not depend on it. `make release-candidate-evidence-reproduction`
opts into the hard reproduction gate and writes
`dist/self-harness-release-candidate-evidence-reproduction.json`.
Use `make reproduction-readiness-artifact-shape-lint
ARTIFACT_DIR=dist/reproduction-artifacts` to validate supplied live artifact
shapes before running the hard gate.
Use `make reproduction-bundle-check` with explicit
`REPRODUCTION_BUNDLE_ID`, `REPRODUCTION_BUNDLE_OPERATOR_LABEL`,
`REPRODUCTION_BUNDLE_CREATED_AT`, `REPRODUCTION_BUNDLE_SOURCE_PROVIDER`,
`REPRODUCTION_BUNDLE_SOURCE_CAPTURED_AT`, and signing-key or external-signer
environment to author, sign, and verify the manifest-bound artifact set.
Use `make reproduction-readiness-bundle-verify
REPRODUCTION_BUNDLE=dist/reproduction-artifacts/bundle.json
REPRODUCTION_BUNDLE_SIGNATURE=dist/reproduction-artifacts/bundle.sig` to verify
the manifest-bound artifact set used by the hard gate.
