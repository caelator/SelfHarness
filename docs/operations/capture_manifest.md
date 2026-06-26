# Capture Manifest

Capture manifests are operator-owned pre-capture contracts for live
Terminal-Bench reproduction attempts. They describe what evidence the operator
intends to capture before Harbor, Docker, paper model backends, scanners,
Sigstore, PyPI, or cloud services are contacted.

They are not live evidence, do not satisfy reproduction requirements by
themselves, and must keep `reproduction_claimed` set to `false`.

## Author A Plan

Use the builder instead of hand-writing the manifest:

```bash
python scripts/capture_manifest_build.py \
  --manifest-id terminal-bench-2.0-capture-plan-001 \
  --bundle-id terminal-bench-2.0-operator-run-001 \
  --operator-label operator-team \
  --created-at 2026-06-24T00:00:00Z \
  --run-id terminal-bench-2.0-live-001 \
  --model-backend minimax \
  --model-backend qwen \
  --model-backend glm \
  --evaluator terminal-bench-verifier \
  --tool-set minimal-terminal-tools \
  --tool-budget-json '{"max_tokens":8192,"max_tool_calls":100}' \
  --outbound-bandwidth-cap-bps 2000000 \
  --mirrored-resource https://resources.example/terminal-bench \
  --source-provider harbor \
  --source-captured-after 2026-06-24T00:00:00Z \
  --source-captured-before 2026-06-25T00:00:00Z \
  --signing-provider operator-kms \
  --key-id capture-manifest-2026-06-24 \
  --out ops/capture-manifest.json
```

The builder derives required artifact classes from
`docs/operations/benchmark_reproduction_requirements.json`, writes one entry per
class, and validates every planned artifact shape before writing. Missing
planned-artifact templates are filled with deterministic shape stubs so
operators can sign and review the complete plan before live services are
contacted. Those stubs are planning material only; they are not accepted as live
evidence by reproduction readiness.

Operators can supply stricter class-specific templates and source overrides:

```bash
python scripts/capture_manifest_build.py \
  ... \
  --planned-artifact live_harbor_preflight_report=ops/planned-harbor-preflight.json \
  --entry-source live_harbor_preflight_report:provider=harbor-primary \
  --entry-note live_harbor_preflight_report="planned from Harbor preflight runbook"
```

The installed CLI exposes the same authoring path:

```bash
self-harness capture-manifest build \
  --manifest-id terminal-bench-2.0-capture-plan-001 \
  --bundle-id terminal-bench-2.0-operator-run-001 \
  --operator-label operator-team \
  --created-at 2026-06-24T00:00:00Z \
  --run-id terminal-bench-2.0-live-001 \
  --model-backend minimax \
  --model-backend qwen \
  --model-backend glm \
  --evaluator terminal-bench-verifier \
  --tool-set minimal-terminal-tools \
  --tool-budget-json '{"max_tokens":8192,"max_tool_calls":100}' \
  --outbound-bandwidth-cap-bps 2000000 \
  --mirrored-resource https://resources.example/terminal-bench \
  --source-provider harbor \
  --source-captured-after 2026-06-24T00:00:00Z \
  --source-captured-before 2026-06-25T00:00:00Z \
  --signing-provider operator-kms \
  --key-id capture-manifest-2026-06-24 \
  --out ops/capture-manifest.json
```

## Verify A Plan

```bash
python scripts/capture_manifest_verify.py \
  --manifest ops/capture-manifest.json \
  --signature ops/capture-manifest.json.sig \
  --require-signature \
  --out dist/self-harness-capture-manifest.json
```

The verifier derives required artifact classes from
`docs/operations/benchmark_reproduction_requirements.json`. Each manifest entry
must include one planned artifact shape for every required class. Those shapes
are checked with the same validators used by reproduction bundle verification,
so the pre-capture plan and post-capture bundle cannot drift silently.

The installed CLI exposes the same verifier:

```bash
self-harness capture-manifest verify \
  --manifest ops/capture-manifest.json \
  --signature ops/capture-manifest.json.sig \
  --require-signature \
  --json
```

## Sign A Plan

```bash
python scripts/sign_capture_manifest.py \
  --manifest ops/capture-manifest.json \
  --private-key keys/capture-manifest.ed25519 \
  --public-key keys/capture-manifest.ed25519.pub \
  --provider operator-kms \
  --key-id capture-manifest-2026-06-24 \
  --out ops/capture-manifest.json.sig
```

External signers use the same stdin/stdout protocol as corpus, provenance,
operator-promotion, and reproduction-bundle signing. Private keys, passphrases,
and signer stderr are operator material and must not be committed.

## Rehearse The Plan

Before contacting live services, rehearse the signed plan against synthetic
artifacts derived from the manifest's planned artifact shapes:

