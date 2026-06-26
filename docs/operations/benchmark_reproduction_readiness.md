# Benchmark Reproduction Readiness

`docs/operations/benchmark_reproduction_requirements.json` maps the
Self-Harness paper's live Terminal-Bench reproduction requirements to concrete
readiness-matrix dependencies and required artifact classes.

Generate the current report with:

```bash
make reproduction-readiness-check
```

This writes `dist/self-harness-reproduction-readiness.json`. The underlying
script exits `0` only when the paper reproduction contract is satisfied, exits
`2` when the report is valid but reproduction is not ready, and exits `3` for
corrupt inputs. The Make target preserves the not-ready report for inspection.

## Contract

The standalone report is fail-closed for benchmark reproduction. A requirement
passes only when:

- every bound readiness-matrix dependency is `provisioned`;
- at least one non-empty artifact exists for the required artifact class;
- no input artifact claims benchmark reproduction;
- every supplied artifact for the required artifact class matches the
  class-specific live evidence shape below.

The current local project is expected to report `reproduction_ready: false`
until live Harbor, Docker, paper model backends, network-control,
artifact-ingest, PyPI, and Sigstore material exists.

The model-backend portion is split into the three backends evaluated by the
paper: MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5.2. Anthropic remains a package
adapter seam, but it cannot satisfy these paper-backend rows.

The required model artifact class is `model_backend_preflight_report`. Operators
produce it with `make model-backend-preflight` after setting the provider
environment described in `docs/operations/model_backend_preflight.md`. The
default dry-run report is useful for inspection but cannot satisfy reproduction
readiness because the model rows remain blocked and the report is not a live
provider reachability proof. Replay reports are also rejected for this artifact
class unless the report is a live preflight with `mode: live` and `ok: true`.

## Editable Surface Hash Convention

For paper-faithful proposer-context logs, each
`proposer_context_manifest.editable_surfaces.surfaces[]` row should keep
`name` and `sha256` coherent with the capture-extract convention:
`sha256(stable_json({"changed_surfaces":[name]}) + "\n")`.
Bundle verification still treats changed-surface name grounding as the
authoritative invariant: candidates with non-empty `changed_surfaces` must name
surfaces from the same-round proposer context, and the `edited_surface_sha256`
binding remains an independent compact evidence check.

## Required Artifact Shapes

The reproduction-readiness evaluator is fail-closed for every artifact class in
`docs/operations/benchmark_reproduction_requirements.json`. These validators
inspect supplied JSON artifacts only. They do not contact Harbor, Docker,
registries, scanners, PyPI, Sigstore, scanner databases, model providers, or
cloud services.

