# Capture Extract

Capture extraction is an offline post-capture step. It transforms
operator-supplied raw live evidence into the strict artifact-class JSON shapes
used by reproduction-readiness and reproduction-bundle verification.

It does not contact Harbor, Docker, registries, scanners, PyPI, Sigstore,
model providers, or cloud providers, and it never sets
`reproduction_claimed:true`.

## Extract An Artifact

Use the installed CLI:

```bash
self-harness capture-extract \
  --class live_harbor_preflight_report \
  --capture-run-id terminal-bench-2.0-live-001 \
  --harbor-discovery-result ops/harbor-discovery-live.json \
  --harbor-version 2.10.0 \
  --out dist/reproduction-artifacts/live_harbor_preflight_report.json
```

The standalone script exposes the same dispatcher:

```bash
python scripts/capture_extract.py \
  --class container_image_trust_report \
  --capture-run-id terminal-bench-2.0-live-001 \
  --harbor-discovery-result ops/harbor-discovery-live.json \
  --image-policy ops/image_policy.json \
  --out dist/reproduction-artifacts/container_image_trust_report.json
```

When Harbor discovery includes multi-arch manifest `child_digests`, non-empty
child digest lists are copied into `container_image_trust_report.images[]`.
Empty lists are omitted from the extracted trust report. Malformed or duplicate
child digests fail extraction before a reproduction bundle can be built.

## Supported Classes

P62 intentionally covered raw-output-derived artifact classes. P63 adds the
two paper-protocol declarations that must also come from explicit
operator-owned live-run material:

- `live_terminal_bench_split_manifest`
- `live_harbor_preflight_report`
- `container_image_trust_report`
- `fixed_protocol_config`
- `model_backend_preflight_report`
- `proposer_llm_request_log`
- `proposer_context_manifest`
- `network_resource_controls_attestation`
- `live_harbor_audit`
- `live_two_repeat_evaluation_report`

The extractor fails closed on non-live input modes, unknown raw fields, missing
image digests, missing or failed required model checks, wrong attempt counts,
implicit timestamp injection, split/task count drift, protocol/model drift, and
`reproduction_claimed:true` leakage.

For `proposer_context_manifest`, raw nested `causal_status` strings inside
held-in failure patterns or previous attempted edits are converted to
`causal_status_sha256` and the raw text is not emitted. If an operator supplies
both the raw string and a hash, the hash must match the canonical stable-JSON
digest.

Held-in failure patterns may also include raw `shared_symptoms` and
`verifier_evidence` values as either strings or string lists. Extraction emits
only `shared_symptoms_sha256` and `verifier_evidence_sha256` using the same
stable-JSON hash convention, and rejects malformed values or mismatched
supplied hashes.

Raw `actionability_hint` strings are similarly converted to
`actionability_hint_sha256`. `presentation_order` is passed through as
operator-declared structure and later validated by the shaped
`proposer_context_manifest` contract when present.

Every primary captured artifact must carry one non-empty `capture_run_id`. The
extractor can read it from raw operator inputs that already include the field or
from `--capture-run-id`; when both are supplied, they must match. Derived
post-capture reports are not produced by this command.

## Examples

Extract the fixed live Terminal-Bench split manifest:

```bash
self-harness capture-extract \
  --class live_terminal_bench_split_manifest \
  --capture-run-id terminal-bench-2.0-live-001 \
  --split-manifest-result ops/split-manifest-live.json \
  --harbor-version 2.10.0 \
  --out dist/reproduction-artifacts/live_terminal_bench_split_manifest.json
```

The split input must contain exactly 64 live Harbor task ids split into
disjoint held-in and held-out sets:

```json
{
  "schema_version": "1.0",
  "mode": "live",
  "source": "harbor",
  "total_cases": 64,
  "held_in_count": 32,
  "held_out_count": 32,
  "held_in_task_ids": [
    "terminal-bench-task-00",
    "terminal-bench-task-01",
    "terminal-bench-task-02",
    "terminal-bench-task-03",
    "terminal-bench-task-04",
    "terminal-bench-task-05",
    "terminal-bench-task-06",
    "terminal-bench-task-07",
    "terminal-bench-task-08",
    "terminal-bench-task-09",
    "terminal-bench-task-10",
    "terminal-bench-task-11",
    "terminal-bench-task-12",
    "terminal-bench-task-13",
    "terminal-bench-task-14",
    "terminal-bench-task-15",
    "terminal-bench-task-16",
    "terminal-bench-task-17",
    "terminal-bench-task-18",
    "terminal-bench-task-19",
    "terminal-bench-task-20",
    "terminal-bench-task-21",
    "terminal-bench-task-22",
    "terminal-bench-task-23",
    "terminal-bench-task-24",
    "terminal-bench-task-25",
    "terminal-bench-task-26",
    "terminal-bench-task-27",
    "terminal-bench-task-28",
    "terminal-bench-task-29",
    "terminal-bench-task-30",
    "terminal-bench-task-31"
  ],
  "held_out_task_ids": [
    "terminal-bench-task-32",
    "terminal-bench-task-33",
    "terminal-bench-task-34",
    "terminal-bench-task-35",
    "terminal-bench-task-36",
    "terminal-bench-task-37",
    "terminal-bench-task-38",
    "terminal-bench-task-39",
    "terminal-bench-task-40",
    "terminal-bench-task-41",
    "terminal-bench-task-42",
    "terminal-bench-task-43",
    "terminal-bench-task-44",
    "terminal-bench-task-45",
    "terminal-bench-task-46",
    "terminal-bench-task-47",
    "terminal-bench-task-48",
    "terminal-bench-task-49",
    "terminal-bench-task-50",
    "terminal-bench-task-51",
    "terminal-bench-task-52",
    "terminal-bench-task-53",
    "terminal-bench-task-54",
    "terminal-bench-task-55",
    "terminal-bench-task-56",
    "terminal-bench-task-57",
    "terminal-bench-task-58",
    "terminal-bench-task-59",
    "terminal-bench-task-60",
    "terminal-bench-task-61",
    "terminal-bench-task-62",
    "terminal-bench-task-63"
  ],
  "fixed_across_variants": true,
  "capture_run_id": "terminal-bench-2.0-live-001",
  "operator_label": "operator-team",
  "reproduction_claimed": false
}
```

Extract the fixed paper protocol declaration:

```bash
self-harness capture-extract \
  --class fixed_protocol_config \
  --capture-run-id terminal-bench-2.0-live-001 \
  --fixed-protocol-declaration ops/fixed-protocol-live.json \
  --out dist/reproduction-artifacts/fixed_protocol_config.json
```

The protocol declaration must pin Terminal-Bench 2.0, the three paper model
backends, evaluator, tool set, and decoding/tool budget across harness
variants:

```json
{
  "schema_version": "1.0",
  "mode": "live",
  "benchmark_protocol": "terminal-bench@2.0",
  "models": ["minimax", "qwen", "glm"],
  "evaluator": "terminal-bench-verifier",
  "tool_set": "minimal-terminal-tools",
  "decoding_budget": {"max_tokens": 8192, "max_tool_calls": 100},
  "self_harness_rounds": 3,
  "proposal_width": 2,
  "fixed_across_variants": true,
  "capture_run_id": "terminal-bench-2.0-live-001",
  "operator_label": "operator-team",
  "reproduction_claimed": false
}
```

Extract a model backend preflight artifact from a live model preflight report:

```bash
self-harness capture-extract \
  --class model_backend_preflight_report \
  --capture-run-id terminal-bench-2.0-live-001 \
  --model-backend-preflight-result ops/model-backend-preflight-live.json \
  --out dist/reproduction-artifacts/model_backend_preflight_report.json
```

Extract proposer-side LLM request-log evidence from an opt-in engine request log
and a live capture envelope:

```bash
self-harness capture-extract \
  --class proposer_llm_request_log \
  --capture-run-id terminal-bench-2.0-live-001 \
  --capture-envelope ops/capture-envelope.json \
  --proposer-request-log ops/proposer-llm-request-log.jsonl \
  --proposer-backend-map primary=minimax \
  --proposer-backend-map secondary=qwen \
  --proposer-backend-map tertiary=glm \
  --out dist/reproduction-artifacts/proposer_llm_request_log.json
```

The raw JSONL rows contain hashes and counts only:

```json
{
  "round_index": 0,
  "proposer_client": "primary",
  "request_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "response_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "attempted_proposals": 2,
  "committed_proposals": 0
}
```

`request_sha256` is the SHA-256 of
`stable_json_dumps({"system_prompt": system_prompt, "user_prompt": user_prompt})`
plus a trailing newline. `response_sha256` is the SHA-256 of the raw string
returned by the LLM client. The extractor stamps the live `capture_run_id`,
maps each `proposer_client` through `--proposer-backend-map`, and writes the
paper model name. Unknown clients, unknown backends, malformed hashes, non-live
capture envelopes, index gaps, and `reproduction_claimed:true` fail extraction.
Bundle verification also binds the resulting `round_count` and per-round
`attempted_proposals` values to `fixed_protocol_config.self_harness_rounds` and
`fixed_protocol_config.proposal_width`.

Extract proposer-context ingredient evidence from a compact per-round JSONL
manifest and the same live capture envelope:

```bash
self-harness capture-extract \
  --class proposer_context_manifest \
  --capture-run-id terminal-bench-2.0-live-001 \
  --capture-envelope ops/capture-envelope.json \
  --split-manifest-result ops/split-manifest-live.json \
  --proposer-context-log ops/proposer-context-log.jsonl \
  --out dist/reproduction-artifacts/proposer_context_manifest.json
```

Each raw JSONL row summarizes the bounded Section 3.3 Harness Proposal context
for one round:

```json
{
  "round_index": 0,
  "editable_surfaces": {
    "surface_count": 1,
    "surfaces": [
      {
        "kind": "prompt",
        "name": "system_prompt",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
      }
    ]
  },
  "held_in_failure_patterns": {
    "pattern_count": 1,
    "patterns": [
      {
        "cluster_id": "held-in-cluster-0",
        "size": 2,
        "task_ids": [
          "terminal-bench-task-00",
          "terminal-bench-task-01"
        ],
        "mechanism_sha256": "1111111111111111111111111111111111111111111111111111111111111111"
      }
    ]
  },
  "passing_behavior_summaries": {
    "summary_count": 1,
    "summaries": [
      {
        "task_ids": [
          "terminal-bench-task-02",
          "terminal-bench-task-03"
        ],
        "task_id_set_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        "preserved_behavior_sha256": "3333333333333333333333333333333333333333333333333333333333333333"
      }
    ]
  },
  "previous_attempted_edits": {
    "edit_count": 0,
    "edits": []
  }
}
```

For non-initial rounds, every `previous_attempted_edits.edits[]` row must
include the prior `proposal_round_index`, the edited `surface`, the legacy
`decision` label, `targeted_mechanism_sha256`, `edited_surface_sha256`,
`audit_decision` (`accepted`, `rejected`, or `invalid`), and an
`audit_decision_reason` string. Accepted edits may use an empty reason; rejected
and invalid edits must carry a non-empty reason.

The extracted `proposer_context_manifest` stores hashes and structural counts,
not raw prompts, full traces, or LLM responses. Bundle verification binds its
round indexes and `round_count` to `proposer_llm_request_log` and
`fixed_protocol_config.self_harness_rounds`; every proposer round with attempted
proposals must carry non-empty editable-surface, held-in-failure, and
passing-behavior blocks, and non-initial rounds must carry previous attempted
edit summaries that bind to prior proposer mechanisms and editable surfaces.
When split, evaluation, and live-audit artifacts are bundled, verification also
checks that failure-pattern `task_ids` cover exactly the held-in failing tasks,
passing-summary `task_ids` cover exactly the held-in passing tasks, and each
passing summary's `task_id_set_sha256` is recomputed from its sorted task id
set.