```bash
python scripts/capture_rehearsal.py \
  --manifest ops/capture-manifest.json \
  --manifest-signature ops/capture-manifest.json.sig \
  --require-manifest-signature \
  --rehearsal-id terminal-bench-2.0-rehearsal-001 \
  --operator-label operator-team \
  --out-dir dist/capture-rehearsal \
  --readiness-matrix-result dist/self-harness-readiness-matrix.json \
  --bundle-external-signer "python path/to/kms_signer_wrapper.py" \
  --bundle-signature-provider operator-kms \
  --bundle-key-id capture-manifest-2026-06-24 \
  --require-bundle-signature \
  --report-out dist/self-harness-capture-rehearsal.json
```

The rehearsal materializes one JSON file per planned artifact class, builds a
synthetic reproduction bundle with the existing bundle builder, optionally
signs that bundle, runs bundle verification, runs plan-vs-bundle diffing, and
then runs reproduction-readiness evaluation against the synthetic bundle. It
does not make live evidence, and `reproduction_ready:false` is expected while
the readiness matrix still lists Harbor, Docker, paper model backends, PyPI, or
Sigstore as blocked.

The installed CLI exposes the same rehearsal:

```bash
self-harness capture-manifest rehearse \
  --manifest ops/capture-manifest.json \
  --manifest-signature ops/capture-manifest.json.sig \
  --require-manifest-signature \
  --rehearsal-id terminal-bench-2.0-rehearsal-001 \
  --operator-label operator-team \
  --out-dir dist/capture-rehearsal \
  --readiness-matrix-result dist/self-harness-readiness-matrix.json \
  --bundle-external-signer "python path/to/kms_signer_wrapper.py" \
  --bundle-signature-provider operator-kms \
  --bundle-key-id capture-manifest-2026-06-24 \
  --require-bundle-signature \
  --json
```

## Diff Against A Bundle

After live artifacts are captured and packaged into a signed reproduction
bundle, compare the realized bundle to the original plan:

```bash
python scripts/capture_manifest_diff.py \
  --manifest ops/capture-manifest.json \
  --bundle dist/reproduction-artifacts/bundle.json \
  --manifest-signature ops/capture-manifest.json.sig \
  --bundle-signature dist/reproduction-artifacts/bundle.sig \
  --require-manifest-signature \
  --require-bundle-signature \
  --out dist/self-harness-capture-manifest-diff.json
```

The diff reports missing planned classes, unplanned bundle classes, source
provider drift, operator-label drift, signing-custody drift, bundle id drift,
capture-run-id drift, fixed-protocol drift, proposer-context evidence drift,
audit-image drift, network-control drift, and capture-window drift.
Capture-run-id drift fails when primary captured
artifacts in the realized bundle do not share the manifest's
`planned_run.run_id`. Fixed-protocol drift fails when the realized
`fixed_protocol_config` core fields differ from the manifest's planned protocol
artifact, including the paper Self-Harness round count and proposal width.
Proposer-context evidence drift fails when realized proposer-context task ids
do not cover exactly the planned held-in split for each proposer round.
Audit-image drift is skipped when neither the planned nor realized
`live_harbor_audit` carries `image_digest`; once present, the planned and
realized digest sets must match and the realized set must match the bundled
`container_image_trust_report`. If the realized trust report declares
`child_digests`, the realized audit digests are checked against the child digest
union rather than the parent manifest digests, and mixed child-digest
declarations fail closed. Network-control drift fails when the realized
`network_resource_controls_attestation` does not match the manifest's planned
outbound bandwidth cap and mirrored resource set. Capture-window drift is
advisory by default; operators can wrap the JSON report with a stricter policy
when their release process requires it.

`make capture-rehearsal`, `make capture-manifest-check`, and
`make capture-manifest-diff-check` run the offline fixture-backed test path.
They are standalone operator checks and are not prerequisites of the default
package release smoke path.

## Operator Sequence

The intended live workflow is:

1. Build the capture manifest with `scripts/capture_manifest_build.py`.
2. Sign it with `scripts/sign_capture_manifest.py`.
3. Verify the signed plan with `scripts/capture_manifest_verify.py`.
4. Rehearse the signed plan with `scripts/capture_rehearsal.py`.
5. Run the operator-owned live Harbor/Docker/model capture outside this
   repository's offline tests.
6. Extract raw live outputs into artifact-class JSON with
   `scripts/capture_extract.py` or `self-harness capture-extract`.
7. Admit the extracted and supplied post-capture artifacts with
   `scripts/capture_admit.py` or `self-harness capture-admit`.
8. Package captured artifacts with `scripts/reproduction_bundle_build.py`.
9. Sign the bundle with `scripts/sign_reproduction_bundle.py`.
10. Diff the signed manifest against the signed bundle with
   `scripts/capture_manifest_diff.py`.

The sequence deliberately separates plan material from realized evidence. A
valid signed capture manifest prepares the live run, but it does not make a
Terminal-Bench reproduction claim.