| Artifact class | Required live evidence shape |
| --- | --- |
| `live_terminal_bench_split_manifest` | `reproduction_claimed:false`, `mode:"live"`, `source:"harbor"`, non-empty `capture_run_id` and `harbor_version`, `total_cases:64`, non-empty disjoint `held_in_task_ids` and `held_out_task_ids`, matching split counts, and `fixed_across_variants:true`. Bundle verification also applies `cross_artifact_capture_run_id_binding` and `cross_artifact_harbor_version_binding` so split evidence must match the bundled live capture run and Harbor preflight environment. This validates the paper's fixed 64-case Terminal-Bench-2.0 split without hard-coding private task IDs. |
| `live_two_repeat_evaluation_report` | `reproduction_claimed:false`, `mode:"live"`, non-empty `capture_run_id`, `attempts_per_task:2`, non-empty `per_task_attempts` rows where every task records exactly two boolean pass attempts, aggregate counts that reconcile, and `fixed_protocol_sha256` binding the evidence to the bundled `fixed_protocol_config`. Bundle verification also applies `cross_artifact_capture_run_id_binding` and `cross_artifact_evaluation_audit_outcomes` so per-attempt pass values must match the bundled `live_harbor_audit`. |
| `fixed_protocol_config` | `reproduction_claimed:false`, `mode:"live"`, non-empty `capture_run_id`, `benchmark_protocol:"terminal-bench@2.0"`, the three paper backends (`minimax`, `qwen`, `glm` or equivalent paper labels), non-empty `evaluator` and `tool_set`, a `decoding_budget` object, positive `self_harness_rounds`, positive `proposal_width`, and `fixed_across_variants:true`. Bundle verification also applies `cross_artifact_capture_run_id_binding`, `cross_artifact_model_protocol_binding`, and `cross_artifact_proposer_round_count` so the protocol model set and Self-Harness `T`/`K` values must match bundled model preflight and proposer request-log evidence. |
| `live_harbor_preflight_report` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, `harbor_reachable:true`, and a non-empty `harbor_version`. Bundle verification also applies `cross_artifact_capture_run_id_binding` and `cross_artifact_harbor_version_binding` so preflight evidence must match the bundled live split environment. |
| `container_image_trust_report` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, `policy:"digest-bound"`, `all_digest_bound:true`, and non-empty `images` entries with `name`, exact `sha256:<64 lowercase hex>` digest, and optional non-empty `child_digests` lists for multi-arch manifests. Bundle verification also applies `cross_artifact_capture_run_id_binding`. When `live_harbor_audit` rows record `image_digest`, bundle verification also applies `cross_artifact_audit_image_binding`: single-arch reports bind audit digests to image manifest digests, while trust reports with `child_digests` bind audit digests to the declared child-digest union and fail closed if only some images declare children. |
| `model_backend_preflight_report` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, all three paper backend ids, and no failed required checks. Bundle verification also applies `cross_artifact_capture_run_id_binding` and `cross_artifact_model_protocol_binding` so preflight reachability must cover the same normalized paper backend set as the fixed protocol config. Dry-run and replay model preflights remain inspection material only. |
| `proposer_llm_request_log` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, `round_count` matching a non-empty `rounds` list, contiguous `round_index` values from zero, paper backend ids (`minimax`, `qwen`, `glm`), matching paper model names, 64-lowercase-hex `request_sha256` and `response_sha256`, non-negative token/proposal counts, and `committed_proposals <= attempted_proposals`. Paper reproduction bundles require this class because the paper's harness proposals are generated by the evaluated LLM backends. Reduced non-paper bundles may omit it; when present, bundle verification applies `cross_artifact_proposer_model_binding` and `cross_artifact_proposer_round_count` so proposer-observed backends, `round_count`, and per-round `attempted_proposals` match `model_backend_preflight_report` and `fixed_protocol_config`. |
| `proposer_context_manifest` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, positive `round_count`, contiguous `round_index` values from zero, and compact per-round blocks for editable surfaces, held-in failure patterns, passing behavior summaries, and previous attempted edits. Editable surfaces must carry pairwise-distinct 64-lowercase-hex `sha256` values within each round. Held-in failure patterns and passing behavior summaries carry explicit `task_ids` plus stable 64-lowercase-hex hashes of the underlying context ingredients rather than raw prompts or traces. Held-in failure patterns may also disclose a closed terminal `failure_category` for the cluster, an opaque `causal_status_sha256` for the paper failure-signature `q` component, optional `shared_symptoms_sha256` / `verifier_evidence_sha256` values for the paper Section 3.2 cluster evidence, and optional `presentation_order` / `actionability_hint_sha256` values for the paper cluster-ordering contract. Within each round, held-in failure patterns must have distinct `(failure_category, causal_status_sha256, mechanism_sha256)` signatures and pairwise-disjoint `task_ids` so exact-match paper clusters do not duplicate the same failure signature or assign one failing task to multiple clusters. When any pattern in a block declares `presentation_order`, every pattern in that block must declare it, the values must form a contiguous permutation from zero, and larger `size` values must precede smaller `size` values; equal-size ties may be ordered by actionability. `support_rank` is not stored. Previous attempted edits carry prior proposer-round indexes, targeted mechanism hashes, optional causal-status hashes, edited surface hashes, closed audit decisions, rejection/invalid reasons when applicable, and pairwise-distinct `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)` signatures within each context round. Paper reproduction bundles require this class because Section 3.3 defines those ingredients as the bounded Harness Proposal context. Reduced non-paper bundles may omit it only when `proposer_llm_request_log` is also omitted; when present, bundle verification applies `cross_artifact_proposer_context_binding` so context rounds align with proposer LLM rounds and fixed protocol Self-Harness rounds while editable surfaces remain distinct, `cross_artifact_proposer_previous_edits_binding` so previous edits bind to prior proposer context mechanisms, causal-status hashes, editable surfaces, and same-round distinctness, and `cross_artifact_proposer_context_evidence_binding` so failure-pattern task ids cover same-round proposal-validation baseline held-in failures without overlap, failure-pattern categories match disclosed baseline terminal categories, optional shared-symptom, verifier-evidence, presentation-order, and actionability hashes are validated and recorded, passing-summary task ids cover same-round baseline held-in passes, and `task_id_set_sha256` is recomputed from the summary task ids. Capture-manifest diffing emits `proposer-context-evidence-derivation` so realized proposer context coverage, editable-surface duplicate counts, previous-attempted-edit duplicate counts, failure-pattern task-overlap counts, failure categories, causal-status hashes, shared-symptom hashes, verifier-evidence hashes, presentation order, and actionability-hint hashes are compared against the planned held-in split. |
| `proposal_validation_manifest` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, positive `round_count`, contiguous validation rounds, `fixed_protocol_sha256`, baseline split outcomes, candidate split outcomes, changed surfaces, edited-surface hashes, targeted-mechanism hashes, summary hashes, committed proposal ids, merge decision metadata, and closed audit decisions (`accepted`, `rejected`, `superseded`, `merged`, or `invalid`). Rejected, superseded, and invalid candidates must carry non-empty rejection reasons. Invalid candidates also carry `validation_failure_category`, closed to `no_editable_surface` or `execution_failure`; `no_editable_surface` is the only candidate category allowed to have empty `changed_surfaces`. Every non-`no_editable_surface` candidate must declare exactly one changed surface. Optional `task_outcomes` on split outcomes disclose task-level split/pass evidence and must reconcile with aggregate split counts; failing task rows may disclose a closed terminal `failure_category`, while passing rows must omit it or keep it null. When `proposer_context_manifest` is bundled, same-round baseline `task_outcomes` are required so weakness-mining evidence is bound to the current harness `h_t`. Optional paired round-level `harness_before_sha256` and `harness_after_sha256` fields bind validation evidence to audit lineage harness states when extraction can derive them; legacy manifests may omit both. Multi-commit rounds that declare harness hashes also declare `harness_after_merged_sha256`, derived from the same lineage `harness_after_hash`, so the next round can bind to the merged harness state. They also declare `merged_split_outcomes`, derived from `proposal_id:"__merge__"` / `arm:"candidate"` audit evaluation rows, so the next baseline can bind to an independent merged-harness split observation. Optional round-level `proposer_round_request_sha256` and `proposer_round_response_sha256` fields bind validation evidence to the shaped proposer LLM request log without storing raw prompts or responses. Bundle verification also applies `cross_artifact_capture_run_id_binding` and `cross_artifact_proposal_validation_binding` so the manifest binds to the fixed protocol hash, fixed `self_harness_rounds` and `proposal_width`, proposer attempted/committed counts, proposer request/response hashes when declared, two-repeat evaluation metadata, proposer-context current candidate grounding, proposer-context previous attempted edits, canonical live split totals, proposal-validation failure categories, baseline task outcomes when present, Algorithm 1 split-outcome lineage continuity including declared multi-commit transitions with merged split-outcome evidence, and optional harness-state hash continuity for no-op, single-commit, and declared multi-commit transitions. Candidate `split_outcomes.evaluation_repeats` must match same-round `baseline_split_outcomes.evaluation_repeats` before aggregate pass-count validation is trusted. When proposer context is bundled, current candidates must target same-round held-in failure mechanism hashes, candidates with changed surfaces must bind to same-round editable-surface hashes and names, and each validation round must use distinct `(targeted_mechanism_sha256, edited_surface_sha256)` signatures. Accepted and merged candidates must improve at least one split, degrade neither split versus their round's baseline split outcomes, and target pairwise-distinct editable-surface hashes within the round before the paper Algorithm 1 `MERGEACCEPTED` compatibility step is trusted. Validation pass counts are deliberately not compared with the final post-commit two-repeat evaluation because baseline and per-candidate rows describe different harness states. Capture-manifest diffing emits `proposal-validation-derivation` to compare realized validation structure, validation-failure-category counts, empty changed-surface counts, single-surface violation counts, harness-hash presence counts, multi-commit merged-hash values, merged split-outcome presence and digest, candidate changed-surface names, accepted/merged surface hashes, task-outcome presence counts, deterministic baseline/candidate task-outcome content digests, and `task_outcomes_digest_version:2` against the planned validation shape. |
| `network_resource_controls_attestation` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, positive `outbound_bandwidth_cap_bps`, and a `mirrored_resources` string list. Bundle verification also applies `cross_artifact_capture_run_id_binding`. |
| `live_harbor_audit` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, non-empty `capture_run_id`, `fixed_protocol_sha256`, and non-empty captured `trial_artifacts` with unique `task_id`, optional exact `image_digest`, `captured:true`, `verifier_outcome`, and exactly two attempt records using distinct `attempt_index` values `0` and `1`. Bundle verification also checks that live audit task ids match the fixed split, two-repeat evaluation artifacts, fixed protocol config hash, shared capture run, per-attempt evaluation outcomes, and, when audit rows carry `image_digest`, the bundled `container_image_trust_report`. |
| `audit_verify_report` | `reproduction_claimed:false`, `ok:true`, `mode:"live"`, `held_out_leakage:false`, and true auditability flags for proposer evidence inspection, changed surfaces, evaluation repeats, and rejected reasons. This derived post-capture report is exempt from `capture_run_id` binding. The default offline audit verifier emits `mode:"replay"` and is not live reproduction evidence; use `self-harness audit-verify-live` with signed live Harbor provenance for this artifact class. |
| `release_candidate_evidence` | `reproduction_claimed:false`, `schema_version:"1.0"`, `ok:true`, `decision:"ready"`, a valid `evidence_sha256`, and passing `audit_integrity`, `provenance_manifest`, `attestation`, and `reproduction_readiness` gates. This derived release report is exempt from `capture_run_id` binding. The reproduction-readiness gate must record `reproduction_ready:true` and a valid report hash. |

