# Capture Admission

Capture admission is an offline post-capture orchestration step. It binds raw
operator evidence, extracted artifact-class JSON, a reproduction bundle, bundle
verification, and optional reproduction-readiness evaluation into one
hash-stable admission report.

It does not contact Harbor, Docker, registries, scanners, PyPI, Sigstore,
model providers, or cloud providers. It does not set
`reproduction_claimed:true`.

## Admit A Capture

Use the installed CLI:

```bash
self-harness capture-admit \
  --admission-id terminal-bench-2.0-admission-001 \
  --operator-label operator-team \
  --created-at 2026-06-24T00:00:00Z \
  --bundle-id terminal-bench-2.0-live-001 \
  --source-provider harbor \
  --source-captured-at 2026-06-24T00:00:00Z \
  --artifact-dir dist/reproduction-artifacts \
  --bundle-out dist/reproduction-artifacts/bundle.json \
  --readiness-matrix-result dist/self-harness-readiness-matrix.json \
  --raw-flag capture_run_id=terminal-bench-2.0-live-001 \
  --raw-flag harbor_version=2.10.0 \
  --raw-input live_terminal_bench_split_manifest:split_manifest_result=ops/split-manifest-live.json \
  --raw-input live_harbor_preflight_report:harbor_discovery_result=ops/harbor-discovery-live.json \
  --raw-input container_image_trust_report:harbor_discovery_result=ops/harbor-discovery-live.json \
  --raw-input container_image_trust_report:image_policy=ops/image-policy.json \
  --raw-input fixed_protocol_config:fixed_protocol_declaration=ops/fixed-protocol-live.json \
  --raw-input model_backend_preflight_report:model_backend_preflight_result=ops/model-backend-live.json \
  --raw-flag proposer_backend_map=primary=minimax,secondary=qwen,tertiary=glm \
  --raw-input proposer_llm_request_log:capture_envelope=ops/capture-envelope.json \
  --raw-input proposer_llm_request_log:proposer_request_log=ops/proposer-llm-request-log.jsonl \
  --raw-input proposer_context_manifest:capture_envelope=ops/capture-envelope.json \
  --raw-input proposer_context_manifest:proposer_context_log=ops/proposer-context-log.jsonl \
  --raw-input proposer_context_manifest:split_manifest_result=ops/split-manifest-live.json \
  --raw-input network_resource_controls_attestation:network_controls=ops/network-controls.json \
  --raw-input live_harbor_audit:harbor_run_dir=ops/harbor-run \
  --raw-input live_two_repeat_evaluation_report:capture_envelope=ops/capture-envelope.json \
  --raw-input live_two_repeat_evaluation_report:attempts_jsonl=ops/per-task-attempts.jsonl \
  --artifact audit_verify_report=ops/audit_verify_report.json \
  --artifact release_candidate_evidence=ops/release_candidate_evidence.json \
  --out dist/self-harness-capture-admission.json
```

The standalone script exposes the same dispatcher:

```bash
python scripts/capture_admit.py ...
```

## Inputs

`--raw-input` values use `CLASS:KEY=PATH`. The keys are the same raw input
names used by `capture-extract`, such as `harbor_discovery_result`,
`image_policy`, `split_manifest_result`, `fixed_protocol_declaration`,
`capture_envelope`, `proposer_request_log`, `proposer_context_log`, and
`attempts_jsonl`.

When `fixed_protocol_config` is extracted or supplied in the same admission
run, admission passes that materialized artifact into `live_harbor_audit` and
`live_two_repeat_evaluation_report` extraction. The resulting artifacts carry
`fixed_protocol_sha256`, and bundle verification rejects any protocol/evidence
hash drift.
When `live_terminal_bench_split_manifest` is extracted or supplied in the same
admission run, admission passes that materialized artifact into
`proposer_context_manifest` extraction when that raw input omits
`split_manifest_result`, so proposer-context task ids can be checked against
the captured split.

`capture_run_id` is a required raw flag for primary captured artifact
extraction. Admission stamps the same value into the fixed split, two-repeat
evaluation, fixed protocol, Harbor preflight, container trust, model preflight,
network controls, and live Harbor audit artifacts; bundle verification rejects
any mismatch through `cross_artifact_capture_run_id_binding`.

`--artifact` values use `CLASS=PATH` for required artifact classes that are
already produced by another post-capture command. The common examples are
`audit_verify_report` from `audit-verify-live` and
`release_candidate_evidence` from the stricter reproduction release path.

Bundle metadata is explicit and required. Admission never fills in
`bundle_id`, `operator_label`, `created_at`, `source_provider`, or
`source_captured_at` from the clock or environment.

## Readiness

By default, admission evaluates reproduction readiness against the built bundle
as the sole artifact source. Use `--skip-readiness` only when the operator wants
an extraction and bundle-verification report without a readiness verdict. The
skipped-readiness report has a different `report_hash` from a full admission.

`--require-bundle-signature` requires the supplied `--bundle-signature` to pass
bundle verification. Admission does not replace `scripts/sign_reproduction_bundle.py`;
signing custody can stay as a separate operator-controlled step.

## Check

Run the offline fixture-backed target:

```bash
make capture-admit-check
```

This target is standalone operator tooling. It is not part of the default
package release smoke path.
