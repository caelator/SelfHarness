# Audit Schema Changelog

## Canonical Hash Rotation Policy

The readiness gate pins a deterministic audit-tree hash in
`tests/fixtures/canonical_audit_hash.txt`. Rotate that hash only when the audit
byte layout intentionally changes. Additive layout changes require a schema
minor bump and an entry in this changelog; breaking layout changes require a
schema major bump plus a migration shim. Any maintainer may rotate the hash when
the schema entry explains why the new byte layout is intentional.

## Trajectory 1.0

- Adds derived `trajectory.jsonl` rows written by `self-harness audit-trajectory`.
- The trajectory schema is independently versioned from the primary audit
  schema because it is a derived reporting view over existing audit artifacts.
- Readiness canonical runs now include `trajectory.jsonl` in the audit tree hash
  so paper-style lineage reporting is pinned byte-for-byte.

## Benchmark Report 1.0

- Adds derived benchmark report JSON written by `self-harness benchmark-report`.
- The report compares multiple audit directories by model label and includes
  per-model provenance, split gains, and per-task committed outcomes.
- The report defaults to `reproduction_claimed=false`; complete provenance is
  required before any reproduction claim can be accepted.

## Reproducible Build Report 1.0

- Adds release/operator package reproducibility reports written by
  `scripts/verify_reproducible_build.py`.
- Reports record the source distribution digest, published wheel digest,
  rebuilt wheel digest, fixed `SOURCE_DATE_EPOCH`, deterministic
  `report_hash`, and `reproduction_claimed=false`.
- `make reproducible-build-check` runs the report after `make build` and fails
  when the wheel rebuilt from the source distribution is not byte-identical to
  the published wheel in `dist/`.
- `scripts/release_candidate_evidence.py` now consumes this report as a
  required release-candidate gate without changing the release-candidate schema
  version.

## Harness Inspection 1.0

- Adds derived retained-edit reports written by
  `self-harness inspect-harness`.
- Reports summarize per-round harness hashes, committed ops, reverse ops,
  changed surfaces, proposal statuses, and final harness surfaces.
- The inspection schema is independently versioned from the primary audit
  schema because it is a derived read-only view over existing audit artifacts.

## Audit Migration Report 1.0

- Adds release/operator migration reports written by
  `self-harness audit-migrate`.
- Reports record source and destination audit hashes, copied file count, changed
  files, and the source/destination schema versions.
- Reports now also record source hash after migration, transform ids, and
  transform classification for the breaking-schema migration framework.
- Migrated audit manifests include `migration_applied=true` and a
  `migration_provenance` block with the source audit hash, source and target
  schema versions, transform ids, classification, notes, and lossy-approval
  state.
- Migration output is a compatibility copy, not source audit evidence, canonical
  readiness input, or benchmark reproduction evidence.

## Attestation Verification Report 1.0

- Adds release/operator attestation verification reports written by
  `self-harness verify-attestation` and `scripts/verify_attestation.py`.
- Reports record structural checks, material digest binding, backend,
  `cryptographic_valid`, deterministic `report_hash`, and
  `reproduction_claimed=false`.
- The `sigstore` backend is opt-in and uses Sigstore's native offline verifier
  only when the optional extra and a full operator-owned trust configuration are
  supplied.
- This report schema is independently versioned from audit artifacts and does
  not rotate the canonical readiness hash, audit schema, task corpus schema, or
  benchmark reproduction semantics.

## Release-Candidate Evidence Inputs 1.0

- Requires the readiness matrix report as release-candidate evidence.
- Requires the readiness drift report as release-candidate evidence.
- The evidence schema remains `1.0`; the existing `gates` array carries the
  `readiness_matrix` and `readiness_drift` gates and metadata.
- The fixture evidence hash rotated because the required input set changed.
  This does not rotate `tests/fixtures/canonical_audit_hash.txt`, the audit
  schema, task corpus schema, or benchmark reproduction semantics.

## Readiness Matrix Catalog 1.1

- Adds `preflight_surface` and `operator_action` to readiness catalog entries.
- The readiness matrix report schema remains `1.0`, but report hashes rotate
  because rows carry the new entry metadata.
- Schema `1.0` catalogs still load with defaults of `preflight_surface="none"`
  and `operator_action="provision"`.
- This does not rotate the canonical readiness hash, audit schema, task corpus
  schema, or benchmark reproduction semantics.

## Readiness Drift Report 1.0

- Adds release/operator readiness drift reports written by
  `scripts/readiness_drift_report.py`.
- Reports cross-check provisioned, reproduction-relevant readiness entries
  against existing offline preflight artifacts and keep blocked or optional
  entries advisory.
- Reports include deterministic `report_hash` and `reproduction_claimed=false`.
- This report schema is independently versioned from audit artifacts and does
  not rotate the canonical readiness hash, audit schema, task corpus schema, or
  benchmark reproduction semantics.

## Release Smoke Status 1.0

- Adds deterministic installed-wheel smoke status reports written by
  `scripts/release_smoke.py` to `dist/self-harness-release-smoke.json`.
- Reports include `schema_version`, `ok`, required step-level `checks`,
  deterministic `report_hash`, `reproduction_claimed=false`, and a boundary
  string that limits the report to offline installability and artifact parity.
- The PyPI readiness entry now uses the existing `release_smoke`
  `preflight_surface` value while remaining `blocked`; the readiness catalog
  schema stays `1.1`.
- This report schema does not validate PyPI trusted publishing, contact live
  services, rotate the canonical readiness hash, change audit/corpus schemas,
  or alter benchmark reproduction semantics.

## Benchmark Reproduction Readiness 1.0

- Adds `docs/operations/benchmark_reproduction_requirements.json` as the
  paper-reproduction requirement catalog.
- Adds deterministic reproduction-readiness reports written by
  `scripts/reproduction_readiness_report.py` to
  `dist/self-harness-reproduction-readiness.json`.
- Reports include `schema_version`, `ok`, `reproduction_ready`, per-requirement
  checks, deterministic `report_hash`, `reproduction_claimed=false`, and
  boundary language limiting the artifact to offline readiness mapping.
- The standalone CLI exits `2` when the report is valid but
  `reproduction_ready=false`; release-candidate evidence treats the report as
  advisory unless `--require-reproduction-readiness` is supplied.
- This report schema does not contact live services, rotate the canonical
  readiness hash, change audit/corpus/manifest schemas, or introduce a
  benchmark reproduction claim.

## Proposal Validation Manifest 1.0

- Adds `proposal_validation_manifest` as a derived post-capture artifact class
  for Section 3.4 proposal validation evidence.
- The artifact records per-round baseline split outcomes, candidate split
  outcomes, changed surfaces, edited-surface hashes, targeted-mechanism hashes,
  summary hashes, committed candidate ids, merge decisions, closed audit
  decisions, repeat metadata, and non-empty rejection reasons for rejected,
  superseded, or invalid candidates.
- Bundle verification adds `cross_artifact_proposal_validation_binding` to bind
  the manifest to the fixed protocol hash, `self_harness_rounds`,
  `proposal_width`, proposer attempted/committed counts, two-repeat evaluation
  metadata, proposer-context previous attempted edits, and the canonical live
  split totals for every baseline and candidate split outcome.
- The verifier deliberately does not bind validation pass counts to the final
  post-commit two-repeat evaluation, because baseline and per-candidate
  validation rows describe different harness states. Raw per-candidate trace
  binding is deferred until a dedicated live artifact shape exists.
- Bundle verification also enforces the Section 3.4 acceptance rule inside
  each validation round: accepted or merged candidates must improve at least one
  split and degrade neither split versus `baseline_split_outcomes`, using the
  aggregate pass counts recorded in `split_outcomes`.
- Candidate rows now include nullable `validation_failure_category`, closed to
  `no_editable_surface` and `execution_failure` for invalid candidates and
  required to be `null` for accepted, rejected, superseded, or merged
  candidates. The no-surface invalid path is the only case where
  `changed_surfaces` may be empty.