Run fast shape validation over operator-supplied artifacts with:

```bash
make reproduction-readiness-artifact-shape-lint ARTIFACT_DIR=dist/reproduction-artifacts
```

The lint exits `0` when every required class has valid supplied evidence, `2`
when the input is well formed but missing or invalid, and `3` for corrupt
inputs. P52 intentionally rotates the committed reproduction-readiness fixture
hash because class-specific invalid-artifact details are now part of the
deterministic report.

## Evidence Bundle Manifest

For production handoff, operators can bind the complete live-evidence set in a
single reproduction bundle manifest:

```json
{
  "schema_version": "1.0",
  "bundle_id": "terminal-bench-2.0-operator-run-001",
  "created_at": "2026-06-24T00:00:00Z",
  "operator_label": "operator-team",
  "entries": [
    {
      "required_artifact_class": "live_terminal_bench_split_manifest",
      "path": "artifacts/live_terminal_bench_split_manifest.json",
      "sha256": "...",
      "byte_size": 1234,
      "source": {
        "provider": "harbor",
        "captured_at": "2026-06-24T00:00:00Z",
        "operator_label": "operator-team"
      }
    }
  ],
  "reproduction_claimed": false
}
```

The bundle verifier rejects unknown fields, absolute paths, paths that escape
the bundle directory, duplicate artifact classes, missing classes, unknown
classes, empty files, byte-size mismatches, SHA-256 mismatches, invalid
class-specific shapes, `cross_artifact_model_protocol_binding` disagreement
between fixed protocol and model-backend preflight artifacts,
`cross_artifact_capture_run_id_binding` disagreement between primary captured
live evidence artifacts,
`cross_artifact_harbor_version_binding` disagreement between fixed split and
live Harbor preflight artifacts,
`cross_artifact_evaluation_audit_outcomes` disagreement between repeated
evaluation results and live Harbor audit outcomes,
`cross_artifact_proposer_model_binding` disagreement between proposer LLM
traffic, model backend preflight evidence, and the fixed protocol declaration,
`cross_artifact_proposer_round_count` disagreement between proposer LLM
round/count evidence and the fixed protocol's Self-Harness rounds and proposal
width,
`cross_artifact_proposer_context_binding` disagreement between proposer
context ingredient evidence, proposer LLM rounds, and the fixed protocol's
Self-Harness rounds,
`cross_artifact_proposer_context_evidence_binding` disagreement between
proposer context task ids or failure categories and same-round
proposal-validation baseline task outcomes,
`cross_artifact_proposer_previous_edits_binding` disagreement between previous
attempted edits and prior-round mechanism, causal-status, or editable-surface
evidence,
`cross_artifact_proposal_validation_binding` disagreement between current
proposal-validation candidates and same-round proposer-context failure
mechanisms, editable surface hashes or names, or candidate signatures,
`cross_artifact_audit_image_binding` disagreement between live Harbor audit
`image_digest` rows and the container image trust report manifest or child
digests, and any reproduction claim.
It reads files only; it does not contact live infrastructure.