Extract a network-resource controls attestation from an operator-owned
declarative input:

```bash
self-harness capture-extract \
  --class network_resource_controls_attestation \
  --capture-run-id terminal-bench-2.0-live-001 \
  --network-controls ops/network-controls.json \
  --out dist/reproduction-artifacts/network_resource_controls_attestation.json
```

The `ops/network-controls.json` input must use explicit operator material:

```json
{
  "schema_version": "1.0",
  "mode": "live",
  "outbound_bandwidth_cap_bps": 2000000,
  "mirrored_resources": ["https://resources.example/terminal-bench"],
  "capture_run_id": "terminal-bench-2.0-live-001",
  "reproduction_claimed": false
}
```

Extract live Harbor audit material from a preserved Harbor run directory:

```bash
self-harness capture-extract \
  --class live_harbor_audit \
  --capture-run-id terminal-bench-2.0-live-001 \
  --harbor-run-dir ops/harbor-run \
  --fixed-protocol-result dist/reproduction-artifacts/fixed_protocol_config.json \
  --out dist/reproduction-artifacts/live_harbor_audit.json
```

The Harbor run directory must contain exactly two captured trial attempts for
each task that will be admitted into a reproduction bundle. The extracted audit
artifact records those attempts, and bundle verification later checks that its
task ids match the fixed split manifest and two-repeat evaluation report. It
also records the SHA-256 of the fixed protocol artifact so bundle verification
can reject protocol/evidence drift.

Each trial attempt may also include an operator-recorded container digest in
`metadata.json` as `image_digest` using the exact `sha256:<64 lowercase hex>`
form. When present for a task, the digest must appear on both attempts and must
be identical across them. The extracted `live_harbor_audit` row carries that
digest forward so bundle verification can bind executed audit evidence to the
`container_image_trust_report`. For multi-arch Harbor images, this executed
digest may be one of the trust report's `child_digests` rather than the parent
manifest digest. Malformed, mixed, or conflicting digests fail extraction.

Extract the two-repeat evaluation report from a live capture envelope and
per-attempt JSONL:

```bash
self-harness capture-extract \
  --class live_two_repeat_evaluation_report \
  --capture-run-id terminal-bench-2.0-live-001 \
  --capture-envelope ops/capture-envelope.json \
  --attempts-jsonl ops/per-task-attempts.jsonl \
  --fixed-protocol-result dist/reproduction-artifacts/fixed_protocol_config.json \
  --out dist/reproduction-artifacts/live_two_repeat_evaluation_report.json
```

The capture envelope must be explicit:

```json
{
  "schema_version": "1.0",
  "mode": "live",
  "source": "harbor",
  "capture_run_id": "terminal-bench-2.0-live-001",
  "operator_label": "operator-team",
  "reproduction_claimed": false
}
```

Each attempts JSONL row must contain exactly `task_id`, `attempt_index`, and
`pass`; every task must have exactly two attempts. The extracted artifact also
records `task_count`, `attempt_count`, `pass_count`, and `fail_count`; shape
validation requires those aggregate counts to reconcile with the per-task
attempt rows and rejects extra summary fields such as `pass_rate`. The extractor
also stamps `fixed_protocol_sha256` from `--fixed-protocol-result` so the
reproduction bundle can bind the repeated-evaluation evidence to the declared
fixed model/evaluator/tool/budget protocol.

## Check

Run the offline extractor test path:

```bash
make capture-extract-check
```

After extraction, package the generated artifact files with
`scripts/reproduction_bundle_build.py`, sign with
`scripts/sign_reproduction_bundle.py`, and verify with
`scripts/reproduction_bundle_verify.py`.