- Split outcomes now accept optional `task_outcomes` rows. When present, these
  rows disclose task-level split/pass evidence and must reconcile with the
  aggregate held-in/held-out pass and total counts.
- Bundle verification uses baseline `task_outcomes`, when present, to ensure
  proposer held-in failure-pattern task ids are represented as baseline
  held-in failures in proposal-validation evidence.
- Capture-manifest diffing now emits `proposal-validation-derivation` and
  compares per-round validation-failure-category counts, empty changed-surface
  counts, and candidate task-outcome presence counts between planned and
  realized proposal-validation evidence.
- The manifest is derived from audit artifacts after capture; it is not authored
  by the proposer and does not introduce a benchmark reproduction claim.

## Paper Model Backend Readiness 1.0

- Splits paper model readiness into explicit MiniMax M2.5,
  Qwen3.5-35B-A3B, and GLM-5 readiness-matrix dependencies.
- Adds offline-testable paper model client contracts for the three paper
  backends without adding provider SDK dependencies or contacting live model
  services.
- Keeps Anthropic as an optional package adapter seam outside paper
  reproduction relevance.
- Rotates release/operator readiness fixtures and evidence hashes that include
  readiness metadata. The canonical audit hash is unchanged, and no benchmark
  reproduction claim is introduced.

## Breaking Migration Framework 1.0

- Adds an in-repo migration transform registry for audit schema upgrades.
- Built-in transforms cover supported audit schema versions `1.0` through `1.4`
  as lossless metadata migrations.
- Operator override transforms are supplied only through `--transforms-json`;
  there is no plugin or entry-point surface.
- Lossy transforms are drop-only, require explicit `--allow-lossy`, and are
  recorded in migrated provenance.
- Unsupported transforms fail closed and document paths that cannot safely be
  migrated.
- This framework does not bump the primary audit writer schema, rotate the
  canonical readiness hash, or affect derived trajectory, benchmark report, or
  harness inspection schemas.

## 1.4

- Adds candidate Harbor artifact ingestion rows written by
  `self-harness harbor-ingest`.
- Evaluation rows may include `harbor_artifact_provenance`, `reward_value`,
  `reward_source`, and `trajectory_event_count`.
- Manifests may include `harbor_artifact_validation_status`; reproduction
  claims require this status to be `validated`.
- The default toy/demo writer remains on schema `1.2`, so the canonical toy
  audit hash is not rotated in this slice.

## 1.3

- Adds optional benchmark provenance fields for experimental benchmark-shaped
  runs: `benchmark_protocol`, `benchmark_dataset`, `benchmark_dataset_version`,
  `harbor_version`, `container_image_digest`, and `reproduction_claimed`.
- Adds optional `task_source_hash` to evaluation rows when a runner can preserve
  per-task source provenance.
- The default toy/demo writer remains on schema `1.2`, so the canonical toy
  audit hash is not rotated in this slice.

## 1.2

- Adds `failure_category` to evaluation rows.
- Keeps `terminal_cause` for compatibility and mirrors the closed verifier
  category used by the runner.
- Introduces a documented schema migration policy.

## 1.1

- Adds schema versioning to manifest, proposal rows, evaluation rows, and
  lineage.
- Adds changed surfaces, aggregate pass counts, evaluation repeats, decision
  reasons, and invalid/rejected candidate reasons.
- Adds manifest surface and operation allowlists.

## 1.0

- Initial deterministic manifest, lineage, proposal JSONL, evaluation JSONL,
  and harness snapshot artifacts.
## P51 Paper Model Backend Preflight

- Adds the `model_backend_preflight` readiness surface for the paper's MiniMax
  M2.5, Qwen3.5-35B-A3B, and GLM-5 backend rows.
- Adds an operator-invoked dry-run/replay/live model backend preflight report
  that always keeps `reproduction_claimed=false`.
- Rotates release/operator readiness fixtures and evidence hashes that include
  readiness surface metadata. The canonical audit hash is unchanged, and no
  benchmark reproduction claim is introduced.

## P52 Reproduction Artifact Shape Validation

- Adds class-specific validators for every artifact class in
  `docs/operations/benchmark_reproduction_requirements.json`.
- Generic placeholder JSON no longer satisfies benchmark reproduction
  readiness when live dependencies are marked provisioned.
- Adds `scripts/reproduction_readiness_artifact_shape_lint.py` and
  `make reproduction-readiness-artifact-shape-lint` for fast operator feedback
  over supplied artifact directories.
- Extends audit verification reports with replay/live mode and auditability
  metadata. The default verifier remains replay-mode release evidence, not live
  benchmark reproduction evidence.
- Rotates the reproduction-readiness fixture hash because invalid artifact
  details are now deterministic report content. The canonical audit hash is
  unchanged, and no benchmark reproduction claim is introduced.

## P53 Readiness Surface Promotion Guards

- Adds `container_preflight` and `attestation_check` readiness surfaces for the
  Docker daemon and Sigstore Fulcio/Rekor rows while keeping both rows blocked
  by default.
- Adds an offline-default container preflight report written by
  `scripts/container_preflight_report.py`.
- Readiness drift now accepts Docker and attestation surface artifacts, but a
  provisioned reproduction-relevant Docker row requires live container preflight
  evidence and a provisioned Sigstore row requires `backend="sigstore"` with
  `cryptographic_valid=true`.
- Rotates release/operator readiness fixtures and evidence hashes that include
  readiness surface metadata. The canonical audit hash is unchanged, and no
  benchmark reproduction claim is introduced.

## P54 Reproduction Evidence Bundle Manifest

- Adds a production handoff manifest for operator-supplied benchmark
  reproduction evidence artifacts.
- Bundle entries bind required artifact classes to relative paths, byte sizes,
  SHA-256 digests, and constrained source metadata.
- Adds `scripts/reproduction_bundle_verify.py` and
  `make reproduction-readiness-bundle-verify`.
- Reproduction-readiness and artifact-shape lint commands can consume a bundle
  as the sole artifact source; mixing bundle and ad hoc artifact inputs fails
  closed.
- The hard reproduction release-candidate path now requires a signed bundle
  report, while the default non-reproduction release path is unchanged.
- This does not rotate the canonical audit hash, change audit/corpus schemas,
  or introduce a benchmark reproduction claim.

## P55 Reproduction Evidence Bundle Authoring

- Adds deterministic bundle authoring for operator-supplied live evidence
  artifacts through `src/self_harness/reproduction_bundle_build.py` and
  `scripts/reproduction_bundle_build.py`.
- Bundle authoring requires explicit operator metadata and never emits
  `reproduction_claimed: true`, current-clock timestamps, random ids, or live
  service probes.
- Adds `scripts/sign_reproduction_bundle.py`, reusing the release-provenance
  local-key and external-signer custody shape while emitting the P54 bundle
  signature sidecar schema.
- Adds standalone `make reproduction-bundle-build`,
  `make reproduction-bundle-sign`, and `make reproduction-bundle-check` targets.
- This does not rotate the canonical audit hash, change audit/corpus schemas,
  change the P54 verifier schema, or introduce a benchmark reproduction claim.

## P56 Readiness Promotion Admission

- Adds read-only baseline-to-candidate readiness catalog promotion admission
  through `src/self_harness/readiness_promotion.py` and
  `scripts/readiness_promotion_report.py`.
- Extracts the provisioned-surface contract used by readiness drift so promotion
  admission and promoted-state verification enforce the same Docker, Sigstore,
  model-backend, required-check, missing-surface, and reproduction-claim rules.
- Adds standalone `make readiness-promotion-check` with
  `READINESS_BASELINE_CATALOG` and `READINESS_CANDIDATE_CATALOG`.
- Adds optional readiness-promotion metadata to release-candidate evidence
  without making it a required default gate.
- This does not mutate catalogs, rotate the canonical audit hash, change
  audit/corpus schemas, or introduce a benchmark reproduction claim.