Build a bundle from operator-supplied live artifacts with:

```bash
python scripts/reproduction_bundle_build.py \
  --artifact-dir dist/reproduction-artifacts \
  --bundle-id terminal-bench-2.0-operator-run-001 \
  --operator-label operator-team \
  --created-at 2026-06-24T00:00:00Z \
  --source-provider harbor \
  --source-captured-at 2026-06-24T00:00:00Z \
  --out dist/reproduction-artifacts/bundle.json
```

The builder is deterministic: it never injects the current clock, a random id,
or a reproduction claim. It requires explicit operator metadata, records
relative paths rooted at the bundle directory, computes byte sizes and SHA-256
digests, and validates class-specific artifact shapes by default. It does not
capture live artifacts; it only packages files the operator already supplied.

Sign the exact bundle bytes with:

```bash
python scripts/sign_reproduction_bundle.py \
  --bundle dist/reproduction-artifacts/bundle.json \
  --private-key /path/to/operator.ed25519 \
  --public-key /path/to/operator.ed25519.pub \
  --provider operator-kms \
  --key-id reproduction-bundle-2026-06-24 \
  --out dist/reproduction-artifacts/bundle.sig
```

`scripts/sign_reproduction_bundle.py` also accepts `--external-signer` plus the
same passphrase, provider, key id, public-key, and fingerprint controls used by
release provenance signing.