## P57 Release-Candidate Evidence Inputs 1.0

- Adds the readiness-promotion report to the default
  `make release-candidate-evidence` input set as an advisory, non-required gate.
- CI fixture release-candidate evidence now supplies
  `tests/fixtures/release_candidate/readiness_promotion_result.json` and checks
  that the `readiness_promotion` gate is present.
- Rotates only `tests/fixtures/release_candidate/expected_hash.txt`, because
  the release/operator evidence aggregate now contains one additional advisory
  gate.
- This does not change the `release_candidate_evidence/1.0` schema, mutate
  readiness catalogs, rotate the canonical audit hash, make reproduction-bundle
  evidence part of the default release path, or introduce a benchmark
  reproduction claim.

## P58 Capture Manifest 1.0

- Adds operator live-evidence capture manifest verification through
  `src/self_harness/capture_manifest.py` and
  `scripts/capture_manifest_verify.py`.
- Adds exact-byte capture manifest signing through
  `scripts/sign_capture_manifest.py`, using the same local-key and
  external-signer sidecar shape as other operator signing flows.
- Adds plan-vs-realized bundle diffing through
  `src/self_harness/capture_manifest_diff.py` and
  `scripts/capture_manifest_diff.py`.
- Adds installed CLI access via `self-harness capture-manifest verify|diff`.
- Adds standalone `make capture-manifest-check` and
  `make capture-manifest-diff-check`.
- This is additive release/operator tooling only. It does not change audit,
  corpus, readiness, release-candidate evidence, or reproduction-bundle schemas,
  does not rotate canonical hashes, and does not introduce a benchmark
  reproduction claim.

## P59 Capture Manifest Authoring

- Adds deterministic authoring for P58 capture manifests through
  `src/self_harness/capture_manifest_build.py` and
  `scripts/capture_manifest_build.py`.
- The builder derives required artifact classes from
  `docs/operations/benchmark_reproduction_requirements.json`, fills missing
  planned-artifact templates with deterministic shape stubs, validates the
  shapes before writing, and always writes `reproduction_claimed=false`.
- Adds installed CLI access via `self-harness capture-manifest build`.
- Adds standalone `make capture-manifest-build`; `make
  capture-manifest-check` now runs build, sign, signed verify, and the fixture
  test matrix.
- This reuses the P58 `capture_manifest/1.0` schema. It does not introduce a
  new evidence schema, contact live services, rotate canonical hashes, alter
  the default release path, or introduce a benchmark reproduction claim.

## P60 Capture Rehearsal 1.0

- Adds deterministic offline capture-pipeline rehearsal through
  `src/self_harness/capture_rehearsal.py` and
  `scripts/capture_rehearsal.py`.
- Rehearsal materializes planned artifact shapes from a capture manifest,
  builds a synthetic reproduction bundle, optionally signs that bundle, runs
  reproduction-bundle verification, runs capture-manifest diffing, and evaluates
  reproduction readiness against the synthetic bundle.
- Adds installed CLI access via `self-harness capture-manifest rehearse`.
- Adds standalone `make capture-rehearsal`; `make capture-manifest-check` now
  runs build, sign, signed verify, rehearsal, signed synthetic-bundle diff, and
  the fixture test matrix.
- The new `capture_rehearsal/1.0` report records stage hashes,
  `reproduction_ready`, deterministic `report_hash`, and
  `reproduction_claimed=false`. Rehearsal output is advisory operator material:
  it does not contact live services, rotate canonical hashes, alter the default
  release path, or introduce a benchmark reproduction claim.

## P61 Live Audit Verification Provenance

- Adds signed live audit verification through
  `src/self_harness/audit_verify_live.py` and
  `scripts/audit_verify_live.py`.
- Adds installed CLI access via `self-harness audit-verify-live`.
- Adds standalone `make audit-verify-live` with a deterministic offline Harbor
  fixture, provenance signing, and live-shaped report output.
- The report reuses the existing `audit_verify_report` artifact shape and
  emits `mode:"live"` only when replay verification, provenance signature,
  live Harbor artifact shape, and task binding checks pass. Failures emit
  `mode:"live_blocked"` with structured checks.
- This is additive release/operator tooling only. It does not change audit,
  corpus, readiness, release-candidate evidence, capture-manifest, or
  reproduction-bundle schemas, does not contact live services, does not rotate
  canonical hashes, and does not introduce a benchmark reproduction claim.

## P62 Capture Extract

- Adds offline post-capture live-evidence extractors through
  `src/self_harness/capture_extract.py` and `scripts/capture_extract.py`.
- Adds installed CLI access via `self-harness capture-extract`.
- Adds standalone `make capture-extract-check`.
- P62 covers `live_harbor_preflight_report`, `container_image_trust_report`,
  `model_backend_preflight_report`, `network_resource_controls_attestation`,
  `live_harbor_audit`, and `live_two_repeat_evaluation_report`.
- Extractors validate generated payloads with the existing artifact-class
  validators and fail closed on unknown raw fields, non-live modes, missing
  digests, wrong attempt counts, timestamp injection, and reproduction-claim
  leakage.
- This is additive release/operator tooling only. It does not change audit,
  corpus, readiness, release-candidate evidence, capture-manifest, or
  reproduction-bundle schemas, does not contact live services, does not rotate
  canonical hashes, and does not introduce a benchmark reproduction claim.

## P63 Capture Extract Split And Protocol Coverage

- Extends `src/self_harness/capture_extract.py`,
  `scripts/capture_extract.py`, and `self-harness capture-extract` with
  `live_terminal_bench_split_manifest` and `fixed_protocol_config` extraction.
- Adds strict raw input contracts for `--split-manifest-result` and
  `--fixed-protocol-declaration`.
- Split extraction requires live Harbor source material, exactly 64 disjoint
  task ids, matching held-in/held-out counts, fixed-across-variants semantics,
  and a non-empty Harbor version.
- Protocol extraction requires `terminal-bench@2.0`, the three paper model
  backends, non-empty evaluator/tool set, an object decoding budget, and
  fixed-across-variants semantics.
- This reuses existing artifact-class shapes and does not change audit, corpus,
  readiness, release-candidate evidence, capture-manifest, or
  reproduction-bundle schemas, does not contact live services, does not rotate
  canonical hashes, and does not introduce a benchmark reproduction claim.

## P64 Capture Admission 1.0

- Adds `capture_admission/1.0` reports through
  `src/self_harness/capture_admit.py` and `scripts/capture_admit.py`.
- Adds installed CLI access via `self-harness capture-admit`.
- Adds standalone `make capture-admit-check`.
- Admission composes existing extraction, artifact shape validation,
  reproduction-bundle build, bundle verification, and optional reproduction
  readiness evaluation into one hash-stable operator report.
- Admission requires explicit operator bundle/source metadata and does not
  derive ids, labels, or timestamps from the current process environment.
- This is additive release/operator tooling only. It does not add artifact
  classes, change artifact-class validators, change readiness semantics, contact
  live services, rotate canonical hashes, or introduce a benchmark reproduction
  claim.

## P65 Two-Repeat Aggregate Validation

- Tightens the `live_two_repeat_evaluation_report` artifact-class shape by
  requiring `task_count`, `attempt_count`, `pass_count`, and `fail_count`.
- The counts must reconcile with `per_task_attempts`; `attempt_count` must equal
  `attempts_per_task * task_count`, `pass_count` must match observed passing
  attempts, and `fail_count` must equal `attempt_count - pass_count`.
- Adds a closed top-level field set for this artifact class so derived fields
  such as `pass_rate` cannot enter reproduction evidence without an explicit
  schema decision.
- `capture-extract` computes the aggregate values from raw per-attempt JSONL
  and keeps `reproduction_claimed=false`.
- Capture-manifest planned two-repeat stubs now carry the same aggregate fields,
  rotating the deterministic capture-manifest build fixture hash.
- This is a stricter artifact-shape contract for paper Section 4.1 evidence. It
  does not change readiness dependencies, contact live services, rotate
  canonical audit hashes, or introduce a benchmark reproduction claim.

## P66 Split/Evaluation Coverage Cross-Check

- Adds the `cross_artifact_split_evaluation_coverage` check to reproduction
  bundle verification.
- The check requires the `live_two_repeat_evaluation_report` task ids to equal
  the union of `held_in_task_ids` and `held_out_task_ids` from
  `live_terminal_bench_split_manifest`.
- The check also requires `task_count=64` and `attempt_count=128`, matching the
  paper's fixed 64-case Terminal-Bench-2.0 subset and two repeated attempts per
  task.
- Capture admission inherits the check through bundle verification, so direct
  admission cannot accept a smaller or divergent repeated-evaluation report.
- This changes reproduction-bundle verification behavior but does not change
  readiness dependencies, contact live services, rotate canonical audit hashes,
  or introduce a benchmark reproduction claim.

## P67 Live Harbor Audit Coverage Cross-Check

- Tightens the `live_harbor_audit` artifact shape so every trial artifact must
  carry exactly two attempts with distinct indexes `0` and `1`.
- The audit verifier now requires `verifier_outcome` to match the two attempts:
  `pass` only when both attempts pass, otherwise `fail`.
- Adds the `cross_artifact_audit_split_coverage` check to reproduction bundle
  verification.
- The new check requires live Harbor audit task ids to equal both the fixed
  split manifest task union and the two-repeat evaluation task set.
- Capture admission inherits this through bundle verification, so admission
  cannot accept a live audit artifact that covers fewer or different tasks.
- This is offline validation only. It does not change readiness dependencies,
  contact live services, rotate canonical audit hashes, or introduce a
  benchmark reproduction claim.

## P68 Fixed Protocol Binding Cross-Check

- Adds required `fixed_protocol_sha256` fields to
  `live_two_repeat_evaluation_report` and `live_harbor_audit` evidence.
- `capture-extract` stamps the hash from `--fixed-protocol-result` or validates
  an explicit `--fixed-protocol-sha256` against that result.
- Adds the `cross_artifact_protocol_binding` check to reproduction bundle
  verification.
- The check recomputes the byte hash of the bundled `fixed_protocol_config` and
  rejects evaluation or audit evidence that points at a different protocol hash.
- Capture admission injects the already materialized fixed protocol artifact
  into downstream raw extraction, so the operator happy path does not require
  manually duplicating a hash.
- This tightens future live reproduction evidence binding. It does not change
  readiness dependencies, contact live services, rotate canonical audit hashes,
  or introduce a benchmark reproduction claim.

## P71 Harbor Version Binding Cross-Check

- Tightens the `live_terminal_bench_split_manifest` artifact shape by requiring
  a non-empty `harbor_version`.
- Adds the `cross_artifact_harbor_version_binding` check to reproduction bundle
  verification.
- The check requires the split manifest and `live_harbor_preflight_report` to
  carry the same Harbor version and fails closed when exactly one side is
  present.
- This catches Harbor execution-environment drift across future live
  Terminal-Bench-2.0 evidence bundles. It does not change readiness
  dependencies, contact live services, rotate canonical audit hashes, or
  introduce a benchmark reproduction claim.

## P72 Capture Run Identity Binding Cross-Check

- Tightens the eight primary captured artifact shapes by requiring non-empty
  `capture_run_id`: fixed split, two-repeat evaluation, fixed protocol, Harbor
  preflight, container image trust, model backend preflight, network controls,
  and live Harbor audit.
- Keeps derived post-capture artifacts exempt: `audit_verify_report` and
  `release_candidate_evidence`.
- Adds the `cross_artifact_capture_run_id_binding` check to reproduction bundle
  verification.
- The check requires every primary captured artifact in a bundle to carry the
  same capture run id and fails closed when any primary captured artifact is
  missing it.
- Live audit verification also binds signed provenance `capture_run_id` to the
  supplied live Harbor audit artifact's `capture_run_id`.
- This catches assembled evidence from multiple live runs. It does not change
  readiness dependencies, contact live services, rotate canonical audit hashes,
  or introduce a benchmark reproduction claim.

## P73 Capture Manifest Run Identity Diff

- Adds a `capture-run-id-binding` finding to
  `capture_manifest_diff/1.0`.
- The diff now reads primary captured artifact `capture_run_id` values from the
  realized bundle and requires their shared value to match
  `capture_manifest.planned_run.run_id`.
- Reuses the reproduction-bundle primary capture-run extraction helper so bundle
  self-consistency and plan-vs-realized diffing share one local read contract.
- This is additive operator diff evidence only. It does not change capture
  manifest, reproduction bundle, readiness, release-candidate, audit, or corpus
  schemas; does not contact live services; does not rotate canonical hashes; and
  does not introduce a benchmark reproduction claim.

## P74 Capture Manifest Network Control Diff

- Adds a `network-control-binding` finding to
  `capture_manifest_diff/1.0`.
- The diff now reads the realized
  `network_resource_controls_attestation` artifact from the reproduction bundle
  and requires its `outbound_bandwidth_cap_bps` and `mirrored_resources` set to
  match `capture_manifest.planned_run`.
- Missing `network_resource_controls_attestation` entries skip this finding so
  reduced requirement sets can still be diffed; present but unreadable or
  drifting artifacts fail closed.
- This is additive operator diff evidence only. It does not change capture
  manifest, reproduction bundle, readiness, release-candidate, audit, or corpus
  schemas; does not contact live services; does not rotate canonical hashes; and
  does not introduce a benchmark reproduction claim.

## P75 Capture Manifest Fixed Protocol Diff

- Adds a `fixed-protocol-binding` finding to
  `capture_manifest_diff/1.0`.
- The diff now compares the planned `fixed_protocol_config` artifact against
  the realized bundled `fixed_protocol_config` using a deterministic hash of
  the paper-relevant protocol core: benchmark protocol, normalized paper model
  backends, evaluator, tool set, decoding budget, and fixed-across-variants
  flag.
- The hash deliberately ignores non-protocol metadata such as capture run ids
  so formatting or custody details do not trigger false protocol drift.
- Missing `fixed_protocol_config` entries skip this finding because existing
  class coverage findings already report missing planned or bundled classes;
  present but unreadable or drifting artifacts fail closed.
- This is additive operator diff evidence only. It does not change capture
  manifest, reproduction bundle, readiness, release-candidate, audit, or corpus
  schemas; does not contact live services; does not rotate canonical hashes; and
  does not introduce a benchmark reproduction claim.

## P76 Live Audit Container Image Digest Binding

- Adds optional `image_digest` to `live_harbor_audit.trial_artifacts[]`, using
  exact `sha256:<64 lowercase hex>` grammar.
- Tightens `container_image_trust_report.images[].digest` to the same exact
  digest grammar.
- Capture extraction reads optional `image_digest` from Harbor trial
  `metadata.json`; when present for a task, every attempt must carry the same
  digest.
- Adds `cross_artifact_audit_image_binding` to reproduction bundle
  verification. Bundles without audit `image_digest` rows keep the existing
  behavior; once audit rows carry digests, the audit digest set must match the
  bundled `container_image_trust_report`.
- Adds an `audit-image-binding` finding to `capture_manifest_diff/1.0` so
  planned live audit image digests, realized audit image digests, and trusted
  image digests cannot drift silently.
- This is additive operator evidence binding only. It does not change capture
  manifest, reproduction bundle, readiness, release-candidate, audit, or corpus
  schema versions; does not contact live services; does not rotate canonical
  hashes; and does not introduce a benchmark reproduction claim.

## P77 Harbor Child Digest Binding

- Adds optional `child_digests` to
  `container_image_trust_report.images[]`, using exact
  `sha256:<64 lowercase hex>` grammar with no duplicates.