Verify a bundle directly with:

```bash
python scripts/reproduction_bundle_verify.py \
  --bundle dist/reproduction-artifacts/bundle.json \
  --signature dist/reproduction-artifacts/bundle.sig \
  --require-signature \
  --out dist/self-harness-reproduction-bundle.json
```

`--signature` uses the same detached Ed25519 sidecar convention as other
Self-Harness operator material and signs the exact bundle manifest bytes.
Signature verification is optional for advisory checks and required by the hard
`make release-candidate-evidence-reproduction` path.

When `--reproduction-bundle` is supplied to the readiness or shape-lint scripts,
it becomes the sole artifact source. Combining it with `--artifact-dir` or
`--artifact` fails closed so an operator cannot accidentally mix two different
evidence sets.

The Make workflow is:

```bash
make reproduction-bundle-check \
  REPRODUCTION_BUNDLE_ID=terminal-bench-2.0-operator-run-001 \
  REPRODUCTION_BUNDLE_OPERATOR_LABEL=operator-team \
  REPRODUCTION_BUNDLE_CREATED_AT=2026-06-24T00:00:00Z \
  REPRODUCTION_BUNDLE_SOURCE_PROVIDER=harbor \
  REPRODUCTION_BUNDLE_SOURCE_CAPTURED_AT=2026-06-24T00:00:00Z \
  REPRODUCTION_BUNDLE_KEY=/path/to/operator.ed25519 \
  REPRODUCTION_BUNDLE_PUBLIC_KEY=/path/to/operator.ed25519.pub
```

The target builds the manifest, signs it, and runs the signed verifier. It is
standalone by design and is not part of the default package `check` target.

## Release Boundary

This report is optional advisory input to release-candidate evidence. Supplying
`--reproduction-readiness-result` records `reproduction_ready` and `report_hash`
metadata, but a well-formed `reproduction_ready: false` report does not block
the default non-reproduction package release path.

Operators can opt into the hard reproduction gate with:

```bash
make release-candidate-evidence-reproduction
```

That target writes
`dist/self-harness-release-candidate-evidence-reproduction.json` and blocks
while `reproduction_ready` is false. The hard path also requires a signed
bundle report at `dist/self-harness-reproduction-bundle.json`, produced from
`REPRODUCTION_BUNDLE` and `REPRODUCTION_BUNDLE_SIGNATURE`.

The report does not contact Harbor, Docker, registries, scanners, PyPI,
Sigstore, scanner databases, model providers, or cloud services. It does not
change audit schemas, corpus schemas, manifest schemas, canonical readiness
hashes, or reproduction-claim semantics.

Readiness and release evidence hashes rotate only when the declared readiness
surface set, readiness catalog contents, or fixture metadata changes. Live model
backend evidence is operator material and is not checked into fixtures, so a
successful live preflight does not rotate committed hashes by itself.