- Capture extraction copies non-empty Harbor discovery `child_digests` into the
  extracted container image trust report and rejects malformed child digests.
- Refines `cross_artifact_audit_image_binding`: reports without child digests
  keep the P76 manifest-digest binding behavior; reports with child digests bind
  live audit `image_digest` values to the child-digest union and fail closed if
  only some trust images declare children.
- Refines `capture_manifest_diff/1.0` `audit-image-binding` with the same
  child-digest semantics for plan-vs-realized drift reports.
- This is additive operator evidence binding only. It does not change capture
  manifest, reproduction bundle, readiness, release-candidate, audit, or corpus
  schema versions; does not contact live services; does not rotate canonical
  hashes; and does not introduce a benchmark reproduction claim.

## P78 Proposer LLM Request Log Binding

- Adds `proposer_llm_request_log` as a strict reproduction artifact class for
  paper-faithful bundles.
- The artifact records live `capture_run_id`, `round_count`, contiguous
  `rounds[].round_index` values, paper backend ids, paper model names,
  `request_sha256`, `response_sha256`, and non-negative token/proposal counts.
- `request_sha256` is computed from the canonical JSON object containing
  `system_prompt` and `user_prompt` plus a trailing newline; `response_sha256`
  is computed from the raw response string returned by the LLM client.
- Capture extraction maps raw `proposer_client` labels to closed paper backend
  ids (`minimax`, `qwen`, `glm`) using operator-supplied backend-map input and
  rejects unknown clients, unknown backends, malformed hashes, and round gaps.
- Adds `cross_artifact_proposer_model_binding` to reproduction bundle
  verification. When a proposer log is present, proposer-observed backends must
  match both `model_backend_preflight_report.backends` and
  `fixed_protocol_config.models`; absent logs are skipped for reduced non-paper
  bundles.
- The paper reproduction requirement catalog now requires
  `proposer_llm_request_log`, so `reproduction_readiness_result.json`,
  release-candidate, capture-manifest, and capture-rehearsal fixture hashes
  rotate. `tests/fixtures/canonical_llm_audit_hash.txt` stays unchanged because
  the recorder is opt-in and disabled on the canonical mock-LLM path.
- This is additive operator evidence binding only. It does not change audit or
  corpus schema versions; does not contact live services; and does not
  introduce a benchmark reproduction claim.

## P79 Proposer Round Count Binding

- Extends `fixed_protocol_config` with required positive
  `self_harness_rounds` and `proposal_width` fields, matching the paper's
  Algorithm 1 inputs `T` and `K`.
- Adds `cross_artifact_proposer_round_count` to reproduction bundle
  verification. When `proposer_llm_request_log` is present, its `round_count`
  and `rounds` length must match `fixed_protocol_config.self_harness_rounds`,
  and every round's `attempted_proposals` must match
  `fixed_protocol_config.proposal_width`.
- Tightens `proposer_llm_request_log` shape validation so
  `committed_proposals` cannot exceed `attempted_proposals`.
- Extends capture-manifest fixed-protocol diffing so plan-vs-realized protocol
  hashes include `self_harness_rounds` and `proposal_width`.
- This rotates paper-faithful fixed-protocol, capture-manifest,
  capture-rehearsal, reproduction-readiness, and release-candidate fixture
  hashes. The canonical LLM audit hash stays unchanged because the proposer
  recorder remains opt-in and disabled on the canonical mock-LLM path.
- This remains offline evidence binding only. It does not change audit or
  corpus schema versions, does not contact live services, and does not
  introduce a benchmark reproduction claim.

## P80 Proposer Context Ingredients Binding

- Adds `proposer_context_manifest` as a strict reproduction artifact class for
  paper-faithful bundles.
- The artifact records live `capture_run_id`, contiguous round indexes, and
  compact per-round hashes/counts for the paper's bounded Harness Proposal
  context ingredients: editable surfaces, verifier-grounded held-in failure
  patterns, passing behavior summaries, and previous attempted edits.
- Capture extraction accepts raw per-round context JSONL plus a live capture
  envelope and fails closed on unknown fields, malformed hashes, round gaps,
  non-live envelopes, or `reproduction_claimed:true`.
- Adds `cross_artifact_proposer_context_binding` to reproduction bundle
  verification. When proposer evidence is present, context `round_count` and
  round indexes must match `proposer_llm_request_log` and
  `fixed_protocol_config.self_harness_rounds`.
- Attempted proposer rounds must include non-empty editable-surface,
  held-in-failure, and passing-behavior blocks; non-initial rounds must also
  include previous attempted-edit summaries. Round zero may have an empty
  previous-edits block.
- This rotates paper-faithful capture-manifest, capture-rehearsal,
  reproduction-readiness, and release-candidate fixture hashes. Canonical audit
  and canonical LLM audit hashes stay unchanged because default audit output is
  not modified.
- This remains offline evidence binding only. It does not change audit or
  corpus schema versions, does not contact live services, and does not
  introduce a benchmark reproduction claim.

## P81 Proposer Context Evidence Derivation Binding

- Adds required `task_ids` to
  `proposer_context_manifest.rounds[].held_in_failure_patterns.patterns[]` and
  `proposer_context_manifest.rounds[].passing_behavior_summaries.summaries[]`.
- Adds `cross_artifact_proposer_context_evidence_binding` to reproduction
  bundle verification.
- The check requires held-in failure pattern task ids to cover exactly the
  held-in failing task set from the bundled split/evaluation/audit evidence,
  requires passing behavior summary task ids to cover exactly the held-in
  passing task set, and strictly recomputes each passing summary's
  `task_id_set_sha256` from `{"task_ids": sorted(task_ids)}`.
- `mechanism_sha256` and `preserved_behavior_sha256` remain compact opaque
  proposer attestations because the paper's abstract mechanism text is not
  deterministically recoverable from the bundled evidence without storing raw
  traces or prompts.
- This rotates paper-faithful capture-manifest and rehearsal fixture hashes.
  Canonical audit and canonical LLM audit hashes stay unchanged.
- This remains offline evidence binding only. It does not change audit or
  corpus schema versions, does not contact live services, and does not
  introduce a benchmark reproduction claim.

## P82 Proposer Previous-Edits Binding

- Extends `proposer_context_manifest.rounds[].previous_attempted_edits.edits[]`
  with required prior-round lineage fields:
  `proposal_round_index`, `targeted_mechanism_sha256`,
  `edited_surface_sha256`, `audit_decision`, and
  `audit_decision_reason`.
- `audit_decision` is closed to `accepted`, `rejected`, or `invalid`.
  `audit_decision_reason` is required as a string and must be non-empty for
  rejected or invalid edits; accepted edits may carry an empty reason.
- Adds `cross_artifact_proposer_previous_edits_binding` to reproduction bundle
  verification. When proposer evidence is present, previous edits must
  reference a real prior proposer/context round, bind their targeted mechanism
  hash to that prior round's held-in failure patterns, and bind their edited
  surface hash to that prior round's editable surfaces.
- Round zero still permits an empty previous-edits block. Reduced non-paper
  bundles can still omit both proposer artifacts.
- This rotates paper-faithful capture-manifest and rehearsal fixture hashes.
  Canonical audit and canonical LLM audit hashes stay unchanged.
- This remains offline evidence binding only. It does not change audit or
  corpus schema versions, does not contact live services, and does not
  introduce a benchmark reproduction claim.

## P83 Capture-Manifest Proposer Context Derivation Diff

- Adds a `proposer-context-evidence-derivation` finding to
  `capture_manifest_diff/1.0`.
- When the capture plan includes `live_terminal_bench_split_manifest` and the
  realized bundle includes `proposer_context_manifest`, the diff verifies that
  each realized proposer-context round's failure-pattern and passing-summary
  task-id union covers exactly the planned held-in split task ids.
- The finding is skipped for reduced bundles that omit proposer context. It is
  a plan-vs-realized diff only and does not replace bundle verification's
  held-in pass/fail derivation checks against evaluation and live-audit
  evidence.
- This rotates capture-manifest diff report hashes where proposer context is
  present. Canonical audit and canonical LLM audit hashes stay unchanged.
- This remains offline evidence binding only. It does not change audit or
  corpus schema versions, does not contact live services, and does not
  introduce a benchmark reproduction claim.

## P87 Proposal Validation Invalid-Candidate Categories

- Extends `proposal_validation_manifest/1.0` candidate rows with nullable
  `validation_failure_category`.
- The field is closed to `no_editable_surface` and `execution_failure` for
  invalid candidates and must be `null` for all non-invalid candidates.
- `changed_surfaces` remains required and non-empty for accepted, rejected,
  superseded, merged, and execution-failure candidates. It may be empty only
  for invalid `no_editable_surface` candidates, matching paper Section 3.4's
  rejected proposal that does not modify any editable surface.
- Capture extraction infers the category from audit row status and surface
  presence without parsing free-text rejection reasons.
- Reproduction bundle verification records
  `validation_failure_category_violations`; invalid candidates remain exempt
  from accepted/merged acceptance-rule comparisons.
- This rotates paper-faithful capture-manifest and rehearsal fixture hashes.
  Canonical audit and canonical LLM audit hashes stay unchanged because default
  audit output is not modified.
- This remains offline evidence binding only. It does not change audit or
  corpus schema versions, does not contact live services, and does not
  introduce a benchmark reproduction claim.

## P91 Proposal Validation Proposer Traffic Binding

- Extends `proposal_validation_manifest/1.0` round rows with optional paired
  fields: `proposer_round_request_sha256` and
  `proposer_round_response_sha256`.
- The fields must be present together when used and must be 64 lowercase
  hexadecimal SHA-256 digests.
- `capture-extract` can stamp the fields from a shaped
  `proposer_llm_request_log` artifact through
  `--proposer-request-log-artifact`; older extraction runs may omit them.
- Reproduction bundle verification records
  `proposer_round_traffic_violations` and fails when declared validation
  traffic hashes do not match the proposer LLM request log.
- `capture_manifest_diff/1.0` emits task-outcome digest version metadata in
  `proposal-validation-derivation` for the P90 task-outcome content digest
  definition. P95 later bumps the active version to `2` when terminal failure
  categories enter the normalized digest content.
- This rotates paper-faithful capture-manifest, capture-manifest diff, and
  rehearsal fixture hashes. Canonical audit and canonical LLM audit hashes stay
  unchanged.
- This remains offline evidence binding only. It does not store raw prompts,
  responses, or traces, does not contact live services, and does not introduce
  a benchmark reproduction claim.

## P92 Proposer Context Intermediate-Baseline Binding

- Changes reproduction bundle verification semantics without changing artifact
  schema versions.
- `cross_artifact_proposer_context_evidence_binding` now derives expected
  held-in failure and passing task sets from each same-round
  `proposal_validation_manifest.baseline_split_outcomes.task_outcomes` block
  instead of from the final `live_two_repeat_evaluation_report`.
- When `proposer_context_manifest` is present, the matching
  proposal-validation baseline task outcomes are required for every context
  round; older manifests that omit them remain shape-valid but cannot satisfy a
  full paper-fidelity bundle with proposer context.
- This reflects the paper's round-local weakness-mining contract: proposer
  context at round `t` is evidence over harness state `h_t`, not the final
  post-commit harness state.
- This rotates paper-faithful reproduction-readiness and capture-manifest
  fixture semantics where proposer context is present. Canonical audit and
  canonical LLM audit hashes stay unchanged.
- This remains offline evidence binding only. It does not contact live services
  and does not introduce a benchmark reproduction claim.

## P93 Proposal Grounding Binding To Proposer Context

- Changes reproduction bundle verification semantics without changing artifact
  schema versions.
- `cross_artifact_proposal_validation_binding` now verifies current
  proposal-validation candidates against the same-round
  `proposer_context_manifest` when proposer context is bundled.
- Candidate `targeted_mechanism_sha256` values must match a same-round held-in
  failure-pattern `mechanism_sha256`.
- Candidates with non-empty `changed_surfaces` must bind
  `edited_surface_sha256` to a same-round editable-surface `sha256`.
  `no_editable_surface` invalid candidates remain allowed to carry no editable
  surface binding while still targeting a known failure mechanism.
- Each validation round now fails closed when two candidates share the same
  `(targeted_mechanism_sha256, edited_surface_sha256)` signature.
- This remains offline evidence binding only. It does not parse free-text
  rationales, change the aggregate pass-count acceptance rule, contact live
  services, or introduce a benchmark reproduction claim.

## P94 Proposal Changed-Surface Name Grounding

- Changes reproduction bundle verification semantics without changing artifact
  schema versions.
- `cross_artifact_proposal_validation_binding` now verifies that each
  non-empty candidate `changed_surfaces` name exists in the same-round
  `proposer_context_manifest.editable_surfaces.surfaces[].name` set.
- The existing `edited_surface_sha256` binding remains independent, so
  surface-hash drift and surface-name drift are reported separately.
- Capture-manifest diffing now records and compares per-candidate
  changed-surface names for `proposal_validation_manifest` rehearsal plans.
- This remains offline evidence binding only. It does not enforce
  single-surface minimality, parse free-text rationales, contact live services,
  or introduce a benchmark reproduction claim.

## P95 Terminal Failure-Category Binding

- Extends the accepted `proposer_context_manifest/1.0` held-in failure-pattern
  shape with optional nullable `failure_category`, closed to the existing
  verifier terminal failure vocabulary excluding `verifier-pass`.
- Extends optional `proposal_validation_manifest/1.0` split
  `task_outcomes` rows with optional nullable `failure_category`, allowed only
  on failing task rows and closed to the same terminal failure vocabulary.
- `capture-extract` now propagates captured audit task-row
  `failure_category` values for failing rows and omits `verifier-pass` markers
  from shaped passing rows.
- `cross_artifact_proposer_context_evidence_binding` now records
  `failure_pattern_category_violations` and fails when same-round held-in
  failure-pattern task ids disclose mixed baseline terminal categories or when
  a pattern's declared `failure_category` disagrees with disclosed baseline
  task outcomes.
- `proposer-context-evidence-derivation` now compares planned versus realized
  proposer-context failure-pattern categories.
- `proposal-validation-derivation` now uses `task_outcomes_digest_version:2`,
  adding `failure_category` to the deterministic task-outcome digest so
  category-only drift is visible in capture rehearsals.
- This is additive and optional for reduced bundles. It does not contact live
  Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud services, parse raw
  traces, require categories when operators cannot disclose them, or introduce
  a benchmark reproduction claim.

## P96 Failure-Signature Causal Status Binding

- Extends the accepted `proposer_context_manifest/1.0` held-in failure-pattern
  shape with optional nullable `causal_status_sha256`, an opaque hash of the
  paper failure-signature `q` attribution.
- Extends accepted `previous_attempted_edits.edits[]` rows with optional
  nullable `causal_status_sha256`, so a prior attempted edit can bind to both
  the prior failure-pattern mechanism hash and causal-status hash.
- `capture-extract` now accepts raw nested `causal_status` strings inside
  proposer-context failure-pattern and previous-edit rows, emits only
  `causal_status_sha256`, and fails closed when a supplied hash disagrees with
  the raw string.
- `cross_artifact_proposer_previous_edits_binding` now records
  `causal_status_violations` and fails when an edit's declared
  `causal_status_sha256` does not match the referenced prior round's targeted
  failure mechanism causal-status hash.
- `proposer-context-evidence-derivation` now compares planned versus realized
  `causal_status_sha256` values per failure-pattern cluster.
- This does not bump `task_outcomes_digest_version`, because the new evidence
  is cluster-level and previous-edit-level rather than a task-outcome row
  extension. It also does not introduce a closed causal-status vocabulary,
  contact live services, or introduce a benchmark reproduction claim.

## P97 Failure-Pattern Symptom And Verifier-Evidence Hashes

- Extends the accepted `proposer_context_manifest/1.0` held-in failure-pattern
  shape with optional nullable `shared_symptoms_sha256` and
  `verifier_evidence_sha256`, covering the paper Section 3.2 cluster evidence
  pieces that sit between representative failing tasks and inferred
  mechanisms.
- `capture-extract` now accepts raw nested `shared_symptoms` and
  `verifier_evidence` strings or string lists in proposer-context failure
  patterns, emits only their stable hashes, and fails closed when supplied
  hashes disagree with the raw evidence.
- `cross_artifact_proposer_context_evidence_binding` now validates and records
  these opaque hashes in failure-pattern metadata, including deterministic
  counts for audit reports, while keeping absence compatible for reduced
  bundles.
- `proposer-context-evidence-derivation` now compares planned versus realized
  `shared_symptoms_sha256` and `verifier_evidence_sha256` values per
  failure-pattern cluster when the capture manifest declares them.
- This does not create a raw trace artifact, closed symptom vocabulary, new
  artifact class, audit/corpus schema bump, live service contact, or benchmark
  reproduction claim.

## P98 Failure-Pattern Presentation Order And Actionability

- Extends the accepted `proposer_context_manifest/1.0` held-in failure-pattern
  shape with optional nullable `presentation_order` and
  `actionability_hint_sha256`, covering the paper Section 3.2 requirement that
  weakness clusters be ordered by support and estimated actionability before
  proposal.
- When any pattern in a held-in failure-pattern block declares
  `presentation_order`, every pattern in that block must declare it and the
  values must form a contiguous permutation from zero.
- `support_rank` is intentionally not stored. Support ordering is derived from
  `size`; equal-size ordering remains actionability evidence rather than a
  stored rank.
- `capture-extract` now accepts raw nested `actionability_hint` strings inside
  proposer-context failure patterns, emits only `actionability_hint_sha256`,
  and fails closed when a supplied hash disagrees with the raw hint.
- `cross_artifact_proposer_context_evidence_binding` now records presentation
  order counts, actionability-hint hash counts, and ordering violations in
  metadata while preserving compatibility for reduced bundles that omit the
  optional fields.
- `proposer-context-evidence-derivation` now compares planned versus realized
  `presentation_order` and `actionability_hint_sha256` values per
  failure-pattern cluster.
- This does not introduce a closed actionability vocabulary, stored
  `support_rank`, new artifact class, audit/corpus schema bump, live service
  contact, or benchmark reproduction claim.

## P99 Accepted-Candidate Surface Distinctness

- Tightens `cross_artifact_proposal_validation_binding` without changing
  artifact schema versions.
- Accepted and merged proposal-validation candidates in the same round must now
  target pairwise-distinct `edited_surface_sha256` values before
  `MERGEACCEPTED` compatibility is trusted.
- The check applies only to candidates with `audit_decision` of `accepted` or
  `merged`; rejected, superseded, and invalid candidates may overlap because
  they do not contribute to the merged harness.
- Reproduction bundle verification records
  `merge_surface_conflict_violations` and fails when two accepted or merged
  candidates target the same editable surface hash in a round.
- Capture-manifest diffing now records and compares
  `accepted_merged_surface_sha256s` so rehearsed capture plans can detect drift
  in the accepted-surface set.
- This is a conservative machine-checkable proxy for the paper Algorithm 1
  `MERGEACCEPTED` compatibility step and Section 3.3 minimal-edit language. It
  does not enforce single-surface minimality per candidate, define a closed
  merge-compatibility vocabulary, inspect raw patches, add an artifact class,
  bump audit/corpus schemas, contact live services, or introduce a benchmark
  reproduction claim.

## P100 Proposal Validation Single-Surface Minimality

- Tightens `proposal_validation_manifest/1.0` candidate shape without changing
  artifact schema versions.
- Every candidate except invalid `no_editable_surface` candidates must declare
  exactly one `changed_surfaces` entry, matching the paper Section 3.3
  requirement that each proposal edits only the needed harness surface.
- Invalid `no_editable_surface` candidates remain the only allowed empty
  `changed_surfaces` case because they disclose that no editable surface was
  available for the attempted fix.
- `capture_manifest_diff/1.0` now records and compares
  `single_surface_violation_count` inside `proposal-validation-derivation` so
  rehearsal plans catch multi-surface proposal drift before bundle validation.
- This remains offline evidence binding only. It does not inspect raw patches,
  add a closed patch vocabulary, add an artifact class, bump audit/corpus
  schemas, contact live services, or introduce a benchmark reproduction claim.

## P101 Failure-Pattern Signature Distinctness

- Tightens `proposer_context_manifest/1.0` held-in failure-pattern validation
  without changing artifact schema versions.
- Within each proposer-context round, held-in failure patterns must now carry
  pairwise-distinct `(failure_category, causal_status_sha256, mechanism_sha256)`
  signatures.
- This makes the paper Section 3.2 exact-match failure clustering contract
  machine-checkable: two clusters may not represent the same verifier
  rejection category, causal-status hash, and reusable mechanism hash.
- The check treats `null` values as part of the signature, preserving reduced
  bundles that omit optional category or causal-status evidence while still
  preventing duplicate reduced signatures.
- This remains offline evidence binding only. It does not derive cluster ids,
  require distinct symptom/evidence hashes, add an artifact class, bump
  audit/corpus schemas, contact live services, or introduce a benchmark
  reproduction claim.

## P102 Failure-Pattern Support Ordering

- Tightens `proposer_context_manifest/1.0` held-in failure-pattern validation
  without changing artifact schema versions.
- When a held-in failure-pattern block declares `presentation_order`, larger
  clusters must appear earlier than smaller clusters. Equal-size ties remain
  unconstrained so Section 3.2's estimated-actionability ordering can decide
  among equal-support patterns.
- This extends the existing contiguous-permutation presentation-order invariant
  into a machine-checkable support-order invariant while avoiding a stored
  `support_rank` field.
- This remains offline evidence binding only. It does not derive cluster ids,
  enforce equal-size tie ordering, add an artifact class, bump audit/corpus
  schemas, contact live services, or introduce a benchmark reproduction claim.

## P105 Editable-Surface Distinctness

- Tightens `proposer_context_manifest/1.0` editable-surface validation without
  changing artifact schema versions.
- Within each proposer-context round, editable surfaces must be pairwise
  distinct by `sha256`. This keeps the paper Section 3.3 bounded proposer
  context shaped as a set of distinct harness configuration points rather than
  duplicated surface declarations.
- `cross_artifact_proposer_context_binding` now records
  `editable_surface_duplicate_violations` and fails when duplicate surfaces
  survive shape validation bypasses or future ingestion paths.
- `capture_manifest_diff/1.0` now records and compares
  `editable_surface_duplicate_count` inside
  `proposer-context-evidence-derivation`, so rehearsal plans catch duplicate
  editable-surface drift before bundle validation.
- This remains offline evidence binding only. It does not close the editable
  surface `kind` vocabulary, require surface-set stability across rounds,
  require every surface to be targeted by a proposal, add an artifact class,
  bump audit/corpus schemas, contact live services, or introduce a benchmark
  reproduction claim.

## P106 Proposal-Validation Evaluation Repeat Consistency

- Tightens `proposal_validation_manifest/1.0` validation without changing
  artifact schema versions.
- Within each proposal-validation round, every candidate
  `split_outcomes.evaluation_repeats` value must match that round's
  `baseline_split_outcomes.evaluation_repeats`.
- This makes Section 3.4 aggregate pass-count validation comparable: a
  candidate cannot be accepted, rejected, or merged against a baseline observed
  with a different repeat count.
- `cross_artifact_proposal_validation_binding` now records
  `evaluation_repeats_mismatch_violations` and fails when repeat mismatches
  survive shape validation bypasses or future ingestion paths.
- This remains offline evidence binding only. It does not require cross-round
  repeat-count stability, bind validation pass counts to final post-commit
  evaluation pass counts, add an artifact class, bump audit/corpus schemas,
  contact live services, or introduce a benchmark reproduction claim.

## P108 Proposal-Validation Harness-State Hash Continuity

- Extends `proposal_validation_manifest/1.0` validation rounds with optional
  paired `harness_before_sha256` and `harness_after_sha256` fields.
- When one harness-state hash is declared, the other must also be declared, and
  both must be 64 lowercase hex digests. Legacy manifests that omit both fields
  remain valid.
- `capture-extract` stamps these fields from audit `lineage.json`
  `harness_before_hash` / `harness_after_hash` rows when available and fails
  closed on malformed lineage hashes.
- `cross_artifact_proposal_validation_binding` now records
  `harness_continuity_violations`, `harness_continuity_missing_rounds`, and
  `harness_continuity_skipped_rounds`. Once any round declares harness hashes,
  adjacent no-op and single-commit transitions must follow the prior committed
  harness state in hash space; multi-commit rounds are skipped because the
  merged harness state is not represented by one candidate row.
- `capture_manifest_diff/1.0` compares `harness_hash_presence_count` inside
  `proposal-validation-derivation`, so rehearsal plans catch planned-versus-
  realized loss of this evidence.
- This makes the paper Algorithm 1 `MERGEACCEPTED` state transition
  machine-checkable in both split-outcome and harness-hash space for no-op and
  single-commit rounds. It does not introduce a new artifact class, require raw
  harness snapshots in reproduction bundles, recompute hashes from snapshots,
  bump audit/corpus schemas, contact live services, or introduce a benchmark
  reproduction claim.

## P109 Multi-Commit MERGEACCEPTED Harness-State Hash Continuity

- Extends `proposal_validation_manifest/1.0` validation rounds with optional
  `harness_after_merged_sha256`.
- The field is valid only on multi-commit rounds with paired
  `harness_before_sha256` / `harness_after_sha256`, must be a 64 lowercase hex
  digest, and must equal the round's `harness_after_sha256` because both are
  derived from audit lineage `harness_after_hash`.
- New-style multi-commit rounds that declare harness hashes must also declare
  `harness_after_merged_sha256`; legacy reduced manifests that omit all harness
  hashes remain valid.
- `capture-extract` stamps `harness_after_merged_sha256` from audit
  `lineage.json` when a round commits two or more proposals.
- `cross_artifact_proposal_validation_binding` uses the declared merged hash to
  enforce the next round's `harness_before_sha256` across multi-commit
  transitions, closing the P108 skip in harness-hash space.
- `capture_manifest_diff/1.0` compares `harness_after_merged_sha256` and
  `multi_commit_merged_hash_violation_count` inside
  `proposal-validation-derivation`, so rehearsal plans catch loss of
  multi-commit merged-hash evidence.
- Split-outcome lineage continuity for multi-commit rounds was intentionally
  left to P111, because it needs the engine's independent `__merge__`
  evaluation rows rather than a value derived from individual candidate rows.
- This does not add an artifact class, recompute merged hashes from raw patches
  or harness snapshots, bump audit/corpus schemas, contact live services, or
  introduce a benchmark reproduction claim.

## P111 Multi-Commit MERGEACCEPTED Split-Outcome Lineage Continuity

- Extends `proposal_validation_manifest/1.0` validation rounds with optional
  `merged_split_outcomes`.
- The field uses the same split-outcome shape as baseline and candidate rows,
  is valid only on multi-commit rounds with paired `harness_before_sha256` /
  `harness_after_sha256`, and must use the same `evaluation_repeats` value as
  the round baseline.
- New-style multi-commit rounds that declare harness hashes must also declare
  `merged_split_outcomes`; legacy reduced manifests that omit all harness
  hashes remain valid.
- `capture-extract` stamps `merged_split_outcomes` only from the engine's
  independent `proposal_id:"__merge__"`, `arm:"candidate"` audit evaluation
  rows and fails closed when those rows are missing for a new-style
  multi-commit round.
- `cross_artifact_proposal_validation_binding` uses the declared merged split
  outcomes as the expected next round baseline, closing the P107
  multi-commit split-outcome skip when this independent audit evidence exists.
- Legacy multi-commit manifests without harness hashes and without
  `merged_split_outcomes` still pass with an explicit
  `missing_merged_split_outcomes` skip.
- `capture_manifest_diff/1.0` compares merged split-outcome presence and a
  deterministic digest inside `proposal-validation-derivation`, so rehearsal
  plans catch loss or drift of this evidence.
- This does not change engine output, canonical audit hashes, artifact class
  coverage, live-service contact, or reproduction-claim semantics.

## P107 Proposal-Validation Lineage Continuity

- Tightens `cross_artifact_proposal_validation_binding` without changing
  artifact schema versions.
- Proposal-validation baselines must now follow the prior round's committed
  validation state when the transition is machine-checkable.
- If a previous round committed no proposals, the next round baseline must
  match the previous baseline split outcomes. If it committed exactly one
  proposal, the next round baseline must match that candidate's split outcomes.
  If it committed multiple proposals, exact lineage matching is skipped because
  the merged harness state is not represented by a single candidate row.
- Bundle verification records `lineage_continuity_violations` and
  `lineage_continuity_skipped_rounds` so synthetic and live evidence cannot
  silently reset harness state between rounds.
- This makes the paper Algorithm 1 `MERGEACCEPTED` state transition
  machine-checkable for no-op and single-commit rounds. It does not compare
  proposal-validation pass counts with the final post-commit evaluation, inspect
  raw patches, add an artifact class, bump audit/corpus schemas, contact live
  services, or introduce a benchmark reproduction claim.

## P104 Previous-Attempted-Edit Distinctness

- Tightens `proposer_context_manifest/1.0` previous-attempted-edit
  validation without changing artifact schema versions.
- Within each proposer-context round, previous attempted edits must be
  pairwise distinct by
  `(proposal_round_index, targeted_mechanism_sha256, edited_surface_sha256)`.
  This keeps the paper Section 3.3 bounded proposal context from repeating the
  same prior attempted edit as multiple rows.
- `cross_artifact_proposer_previous_edits_binding` now records
  `previous_edit_duplicate_violations` and fails when duplicate signatures
  survive shape validation bypasses or future ingestion paths.
- `capture_manifest_diff/1.0` now records and compares
  `previous_attempted_edit_signature_duplicate_count` inside
  `proposer-context-evidence-derivation`, so rehearsal plans catch duplicate
  prior-edit summaries before bundle validation.
- This remains offline evidence binding only. It does not require a non-empty
  previous-edit block beyond existing attempted-proposal rules, enforce
  cross-round edit stability, add an artifact class, bump audit/corpus schemas,
  contact live services, or introduce a benchmark reproduction claim.

## P103 Failure-Pattern Task-Id Disjointness

- Tightens `proposer_context_manifest/1.0` held-in failure-pattern validation
  without changing artifact schema versions.
- Within each proposer-context round, held-in failure-pattern `task_ids` must be
  pairwise disjoint. This makes the paper Section 3.2 exact-match clustering
  contract machine-checkable: one failed task has one failure signature and
  therefore belongs to one cluster.
- `cross_artifact_proposer_context_evidence_binding` now records
  `failure_pattern_task_overlap_violations` and fails when same-round clusters
  share a task id.
- `capture_manifest_diff/1.0` now records and compares
  `failure_pattern_task_overlap_count` inside
  `proposer-context-evidence-derivation`, so rehearsal plans catch task-overlap
  drift before bundle validation.
- This remains offline evidence binding only. It does not enforce inter-round
  cluster stability, constrain passing-behavior summaries, add an artifact
  class, bump audit/corpus schemas, contact live services, or introduce a
  benchmark reproduction claim.

## P50 Paper Model Backend Readiness
