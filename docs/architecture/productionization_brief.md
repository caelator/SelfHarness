# Productionization Brief

## Status

GLM convergence completed for P0 in
`docs/architecture/glm_productionization_plan.md` and for P1 in
`docs/architecture/glm_p1_fidelity_plan.md`. P0 productionization and the P1
paper-fidelity slice have been implemented.

Implemented:

- `src/` package layout;
- immutable `EngineConfig`;
- project-specific exceptions;
- schema versioning in manifest, proposal rows, evaluation rows, and lineage;
- expanded CLI runtime controls;
- ruff, mypy, pytest, build tooling;
- Makefile and GitHub Actions CI;
- tests for config validation, CLI wiring, invalid proposal audit handling, and
  schema-versioned artifacts;
- README development workflow and stable public API section.
- richer DeepAgent-like harness surfaces: tools, skills, memory sources, and
  subagents;
- `AppendToListSurface` with reversible list-surface edits;
- manifest `surface_kinds`;
- proposal addressability and diversity policy.

P2 audit readback and local subprocess adapters are also implemented. P3
provider-neutral LLM proposer seam is implemented. P4 task adapter and corpus
stabilization is implemented:

- versioned `TaskCorpus` schema and loader;
- structured task-load error reasons;
- neutral `TaskAdapter` protocol and `LocalSubprocessTaskAdapter`;
- `validate-tasks` CLI;
- `audit-diff` CLI;
- closed verifier failure taxonomy in local subprocess outcomes;
- audit schema `1.2` with `failure_category`;
- audit schema policy and changelog docs.

P5 readiness and release automation is implemented:

- `make readiness` paper-fidelity invariant gate;
- canonical deterministic audit hash fixture;
- stable audit-tree hashing helper;
- held-out context leakage promoted from `assert` to `PaperFidelityError`;
- CI wiring for `make check` and `make readiness`;
- tag-driven release workflow with release-candidate dry-runs;
- release policy and canonical-hash rotation policy.

P6 experimental Terminal-Bench/Harbor adapter boundary is implemented:

- Terminal-Bench-shaped manifest ingestion into `TaskCorpus`;
- pure harness-to-agent config rendering;
- `HarborRunner` dry-run mode with best-effort live boundary;
- `self-harness terminal-bench` CLI;
- schema `1.3` benchmark provenance fields;
- per-task `task_source_hash` in evaluation rows;
- readiness invariant preventing benchmark-protocol audit manifests from
  claiming reproduction.

P7 live preflight and capture scaffolding is implemented:

- typed Terminal-Bench live preflight reports;
- `terminal-bench-preflight` CLI;
- live mode preflight gate before engine execution;
- `terminal-bench-capture` single-task capture scaffold;
- synthetic Harbor capture test path;
- capture-artifact no-reproduction invariant.

P8 input integrity and provenance hardening is implemented:

- optional Ed25519 signature verification for task corpora;
- `--require-corpus-signature` gates for `validate-tasks` and `local-demo`;
- stable corpus integrity payload shared by checksums and signatures;
- structured `invalid-signature` task-load reason;
- captured Terminal-Bench fixture `task_source_hash` recording;
- dry-run replay rejection when captured fixture hashes no longer match the
  current manifest task hash;
- GitHub Actions CI matrix across Python 3.11, 3.12, and 3.13.

P9 LLM proposer and trajectory reporting is implemented:

- optional Anthropic Claude adapter for the provider-neutral `LLMProposer`;
- typed LLM provider/request exceptions;
- proposer-rendered held-in evidence bundles with paper-required pattern
  support, task ids, symptoms, verifier evidence, and mechanism fields;
- auditable proposer-side invalid reasons for ungrounded, unaddressable, and
  duplicate LLM suggestions;
- `audit-trajectory` CLI and trajectory schema `1.0`;
- canonical readiness hash coverage for derived trajectory bytes;
- CI guards proving core import works without optional extras and the Anthropic
  adapter contract works with mock transport.

P10 Harbor conformance and benchmark reporting is implemented:

- documented Harbor command construction for `--dataset`, `--agent`, `--model`,
  `--n-concurrent`, and `--env`;
- Terminal-Bench agent adapter seam for named agents and DeepAgent-style config
  materialization;
- versioned Harbor output parser for verifier-grounded live records;
- live `HarborRunner` refactor to use command construction and structured
  parser output;
- `benchmark-report` CLI and benchmark report schema `1.0`;
- provenance-completeness invariant for any future reproduction claim.

P11 Harbor artifact ingestion is implemented:

- `harbor-inspect` redacted artifact-tree inspection with file hashes;
- `harbor-ingest` offline conversion of preserved Harbor artifacts into schema
  `1.4` audit directories;
- source-attributed reward and trajectory parsing;
- `terminal-bench --mode live --keep-run-dir` preservation support;
- artifact validation status gates for future reproduction claims.

P12 LLM proposer engine-loop hardening is implemented:

- deterministic `MockLLMClient` for no-network provider-shaped engine tests;
- held-in-only LLM prompt rendering guard;
- mock-LLM full engine loop coverage against the toy task set;
- canonical LLM audit hash fixture;
- invariants for LLM prompt leakage, ungrounded proposals, and Terminal-Bench
  no-reproduction claims on the LLM path.

P13 release artifact installability is implemented:

- isolated wheel-install smoke script under `scripts/release_smoke.py`;
- `make smoke` and `make release-smoke` gates;
- installed CLI parity check against the canonical audit hash;
- local optional SBOM target;
- CI release-smoke job for all supported Python versions.

P14 harness inspection is implemented:

- `inspect_harness_run` and `write_harness_inspection` derived reporting APIs;
- `self-harness inspect-harness` CLI;
- stable retained-edit reports with per-round hashes, committed ops, reverse
  ops, proposal statuses, and final harness surfaces;
- derived harness inspection schema `1.0`.

P15 corpus signing workflow is implemented:

- offline Ed25519 key generation through `self-harness corpus-keygen`;
- signed corpus authoring through `self-harness corpus-sign`;
- stable public-key fingerprints through `self-harness corpus-fingerprint`;
- tests for signing, verification, tampering, checksums, fingerprint
  equivalence, overwrite safety, and private-key exclusion from corpus JSON.

P16 corpus keyring workflow is implemented:

- portable corpus trust manifests through `self-harness corpus-keyring`;
- active, retired, and revoked public-key statuses per `corpus_id`;
- `validate-tasks` and `local-demo` keyring gates over signed corpora;
- fingerprint recomputation on load, duplicate entry rejection, stable JSON
  writes, and tests for revoked/wrong-corpus trust failures.

P17 encrypted corpus signing keys are implemented:

- opt-in encrypted PKCS8 private-key generation through passphrase sources;
- encrypted private-key signing through `--passphrase`, `--passphrase-file`, or
  `--passphrase-env`;
- fixed redacted signing errors for missing or incorrect passphrases;
- tests for encrypted round trips, passphrase-file/env sources, missing
  passphrases, and passphrase exclusion from produced JSON/keyring artifacts.

P18 trusted in-process Python verifier adapter is implemented:

- `InProcessPythonTaskAdapter` and `InProcessPythonRunner` as a sibling to the
  local subprocess adapter;
- `python-demo` CLI with an explicit `--trust-verifier-module` trust boundary;
- structured verifier outcomes mapped through the closed `FailureCategory`
  enum, with unknown categories failing closed;
- fresh per-attempt workdirs, optional setup hooks, opaque
  `verifier_selector` metadata, and canonical readiness hash coverage.

P19 trusted HTTP verifier adapter is implemented:

- `HttpVerifierTaskAdapter` and `HttpVerifierRunner` as a stdlib-only network
  verifier boundary;
- `http-demo` CLI with an explicit `--trust-verifier-url` trust boundary;
- deterministic JSON request bodies and strict structured verifier response
  validation;
- timeout, non-2xx, malformed JSON, unknown category, and disallowed corpus URL
  tests using only local `127.0.0.1` servers;
- canonical readiness hash coverage for the HTTP verifier path.

P20 trusted container verifier boundary is implemented:

- `ContainerVerifierTaskAdapter` and `ContainerVerifierRunner` with dry-run and
  live modes;
- deterministic `docker run` command construction and fixture-backed dry-run
  verifier outcomes;
- `container-demo` CLI with an explicit `--trust-container-image` trust
  boundary;
- live-mode Docker preflight that writes `preflight.json` and exits before
  engine rounds when Docker is unavailable;
- corpus metadata guardrails forbidding images, commands, entrypoints, digests,
  and Docker args from task JSON;
- canonical readiness hash coverage for the container dry-run path.

P21 external corpus signer custody is implemented:

- `ExternalSignerResponse` and structured `ExternalSignerFailure` errors;
- a versioned external signer stdin/stdout protocol;
- `corpus-sign --external-signer` as a mutually exclusive alternative to
  `--private-key`;
- signer timeout and stdout size bounds with structured fail-closed behavior;
- fixture-backed subprocess tests using the same transport path as production;
- operator-visible public key, fingerprint, provider, and key-id provenance;
- no corpus, keyring, or audit schema change.

P22 verifier authentication and mTLS hardening is implemented:

- HTTP verifier custom CA bundle and mTLS client certificate/key support;
- TLS failures mapped to closed environment outcomes;
- container verifier env values moved from `docker run -e KEY=VALUE` argv into
  per-attempt env-files;
- `container-demo --env-file` and `--docker-config` operator controls;
- parent-process `DOCKER_CONFIG` support for private registry auth;
- redacted container command traces;
- extended corpus metadata guardrails for TLS, registry, auth, secret, and
  header-shaped keys;
- local mTLS and fake-Docker tests with no real registry, cloud, or external
  network dependency.

P23 container image policy enforcement is implemented:

- operator-owned image policy JSON loading for `container-demo`;
- active, retired, and revoked policy entry statuses;
- strict optional digest enforcement with `sha256:<64 lowercase hex>` grammar;
- policy gates in both dry-run and live modes before Docker preflight or engine
  rounds;
- fail-closed tests for missing policy entries, digest mismatch, required
  digest absence, and live rejection before Docker invocation;
- no audit, corpus, or manifest schema change.

P24 Harbor-side image policy enforcement is implemented:

- operator-owned image policy controls on `terminal-bench`;
- operator-pinned trusted image name and digest inputs for Harbor runs;
- pre-engine policy validation before live preflight or dry-run engine rounds;
- live parsed Harbor container digest verification after each task invocation;
- structured exit code 2 failures and cleanup of partial round directories on
  live policy rejection;
- no audit, corpus, or manifest schema change.

P25 release artifact provenance is implemented:

- deterministic release provenance manifest generation under `dist/`;
- SHA-256 and byte-size binding for wheel, source distribution, and optional
  SBOM artifacts;
- release-smoke verification of provenance before isolated wheel install;
- release CI generation and verification before artifact upload and publishing
  steps;
- no core dependency, audit schema, corpus schema, or reproduction-claim change.

P26 release provenance signing is implemented:

- detached Ed25519 signature sidecars over exact P25 manifest bytes;
- local encrypted/private PEM signing and external signer custody paths;
- sidecar verification of manifest filename, manifest SHA-256, fingerprint,
  public key, schema version, and signature bytes;
- release-smoke sidecar verification when present or explicitly requested;
- optional `make provenance-sign` operator target;
- no audit schema, corpus schema, manifest schema, or reproduction-claim change.

P27 dependency vulnerability policy checks are implemented:

- versioned vulnerability policy files with explicit justification and expiry;
- pip-audit-shaped finding parsing with fail-closed policy evaluation;
- built-wheel runtime dependency auditing through `make vuln-check`;
- release-smoke and release-workflow gating with structured JSON reports;
- fixture-backed offline tests for clean, unallowed, allowed, expired, and
  inactive-policy cases;
- no audit schema, corpus schema, manifest schema, readiness hash, or
  reproduction-claim change.

P28 container image vulnerability report evaluation is implemented:

- Trivy JSON report parsing into normalized vulnerability findings;
- reuse of the P27 vulnerability policy decision engine for container findings;
- offline CLI evaluation through `scripts/vuln_check.py --format trivy`;
- fixture-backed tests for clean, malformed, allowed, expired, inactive, alias,
  and multi-result report cases;
- no Docker, registry, Harbor, audit schema, corpus schema, manifest schema,
  readiness hash, or reproduction-claim change.

P29 Trivy report image-policy binding is implemented:

- extraction of Trivy `Metadata.RepoDigests` image/digest references;
- optional `--image-policy` enforcement for Trivy vulnerability reports;
- fail-closed behavior for missing report digests and image-policy mismatches;
- structured JSON reporting of image-policy allow/deny decisions;
- no Docker, registry, Harbor, audit schema, corpus schema, manifest schema,
  readiness hash, or reproduction-claim change.

P30 scanner report freshness validation is implemented:

- operator-owned freshness policy schema `1` with `max_age_days` and
  `not_before` rules;
- Trivy report timestamp extraction from top-level `CreatedAt` or
  `Metadata.CreatedAt`;
- optional `--freshness-policy` enforcement for Trivy vulnerability reports;
- fail-closed behavior for missing, malformed, future-dated, before-policy, and
  stale report timestamps;
- structured JSON reporting of freshness decisions;
- no scanner execution, Docker, registry, Harbor, audit schema, corpus schema,
  manifest schema, readiness hash, or reproduction-claim change.

P31 scanner execution orchestration is implemented:

- deterministic Trivy command construction for image plus optional digest
  targets;
- dry-run mode for CI-safe command validation without Trivy, Docker, registry,
  or scanner database access;
- replay mode that copies a supplied Trivy report and routes it through the
  existing P28-P30 vulnerability, image-policy, and freshness evaluators;
- live mode preflight that fails closed when Trivy or requested DB metadata is
  missing before scanner execution;
- `scripts/scanner_run.py` and `make scanner-check` as release/operator
  surfaces;
- no Docker daemon requirement, registry contact, Harbor execution, audit
  schema, corpus schema, manifest schema, readiness hash, or reproduction-claim
  change.

P32 scanner database freshness validation is implemented:

- operator-owned scanner DB freshness policy schema `1` with `max_age_days`
  and `require_next_update` controls;
- strict Trivy DB metadata parsing for `Version`, `NextUpdate`, and
  `UpdatedAt`;
- live scanner preflight rejection for stale, missing, malformed, or
  future-dated DB metadata;
- replay-mode DB freshness evaluation without requiring a Trivy binary;
- `make scanner-check` coverage for dry-run command construction and offline DB
  freshness replay;
- no scanner DB download/update orchestration, registry contact, Harbor
  execution, audit schema, corpus schema, manifest schema, readiness hash, or
  reproduction-claim change.

P33 scanner database update orchestration is implemented:

- deterministic Trivy DB update command construction using
  `trivy image --cache-dir <dir> --download-db-only`;
- `scripts/scanner_db_update.py` for dry-run and live operator execution;
- `make scanner-check` dry-run coverage for scanner DB update command
  construction;
- fail-closed live behavior when the Trivy binary is unavailable or exits
  non-zero;
- no CI scanner DB download, registry contact, Harbor execution, audit schema,
  corpus schema, manifest schema, readiness hash, or reproduction-claim change.

P34 pre-run Harbor image discovery orchestration is implemented:

- deterministic Harbor v2 artifact request construction for project,
  repository, and tag-or-digest references;
- strict replay parsing of Harbor artifact JSON into image/digest, tag, media
  type, and child-digest records;
- fail-closed handling for missing, malformed, or invalid digest fields;
- image-policy binding tests for discovered digests;
- `scripts/harbor_discovery.py` and `make harbor-discovery-check` as
  release/operator surfaces;
- no live Harbor CI contact, registry login, OAuth/OIDC refresh, audit schema,
  corpus schema, manifest schema, readiness hash, or reproduction-claim change.

P35 audit migration and operator trust-boundary templates are implemented:

- `self-harness audit-migrate` copy-first, upgrade-only migration from older
  audit schema metadata to the latest readable audit schema;
- deterministic migration reports with source/destination audit hashes,
  changed files, and explicit release/operator boundary language;
- synthetic legacy fixture coverage for schema `1.0` migration and fail-closed
  rejection of current-target, downgrade, malformed, or existing-destination
  cases;
- `scripts/example_external_signer.py` as a local-only Ed25519 reference
  implementation of the external signer protocol;
- first-class `--db-registry-config` support for scanner runs and scanner DB
  updates, mapped to Trivy `--registry-config` with absolute path traces and
  live-mode missing-file rejection;
- `make scanner-check` dry-run coverage for registry-config command
  construction;
- no source audit mutation, canonical readiness hash rotation, live registry
  login, KMS/HSM/YubiKey implementation, scanner DB download, audit writer
  default change, or reproduction-claim change.

P36 operator policy bundle and consolidated offline preflight are implemented:

- path-only `OperatorPolicyBundle` schema `1` for image policy, scanner report
  freshness policy, vulnerability policy, scanner DB freshness policy, and
  trusted public key references;
- strict bundle loading with unknown-field, missing-file, malformed JSON,
  unsupported-version, and expiry rejection;
- `scripts/operator_preflight.py` structured JSON report covering bundle
  validation, policy parsing, trusted public key fingerprinting, scanner
  command dry-run construction, scanner DB update dry-run construction,
  registry-config path presence, and optional Harbor discovery replay;
- `make operator-check` and a dedicated CI job running the fixture bundle
  offline;
- `docs/operations/operator_bundle.md` with paths-only authoring rules and
  secret/reproduction boundaries;
- no inline policy embedding, secret material, live Harbor/Docker/Trivy/PyPI/
  Sigstore/cloud contact, audit schema change, corpus schema change, readiness
  hash rotation, or reproduction-claim change.

P37 provider extension seams and release-candidate evidence aggregation are
implemented:

- `self_harness.providers` contracts for secret resolution, OAuth/OIDC token
  sources, registry credential providers, and KMS/HSM-style signers;
- process-local provider registry plus static non-production providers for
  tests and local dry-run demos;
- `scripts/release_candidate_evidence.py` strict aggregator over existing
  offline gate artifacts, including readiness hash, vulnerability policy,
  scanner execution, scanner DB update, Harbor discovery, operator preflight,
  operator promotion, release provenance, and optional provenance signature
  sidecar;
- deterministic evidence hash fixture coverage for the fixture release
  decision document;
- `make release-candidate-evidence` and CI fixture coverage for the aggregator;
- `docs/operations/provider_seams.md` and
  `docs/operations/release_candidate_evidence.md` documenting the extension
  and decision boundaries;
- no provider SDKs, secret material, live OAuth/OIDC, registry credential
  acquisition, KMS/HSM/YubiKey implementation, live Harbor/Docker/Trivy/PyPI/
  Sigstore/cloud contact, audit schema change, corpus schema change, readiness
  hash rotation, or reproduction-claim change.

P38 breaking-schema audit migration framework is implemented:

- `MigrationRegistry` and `MigrationTransform` model upgrade paths with
  `lossless`, `lossy`, and `unsupported` classifications;
- built-in lossless transforms cover audit schemas `1.0` through `1.4`;
- `self-harness audit-migrate` supports `--target-major`, `--allow-lossy`, and
  local `--transforms-json` operator registries;
- source audit hashes are checked before and after migration to prove copy-only
  behavior;
- migrated manifests carry `migration_applied=true` and a structured
  `migration_provenance` block with source hash, schema versions, transform ids,
  classification, notes, and lossy-approval state;
- fixture coverage spans source audit schemas `1.0`, `1.1`, `1.2`, `1.3`, and
  current-schema rejection for `1.4`, with deterministic expected hashes;
- lossy migrations are drop-only and fail closed unless `--allow-lossy` is
  supplied; unsupported transforms fail closed;
- `make migration-check` and a dedicated CI job exercise the offline fixture
  matrix as a standalone production gate;
- no source audit mutation, canonical readiness hash rotation, default writer
  change, live network contact, plugin transform loading, audit writer schema
  bump, corpus schema change, or reproduction-claim change.

P39 offline audit integrity verification is implemented:

- `verify_audit_run` checks an existing audit directory without mutating it or
  re-running tasks;
- structured verification reports include per-check status, deterministic
  `report_hash`, and an explicit non-reproduction boundary;
- verification covers manifest schema support, lineage/round coverage, harness
  snapshot hashes, round continuity, accepted proposal ids, row schema versions,
  baseline and committed split totals, proposal held-out leakage, and optional
  migration provenance shape;
- `self-harness audit-verify` exposes JSON and file-output modes with exit code
  `0` for clean, `2` for failed consistency checks, and `3` for corrupt core
  audit artifacts;
- `make audit-verify` generates a deterministic local audit under `dist/`,
  writes `trajectory.jsonl`, and verifies the audit report as an offline gate;
- `make readiness` and `make release-candidate-evidence` include the audit
  verification gate, and release-candidate evidence blocks on missing, malformed,
  failed, or reproduction-claiming audit verification artifacts;
- canonical generated audits across the demo, Python verifier, HTTP verifier,
  container verifier, and Terminal-Bench dry-run paths verify clean in the
  paper-fidelity invariant suite;
- no audit artifact mutation, audit schema change, corpus schema change,
  readiness hash rotation, live Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model
  contact, or reproduction-claim change.

P40 operator policy promotion is implemented:

- `self_harness.operator_promotion` models promotion manifests, entries,
  detached signatures, verification checks, and deterministic report hashes;
- `self-harness operator-promotion` supports manifest init, policy add,
  monotonic lifecycle advancement, local Ed25519 signing, external-signer
  signing, and signature-aware verification;
- promotion manifests bind operator-owned release-policy files to SHA-256
  digests, byte sizes, concrete policy kinds, and `draft`/`candidate`/`active`/
  `retired` lifecycle states;
- lifecycle transitions fail closed on backwards moves and retired
  reactivation;
- `scripts/operator_promotion_preflight.py` writes a structured offline
  preflight report, and `make operator-promotion-check` signs and verifies the
  fixture manifest with temporary local private key material outside `dist/`;
- release-candidate evidence now blocks on missing, malformed, failed, or
  reproduction-claiming operator promotion evidence;
- `docs/operations/operator_promotion.md` documents operator workflow,
  signing, external-signer custody, and release-gate boundaries;
- no audit schema change, corpus schema change, readiness hash rotation, live
  Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact, secret material,
  inline policy embedding, or reproduction-claim change.

P41 operator policy digest binding verification is implemented:

- `self_harness.operator_policy_binding` cross-checks operator policy bundle
  paths against active operator promotion entries and their SHA-256 digests;
- the verifier catches missing active promotion entries, extra active
  promotion entries, stale digests or byte sizes, malformed inputs, and optional
  promotion signature failures;
- retired promotion entries are ignored so historical policy material does not
  block current active release material;
- `scripts/operator_policy_binding_verify.py` writes a structured offline
  report with deterministic `report_hash`, `reproduction_claimed=false`, and
  explicit release/operator boundary language;
- `make operator-policy-binding-check` and CI's operator preflight job run the
  fixture gate;
- release-candidate evidence aggregation is intentionally deferred to P42 as a
  separate additive release-evidence schema slice;
- no audit schema change, corpus schema change, readiness hash rotation, live
  Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact, provider SDK
  dependency, bundle schema change, promotion schema change, or
  reproduction-claim change.

P42 release-candidate evidence binding integration is implemented:

- `scripts/release_candidate_evidence.py` now requires the P41 operator policy
  binding report and records it as the `operator_policy_binding` gate;
- binding reports that are missing, malformed, failed, or claim benchmark
  reproduction block the release-candidate decision;
- the binding report's deterministic `report_hash` is carried into gate
  metadata for operator triage;
- `make release-candidate-evidence` depends on `operator-policy-binding-check`
  and passes `dist/self-harness-operator-policy-binding.json`;
- CI's fixture release-candidate evidence job includes
  `tests/fixtures/release_candidate/operator_policy_binding_result.json`;
- the release-candidate evidence output schema remains `1.0` because the
  existing `gates` array is the additive extension point;
- no audit schema change, corpus schema change, readiness hash rotation, live
  Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact, provider SDK
  dependency, or reproduction-claim change.

P43 offline attestation verification contract and structural pre-validation is
implemented:

- `self_harness.attestations` parses operator-owned PyPI attestation envelopes,
  embedded Sigstore bundle material, and attestation trust-root files;
- `verify_attestation` writes deterministic reports with explicit structural
  checks, `cryptographic_valid=null` for the structural backend, and
  `reproduction_claimed=false`;
- `self-harness verify-attestation`, `scripts/verify_attestation.py`, and
  `make attestation-check` expose the offline operator gate;
- `SigstorePythonVerifier` defines the future cryptographic verifier seam and
  is contract-tested with an injected verifier so the core package does not
  require the Sigstore library;
- release-candidate evidence accepts an optional attestation report and the
  Makefile production stack supplies the generated structural report;
- CI runs both the structural attestation gate and the Sigstore backend
  contract tests across Python 3.11, 3.12, and 3.13;
- no audit schema change, corpus schema change, readiness hash rotation, live
  Fulcio/Rekor/PyPI/Sigstore/Harbor/Docker/registry/scanner/model/cloud
  contact, or reproduction-claim change.

P44 offline Sigstore cryptographic backend is implemented:

- `SigstorePythonVerifier` now lazily uses `sigstore-python` for
  `--backend sigstore` instead of failing closed as a stub;
- crypto mode verifies canonical Sigstore bundle material against artifact bytes
  and identity policy through an operator-supplied Sigstore client trust config
  or trusted-root file;
- the default structural backend and `make attestation-check` remain unchanged
  and do not require the optional Sigstore dependency;
- mocked offline tests prove bundle parsing, verifier wiring, verification
  failure handling, missing-extra failure, full-trust-config requirements, and
  `reproduction_claimed=false` semantics without checking in fake passing
  real-crypto material;
- CI includes an optional-extra `sigstore-crypto-backend` matrix job across
  Python 3.11, 3.12, and 3.13;
- no audit schema change, corpus schema change, readiness hash rotation, live
  Fulcio/Rekor/PyPI/Sigstore/Harbor/Docker/registry/scanner/model/cloud
  contact, or reproduction-claim change.

P45 offline operator readiness matrix is implemented:

- a checked-in readiness catalog enumerates live Harbor, Docker, Trivy,
  Sigstore, PyPI, model, scanner mirror, and signer dependencies;
- `scripts/readiness_matrix_report.py` validates the catalog and writes a
  deterministic report with `live_execution_blocked`, dependency counts,
  `report_hash`, and `reproduction_claimed=false`;
- `make readiness-matrix` and CI exercise the offline validation path;
- release-candidate evidence records the readiness matrix gate metadata;
- no live probing, audit schema change, corpus schema change, readiness hash
  rotation, provider SDK dependency, or reproduction-claim change.

P46 readiness matrix release evidence requirement is implemented:

- `scripts/release_candidate_evidence.py` now requires
  `--readiness-matrix-result`;
- fixture release-candidate evidence includes
  `tests/fixtures/release_candidate/readiness_matrix_result.json`;
- the release-candidate evidence fixture hash rotated because the required
  input set changed;
- the canonical paper-fidelity audit hash remains unchanged;
- a valid readiness matrix may still report `live_execution_blocked=true`; this
  is operator visibility, not a release-candidate failure;
- no audit schema change, corpus schema change, manifest schema change, live
  Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact, or reproduction-claim
  change.

P47 readiness catalog drift detection is implemented:

- readiness catalog schema `1.1` adds `preflight_surface` and
  `operator_action` metadata with strict enum validation and safe defaults for
  schema `1.0` catalogs;
- `self_harness.readiness_drift` evaluates provisioned, reproduction-relevant
  catalog entries against existing offline preflight artifacts;
- blocked and optional entries remain advisory, preserving the P46
  operator-visibility contract;
- `scripts/readiness_drift_report.py` writes deterministic reports with
  `report_hash`, explicit non-reproduction boundary language, and distinct
  exit codes for clean, drift, and corrupt inputs;
- `make readiness-drift-check` and release-candidate evidence now require the
  drift report;
- fixture readiness matrix, drift, and release-candidate evidence hashes are
  rotated;
- no live Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact, audit schema
  change, corpus schema change, manifest schema change, canonical readiness
  hash rotation, or reproduction-claim change.

P48 release-smoke readiness binding is implemented:

- `scripts/release_smoke.py` now writes deterministic
  `release_smoke_status/1.0` JSON to
  `dist/self-harness-release-smoke.json` on both success and controlled
  failure paths;
- the status report records required step-level checks, `report_hash`,
  `reproduction_claimed=false`, and boundary language limiting it to offline
  installability and artifact parity;
- the PyPI trusted-publishing readiness entry uses
  `preflight_surface: release_smoke` while remaining `blocked`, so readiness
  drift can enforce the surface after an operator explicitly advances the entry
  to `provisioned`;
- `make smoke` produces the release-smoke status, and
  `make readiness-drift-check` consumes it through
  `--release-smoke-result`;
- readiness matrix, readiness drift, and release-candidate evidence fixtures
  are regenerated; the canonical paper-fidelity audit hash is unchanged;
- no live PyPI/TestPyPI/Sigstore/OIDC/Harbor/Docker/model contact, audit schema
  change, corpus schema change, manifest schema change, canonical readiness
  hash rotation, or reproduction-claim change.

P49 benchmark reproduction readiness mapping is implemented:

- `docs/operations/benchmark_reproduction_requirements.json` maps the paper's
  live Terminal-Bench reproduction requirements to readiness-matrix
  dependencies and required artifact classes;
- `self_harness.reproduction_readiness` evaluates that catalog against the
  readiness matrix and artifact evidence, producing deterministic
  `reproduction_readiness` reports with `report_hash` and
  `reproduction_claimed=false`;
- `scripts/reproduction_readiness_report.py` exits `0` only when the paper
  reproduction contract is satisfied, `2` for a valid not-ready report, and `3`
  for corrupt inputs;
- `make reproduction-readiness-check` materializes
  `dist/self-harness-reproduction-readiness.json` without changing the default
  package release path;
- release-candidate evidence can consume reproduction readiness as advisory
  metadata, and `make release-candidate-evidence-reproduction` opts into a hard
  reproduction gate that remains blocked while live evidence is absent;
- no live Harbor/Docker/Trivy/PyPI/Sigstore/registry/scanner-db/model contact,
  audit schema change, corpus schema change, manifest schema change, canonical
  readiness hash rotation, or reproduction-claim change.

P50 paper model-backend readiness is implemented:

- `docs/operations/readiness_matrix.json` now tracks MiniMax M2.5,
  Qwen3.5-35B-A3B, and GLM-5 as separate blocked, reproduction-relevant model
  dependencies;
- Anthropic remains present as an optional package adapter seam, but it is not
  counted as a paper backend;
- `docs/operations/benchmark_reproduction_requirements.json` splits model
  readiness into per-backend requirements and requires the three paper backends
  for the fixed model/evaluator/tool-budget row;
- `self_harness.adapters.llm.paper_models` provides offline-testable
  chat-completions contracts for the paper backends, with tests that use fake
  transports and never contact providers;
- readiness matrix, readiness drift, reproduction-readiness, and release
  evidence fixtures are regenerated because their report metadata changed; the
  canonical paper-fidelity audit hash is unchanged;
- no live MiniMax/Qwen/GLM/OpenRouter/Anthropic contact, audit schema change,
  corpus schema change, manifest schema change, canonical readiness hash
  rotation, or reproduction-claim change.

P51 paper model-backend preflight is implemented:

- `self_harness.model_backend_preflight` writes deterministic readiness reports
  for MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5 using dry-run, replay, or
  explicit live modes;
- `scripts/model_backend_preflight.py` and `make model-backend-preflight`
  provide the operator command, with dry-run as the default no-contact mode and
  live mode gated behind `MODEL_BACKEND_PREFLIGHT_MODE=live`;
- the readiness matrix binds the three paper model dependencies to the new
  `model_backend_preflight` surface while keeping them `blocked` by default;
- readiness drift can ingest
  `dist/self-harness-model-backend-preflight.json` as an existing surface
  artifact, so provisioned model rows fail closed unless a passing model
  preflight report exists;
- reproduction readiness maps that same artifact to
  `model_backend_preflight_report` when present, without changing the
  fail-closed benchmark reproduction contract;
- no default release, check, drift, or reproduction-readiness target contacts
  model providers; live evidence remains operator-owned and never claims
  benchmark reproduction.

P53 readiness preflight surface parity is implemented:

- the Docker readiness row now uses `container_preflight` instead of `none`
  while remaining blocked by default;
- the Sigstore Fulcio/Rekor row now uses `attestation_check` instead of `none`
  while remaining blocked by default;
- `scripts/container_preflight_report.py` and `make container-preflight` write
  an offline Docker surface report that skips daemon and image probes;
- readiness drift ingests Docker and attestation surfaces while preserving
  advisory behavior for blocked rows;
- promotion is stricter than advisory evidence: provisioned Docker requires a
  live container-preflight report, and provisioned Sigstore requires the
  Sigstore backend with `cryptographic_valid: true`;
- no default release path contacts Docker, Sigstore, PyPI, Harbor, scanners,
  registries, model providers, or cloud services, and no benchmark reproduction
  claim is introduced.

P54 reproduction evidence bundle manifests are implemented:

- `src/self_harness/reproduction_bundle.py` verifies operator-supplied
  benchmark reproduction evidence bundle manifests;
- each bundle must declare exactly one entry per required live artifact class,
  with relative paths, SHA-256 digests, byte sizes, and constrained source
  metadata;
- bundle verification rejects duplicate, missing, unknown, path-escaping,
  digest-mismatched, byte-size-mismatched, shape-invalid, or reproduction-
  claiming artifacts;
- `scripts/reproduction_bundle_verify.py` writes deterministic bundle reports
  and can require a detached Ed25519 signature over exact bundle bytes;
- reproduction-readiness and shape-lint scripts can consume a bundle as the
  sole artifact source, while ad hoc `--artifact-dir`/`--artifact` inputs remain
  available for advisory checks;
- the hard `release-candidate-evidence-reproduction` path now requires a
  signed bundle report in addition to `reproduction_ready:true`; the default
  package release path is unchanged.

P55 reproduction evidence bundle authoring is implemented:

- `src/self_harness/reproduction_bundle_build.py` builds deterministic
  reproduction evidence bundle manifests from operator-supplied live artifact
  paths;
- `scripts/reproduction_bundle_build.py` requires explicit bundle id,
  operator label, created-at timestamp, source provider, and source capture
  timestamp, so it never injects a clock value, random id, or reproduction
  claim;
- the builder computes SHA-256 digests, byte sizes, relative bundle-rooted
  artifact paths, exact required-class coverage, optional per-entry source
  overrides, and class-specific shape validation before writing a manifest;
- `scripts/sign_reproduction_bundle.py` signs exact bundle manifest bytes with
  the same local-PEM or external-signer custody semantics used by release
  provenance signing, while emitting the P54 bundle signature sidecar schema;
- `make reproduction-bundle-build`, `make reproduction-bundle-sign`, and
  `make reproduction-bundle-check` provide an explicit operator workflow for
  authoring, signing, and verifying bundles without joining the default
  package `check` path;
- no live artifact capture, Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud
  contact, audit schema change, corpus schema change, canonical readiness hash
  rotation, or benchmark reproduction claim is introduced.

P56 readiness-catalog promotion admission is implemented:

- `src/self_harness/readiness_promotion.py` verifies baseline-to-candidate
  readiness catalog transitions without mutating either catalog;
- the provisioned-surface contract is shared with readiness drift, so Docker
  live mode, Sigstore cryptographic validity, model-backend live mode, failed
  required checks, missing surface reports, and reproduction-claim leakage are
  enforced consistently for both promotion admission and promoted-state drift;
- `scripts/readiness_promotion_report.py` writes deterministic
  `readiness_promotion/1.0` reports with admitted, rejected, and advisory
  transition lists, `unchanged_count`, `report_hash`, and
  `reproduction_claimed=false`;
- `make readiness-promotion-check` compares `READINESS_BASELINE_CATALOG`
  against `READINESS_CANDIDATE_CATALOG` using existing surface artifacts under
  `dist/`, without running live tools or regenerating evidence;
- release-candidate evidence can consume a readiness-promotion report as
  optional advisory metadata, while the default release path remains unchanged;
- no catalog mutation, signed snapshot scheme, live probing, audit schema
  change, corpus schema change, canonical readiness hash rotation, or benchmark
  reproduction claim is introduced.

P57 release-candidate evidence promotion integration is implemented:

- `make release-candidate-evidence` now depends on
  `readiness-promotion-check` and passes
  `dist/self-harness-readiness-promotion.json` into the release-candidate
  aggregator;
- the default release path uses the existing
  `READINESS_BASELINE_CATALOG=READINESS_CANDIDATE_CATALOG` defaults, so it
  records a deterministic no-op advisory gate unless an operator supplies a
  candidate catalog;
- CI fixture release-candidate evidence includes
  `tests/fixtures/release_candidate/readiness_promotion_result.json` and asserts
  the `readiness_promotion` gate is present, non-required, and passing;
- the release/operator fixture hash rotated for the new advisory input set, but
  the canonical audit/readiness hash did not change;
- reproduction bundle admission remains opt-in through
  `release-candidate-evidence-reproduction`; no live Harbor, Docker, scanner,
  PyPI, Sigstore, model, or cloud contact and no benchmark reproduction claim is
  introduced.

P58 operator live-evidence capture manifest contract is implemented:

- `src/self_harness/capture_manifest.py` validates signed, operator-owned
  pre-capture manifests for planned Terminal-Bench live evidence runs;
- planned artifact shapes are checked with the same class validators used by
  reproduction bundle/readiness verification, and required classes are derived
  from `docs/operations/benchmark_reproduction_requirements.json`;
- `src/self_harness/capture_manifest_diff.py` compares a capture manifest to a
  realized reproduction bundle and reports missing, extra, source-drift,
  custody-drift, binding-drift, and advisory capture-window findings;
- `scripts/capture_manifest_verify.py`, `scripts/sign_capture_manifest.py`, and
  `scripts/capture_manifest_diff.py` expose the operator workflow, while
  `self-harness capture-manifest verify|diff` exposes installed CLI access;
- `make capture-manifest-check` and `make capture-manifest-diff-check` exercise
  the offline fixture-backed path without joining the default release smoke
  stack;
- no live service contact, default release-path change, audit/corpus/readiness
  schema change, canonical hash rotation, or benchmark reproduction claim is
  introduced.

P59 capture manifest authoring is implemented:

- `src/self_harness/capture_manifest_build.py` builds deterministic P58 capture
  manifests from explicit operator metadata, planned run parameters, signing
  custody, source windows, and optional per-class planned-artifact templates;
- required artifact classes are derived from
  `docs/operations/benchmark_reproduction_requirements.json`, and missing
  templates are filled with deterministic validator-compatible planning stubs;
- `scripts/capture_manifest_build.py` and
  `self-harness capture-manifest build` expose the same authoring path;
- `make capture-manifest-build` creates an offline fixture manifest, while
  `make capture-manifest-check` now builds, signs, verifies, and tests the
  capture-manifest workflow;
- the builder never injects the current clock, random ids, live contact, or
  `reproduction_claimed=true`, and it reuses the P58 schema rather than adding
  a new evidence schema.

P60 offline capture pipeline rehearsal is implemented:

- `src/self_harness/capture_rehearsal.py` materializes synthetic artifacts from
  a capture manifest's planned artifact shapes, then builds a reproduction
  bundle through the existing P55 bundle builder;
- the rehearsal can sign the synthetic bundle with local or external signer
  custody, then runs reproduction-bundle verification and the P58
  plan-vs-bundle diff without relaxing existing custody checks;
- the rehearsal evaluates reproduction readiness against the synthetic bundle
  and records `reproduction_ready` separately from rehearsal `ok`, so blocked
  live dependencies remain visible instead of being hidden by a green rehearsal;
- `scripts/capture_rehearsal.py`,
  `self-harness capture-manifest rehearse`, and `make capture-rehearsal` expose
  the workflow;
- `make capture-manifest-check` now exercises build, sign, signed verify,
  rehearse, signed synthetic-bundle diff, and capture-manifest tests;
- no live service contact, default release-path change, audit/corpus/readiness
  schema change, canonical hash rotation, or benchmark reproduction claim is
  introduced.

P61 live audit verification provenance seam is implemented:

- `src/self_harness/audit_verify_live.py` wraps the replay audit verifier with
  signed live Harbor provenance checks;
- `scripts/audit_verify_live.py` and `self-harness audit-verify-live` expose a
  post-capture operator workflow for the `audit_verify_report` artifact class;
- the verifier emits `mode:"live"` only when replay verification, exact-byte
  provenance signature, live Harbor artifact shape, and task-id/task-source
  binding checks pass; failures emit `mode:"live_blocked"`;
- `make audit-verify-live` produces a deterministic offline fixture report
  without contacting Harbor, Docker, models, registries, scanners, PyPI,
  Sigstore, or cloud services;
- no audit schema change, corpus schema change, default release-path change,
  canonical hash rotation, or benchmark reproduction claim is introduced.

P62 post-capture live-evidence extractors are implemented:

- `src/self_harness/capture_extract.py` provides strict offline transforms from
  operator raw live outputs into six reproduction artifact-class JSON shapes;
- `scripts/capture_extract.py` and `self-harness capture-extract` expose the
  dispatcher for `live_harbor_preflight_report`,
  `container_image_trust_report`, `model_backend_preflight_report`,
  `network_resource_controls_attestation`, `live_harbor_audit`, and
  `live_two_repeat_evaluation_report`;
- extractors validate their outputs with the existing artifact-shape validators
  and reject unknown fields, non-live modes, missing digests, wrong repeat
  counts, timestamp injection, and `reproduction_claimed:true` leakage;
- `make capture-extract-check` runs the offline fixture-backed test path;
- no audit schema change, corpus schema change, default release-path change,
  readiness mutation, canonical hash rotation, or benchmark reproduction claim
  is introduced.

P63 fixed split and protocol extractors are implemented:

- `src/self_harness/capture_extract.py` now also emits
  `live_terminal_bench_split_manifest` and `fixed_protocol_config` from
  operator-owned live-run declarations;
- split extraction requires `mode:"live"`, `source:"harbor"`, exactly 64
  disjoint task ids, matching held-in/held-out counts, and an explicit Harbor
  version;
- protocol extraction requires `terminal-bench@2.0`, the paper's MiniMax,
  Qwen, and GLM backends, non-empty evaluator/tool-set labels, a decoding
  budget object, and `fixed_across_variants:true`;
- the installed CLI and standalone script share the same dispatcher flags:
  `--split-manifest-result` and `--fixed-protocol-declaration`;
- the extractor gate rejects unknown fields, timestamp injection, malformed
  split/protocol material, and `reproduction_claimed:true` leakage;
- readiness still remains blocked until operators provision live Harbor,
  Docker, paper model backends, PyPI, and Sigstore evidence.

P64 capture admission orchestration is implemented:

- `src/self_harness/capture_admit.py` composes the existing
  extract/build/verify/readiness primitives into one hash-stable admission
  report;
- `scripts/capture_admit.py` and `self-harness capture-admit` expose the
  operator workflow for binding raw inputs, extracted artifacts, supplied
  post-capture artifacts, bundle verification, and optional readiness
  evaluation;
- admission requires explicit bundle/source metadata and never fills ids,
  labels, timestamps, or source fields from the environment or clock;
- `--skip-readiness` is explicit and produces a distinct report hash from a
  full readiness admission;
- `make capture-admit-check` runs the offline fixture-backed operator test
  path;
- no new artifact class, shape validator, readiness gate, default release-path
  dependency, canonical hash rotation, or benchmark reproduction claim is
  introduced.

P65 two-repeat aggregate validation is implemented:

- `live_two_repeat_evaluation_report` artifacts now require `task_count`,
  `attempt_count`, `pass_count`, and `fail_count`;
- artifact-shape validation reconciles those aggregates against
  `per_task_attempts` and rejects summary drift;
- the two-repeat artifact now has a closed top-level field set, so unreviewed
  derived metrics such as `pass_rate` cannot enter reproduction evidence by
  accident;
- `capture-extract` computes the aggregates from raw attempt JSONL while
  keeping `reproduction_claimed=false`;
- this improves the paper Section 4.1 repeated-attempt evidence path without
  changing readiness dependencies or pretending live Harbor/model evidence has
  been provisioned.

P66 split/evaluation coverage verification is implemented:

- reproduction bundle verification now emits
  `cross_artifact_split_evaluation_coverage`;
- repeated-evaluation task ids must exactly match the fixed split manifest's
  held-in and held-out task union;
- the bundle check requires `task_count=64` and `attempt_count=128`, matching
  the paper's fixed 64-case subset and two repeated attempts per task;
- capture admission inherits this check through bundle verification, preventing
  an operator admission report from accepting a smaller or divergent evaluation
  artifact;
- readiness dependencies remain unchanged and live Harbor/model/PyPI/Sigstore
  evidence is still required before reproduction readiness can pass.

P67 live Harbor audit coverage verification is implemented:

- `live_harbor_audit` artifacts now require exactly two attempt rows per task
  with distinct attempt indexes;
- verifier outcomes must match the attempts, passing only when both attempts
  pass;
- reproduction bundle verification now emits
  `cross_artifact_audit_split_coverage`;
- live Harbor audit task ids must exactly match both the fixed split manifest
  and the two-repeat evaluation report;
- capture admission inherits this through bundle verification, keeping live
  artifact ingest evidence tied to the same paper Section 4.1 task set without
  claiming reproduction readiness before live dependencies are provisioned.

P68 fixed protocol binding verification is implemented:

- `live_two_repeat_evaluation_report` and `live_harbor_audit` artifacts now
  carry `fixed_protocol_sha256`;
- `capture-extract` stamps that value from a validated
  `fixed_protocol_config` artifact, or validates an explicit protocol hash
  against the artifact when both are supplied;
- reproduction bundle verification now emits
  `cross_artifact_protocol_binding`;
- the new check recomputes the bundled `fixed_protocol_config` byte hash and
  rejects evaluation or audit evidence that references a different fixed model,
  evaluator, tool set, or decoding/tool budget protocol;
- capture admission injects the already materialized fixed protocol artifact
  into downstream raw extraction, so the operator happy path stays explicit but
  avoids hand-duplicated hashes;
- this keeps future live Terminal-Bench evidence tied to the same paper Section
  4.2 fixed-protocol declaration without claiming reproduction readiness before
  live dependencies are provisioned.

P69 model-backend protocol binding verification is implemented:

- reproduction bundle verification now emits
  `cross_artifact_model_protocol_binding`;
- the check normalizes the bundled `fixed_protocol_config.models` and
  `model_backend_preflight_report.backends` with the canonical paper-backend
  aliases and requires both artifacts to cover the same `minimax`, `qwen`, and
  `glm` set;
- the invariant skips only when both artifacts are absent and fails closed when
  exactly one side is present;
- capture admission inherits the rejection through bundle verification, so an
  operator admission report cannot accept model preflight evidence that drifts
  from the fixed protocol declaration;
- this closes another paper Section 4.1/4.2 fixed-protocol surface without
  changing readiness dependencies or claiming reproduction readiness before
  live model, Harbor, Docker, PyPI, and Sigstore evidence is provisioned.

P70 evaluation/audit outcome binding verification is implemented:

- reproduction bundle verification now emits
  `cross_artifact_evaluation_audit_outcomes`;
- the check compares each `live_two_repeat_evaluation_report` task's ordered
  attempt pass values against the corresponding `live_harbor_audit` attempts
  keyed by `attempt_index`;
- the audit row's `verifier_outcome` must also match the evaluation-derived
  task outcome, so a task cannot be `pass/pass` in one artifact and `fail` or
  differently ordered in the other;
- capture admission inherits the rejection through bundle verification, so
  post-capture admission fails if extracted attempt JSONL and live Harbor trial
  artifacts disagree;
- this ties the paper Section 4.1 repeated-attempt metric to the same verifier
  outcome evidence without changing artifact schemas, readiness dependencies,
  or reproduction-claim semantics.

P71 Harbor environment version binding verification is implemented:

- `live_terminal_bench_split_manifest` artifacts now require a non-empty
  `harbor_version`, matching the field already emitted by `capture-extract`;
- reproduction bundle verification now emits
  `cross_artifact_harbor_version_binding`;
- the check requires the bundled split manifest and
  `live_harbor_preflight_report` to agree on the same Harbor version and fails
  closed if exactly one side is present;
- this catches Harbor execution-environment drift across the paper Appendix
  A.1 Terminal-Bench-2.0 evidence bundle without adding any live dependency or
  claiming reproduction readiness before live Harbor/Docker/model/PyPI/Sigstore
  evidence is provisioned.

P72 capture-run identity binding verification is implemented:

- primary captured live artifact classes now require a non-empty
  `capture_run_id`;
- derived post-capture artifacts, `audit_verify_report` and
  `release_candidate_evidence`, stay exempt so summaries are not mistaken for
  raw live capture evidence;
- `capture-extract`, capture admission, and planned capture manifests stamp one
  shared run id across fixed split, two-repeat evaluation, fixed protocol,
  Harbor preflight, container trust, model preflight, network controls, and live
  Harbor audit artifacts;
- reproduction bundle verification now emits
  `cross_artifact_capture_run_id_binding` and rejects primary evidence assembled
  from different runs or missing run identity;
- live audit verification binds signed provenance `capture_run_id` to the
  supplied live Harbor audit artifact's `capture_run_id`;
- this catches stitched-together Terminal-Bench evidence bundles without adding
  any live dependency or claiming reproduction readiness before live
  Harbor/Docker/model/PyPI/Sigstore evidence is provisioned.

P73 capture-manifest run identity diffing is implemented:

- reproduction bundle verification and capture-manifest diffing now share the
  same primary captured artifact `capture_run_id` reader;
- `capture_manifest_diff` emits `capture-run-id-binding` and fails when the
  realized bundle's primary captured artifacts do not share
  `capture_manifest.planned_run.run_id`;
- the existing diff remains offline and additive, with no live Harbor, Docker,
  model, scanner, PyPI, Sigstore, registry, or cloud contact;
- no capture manifest, reproduction bundle, readiness, release-candidate, audit,
  or corpus schema version changes are introduced, and no benchmark reproduction
  claim is made.

P74 capture-manifest network-control diffing is implemented:

- reproduction bundle verification and capture-manifest diffing now share a
  public artifact-payload reader for bundled JSON evidence;
- `capture_manifest_diff` emits `network-control-binding` when a realized
  `network_resource_controls_attestation` artifact is present;
- the finding fails when the realized outbound bandwidth cap or mirrored
  resource set differs from `capture_manifest.planned_run`;
- reduced requirement sets that omit the network-control artifact skip this
  finding instead of fabricating drift;
- this remains an offline, additive diff check with no live Harbor, Docker,
  model, scanner, PyPI, Sigstore, registry, or cloud contact;
- no capture manifest, reproduction bundle, readiness, release-candidate, audit,
  or corpus schema version changes are introduced, no canonical hashes rotate,
  and no benchmark reproduction claim is made.

P75 capture-manifest fixed-protocol diffing is implemented:

- `capture_manifest_diff` emits `fixed-protocol-binding` when both the capture
  plan and realized bundle include `fixed_protocol_config`;
- the finding compares a deterministic hash of the protocol core:
  `benchmark_protocol`, normalized paper model backends, `evaluator`,
  `tool_set`, `decoding_budget`, and `fixed_across_variants`;
- the check fails when the operator planned one fixed Terminal-Bench protocol
  but packaged evidence for another, while ignoring non-protocol metadata such
  as capture run ids;
- reduced requirement sets that omit the fixed protocol artifact keep relying
  on the existing missing-class findings instead of inventing a separate
  protocol drift failure;
- this remains an offline, additive diff check with no live Harbor, Docker,
  model, scanner, PyPI, Sigstore, registry, or cloud contact;
- no capture manifest, reproduction bundle, readiness, release-candidate, audit,
  or corpus schema version changes are introduced, no canonical hashes rotate,
  and no benchmark reproduction claim is made.

P76 live-audit container image digest binding is implemented:

- `live_harbor_audit.trial_artifacts[]` now accepts optional exact
  `image_digest` values, and `container_image_trust_report.images[].digest`
  uses the same `sha256:<64 lowercase hex>` grammar;
- capture extraction reads optional Harbor trial `metadata.json` image digests
  and fails when per-task attempts have malformed, missing-on-one-attempt, or
  conflicting digest material;
- reproduction bundle verification emits
  `cross_artifact_audit_image_binding` when live audit rows carry image
  digests, requiring the executed audit image digest set to match the bundled
  container image trust report;
- `capture_manifest_diff` emits `audit-image-binding` when planned or realized
  live audit image digests are present, binding planned audit, realized audit,
  and trusted image digest sets;
- older offline fixtures that omit live audit image digests continue to verify
  without hash rotation, while future operator bundles with digest material fail
  closed on drift;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact, and no benchmark
  reproduction claim is made.

P77 Harbor multi-arch child-digest binding is implemented:

- `container_image_trust_report.images[]` now accepts optional non-empty
  `child_digests` for multi-arch Harbor manifests, using the same exact
  `sha256:<64 lowercase hex>` grammar as parent image digests;
- capture extraction copies non-empty Harbor discovery `child_digests` into the
  trust report and rejects malformed or duplicate child digest lists;
- reproduction bundle verification preserves P76 single-arch manifest-digest
  binding when no child digests are declared, but binds live audit
  `image_digest` values to the child-digest union when trust images declare
  children;
- mixed trust reports where only some images declare `child_digests` fail
  closed instead of silently comparing different digest namespaces;
- `capture_manifest_diff` applies the same child-digest semantics to
  `audit-image-binding` so planned audit, realized audit, and trusted Harbor
  image evidence cannot drift in multi-arch deployments;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact, and no benchmark
  reproduction claim is made.

P78 proposer LLM request-log binding is implemented:

- `proposer_llm_request_log` is now a strict reproduction artifact class for
  paper-faithful bundles, carrying live `capture_run_id`, contiguous round
  indexes, paper backend ids, paper model names, request/response SHA-256
  hashes, and non-negative token/proposal counts;
- `SelfHarnessEngine` can opt into an engine-owned `RecordingLLMClient` wrapper
  that writes raw proposer request-log JSONL with stable request and response
  hashes while leaving default audit output and canonical mock-LLM hashes
  unchanged;
- capture extraction turns the raw request-log rows plus an operator
  `proposer_client` to paper-backend map into the live artifact, failing closed
  on unknown clients, unknown backends, malformed hashes, round gaps, non-live
  envelopes, or reproduction-claim leakage;
- reproduction bundle verification emits
  `cross_artifact_proposer_model_binding` when the artifact is present,
  requiring proposer-observed backends to match both the model-backend preflight
  report and the fixed protocol declaration;
- reduced non-paper bundles can omit the proposer log, but the paper
  reproduction requirement catalog now requires it before
  `reproduction_ready:true`;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P79 proposer round-count binding is implemented:

- `fixed_protocol_config` now records the paper's Self-Harness round count
  (`self_harness_rounds`) and proposal width (`proposal_width`) alongside the
  fixed benchmark, model, evaluator, tool-set, and decoding budget contract;
- capture extraction and capture-manifest planning emit those protocol fields,
  and capture-manifest diffing includes them in the fixed-protocol core hash;
- reproduction bundle verification emits
  `cross_artifact_proposer_round_count` when a proposer LLM request log is
  present, requiring proposer `round_count`, `rounds` length, and each round's
  `attempted_proposals` value to match the fixed protocol's `T` and `K`;
- `proposer_llm_request_log` shape validation now also fails closed when
  `committed_proposals` exceeds `attempted_proposals`;
- reduced non-paper bundles can still omit proposer LLM logs, but paper
  reproduction bundles now have an offline verifier for the proposer schedule
  declared by the fixed protocol;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P80 proposer context-ingredients binding is implemented:

- `proposer_context_manifest` is now a strict reproduction artifact class for
  paper-faithful bundles, carrying live `capture_run_id`, contiguous round
  indexes, and compact per-round hashes/counts for the four Section 3.3
  Harness Proposal context ingredients: editable surfaces, held-in failure
  patterns, passing behavior summaries, and previous attempted edits;
- capture extraction turns raw per-round context JSONL plus a live capture
  envelope into the normalized artifact, failing closed on unknown fields,
  malformed hashes, round gaps, non-live envelopes, or reproduction-claim
  leakage;
- reproduction bundle verification emits
  `cross_artifact_proposer_context_binding` when proposer artifacts are
  present, requiring context `round_count` and round indexes to align with
  `proposer_llm_request_log` and `fixed_protocol_config.self_harness_rounds`;
- attempted proposer rounds must carry non-empty editable-surface,
  held-in-failure, and passing-behavior blocks, and non-initial rounds must
  carry previous attempted-edit summaries;
- reduced non-paper bundles can still omit both proposer artifacts, but paper
  reproduction bundles now require context evidence before
  `reproduction_ready:true`;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P81 proposer context evidence-derivation binding is implemented:

- `proposer_context_manifest` held-in failure patterns and passing behavior
  summaries now carry explicit `task_ids` so compact proposer-context evidence
  is no longer just hashes and counts;
- reproduction bundle verification emits
  `cross_artifact_proposer_context_evidence_binding` when proposer context,
  fixed split, two-repeat evaluation, and live Harbor audit evidence are
  present;
- held-in failure pattern task ids must cover exactly the held-in failing task
  set, pattern `size` must match the task-id count, and task ids outside the
  held-in failing set fail closed;
- passing behavior summary task ids must cover exactly the held-in passing task
  set, and each summary's `task_id_set_sha256` is recomputed from the sorted
  task-id set;
- `mechanism_sha256` and `preserved_behavior_sha256` remain opaque compact
  proposer attestations because the paper's mechanism text is not
  deterministically recoverable without bundling raw traces or prompts;
- capture extraction can validate proposer-context task ids against the fixed
  split when `--split-manifest-result` is supplied, and capture admission
  auto-injects the materialized split artifact for that extraction path;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P82 proposer previous-edits binding is implemented:

- `proposer_context_manifest` previous attempted edits now carry prior
  `proposal_round_index`, `targeted_mechanism_sha256`,
  `edited_surface_sha256`, closed `audit_decision`, and
  `audit_decision_reason` fields;
- reproduction bundle verification emits
  `cross_artifact_proposer_previous_edits_binding` when proposer context and
  proposer LLM request logs are present;
- every non-initial previous edit must reference a real prior proposer/context
  round, bind its targeted mechanism hash to a held-in failure pattern from
  that prior round, and bind its edited surface hash to that prior round's
  editable surfaces;
- rejected and invalid previous edits must carry non-empty audit-decision
  reasons, while accepted edits may keep an empty reason;
- round zero still permits an empty previous-edits block, and reduced
  non-paper bundles can still omit both proposer artifacts;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P83 capture-manifest proposer-context evidence derivation diff is implemented:

- `capture_manifest_diff` emits `proposer-context-evidence-derivation` when
  the capture plan includes a fixed split and the realized bundle includes a
  proposer-context manifest;
- each realized proposer-context round's failure-pattern and passing-summary
  task-id union must cover exactly the planned held-in split task ids;
- the finding is skipped for reduced bundles without proposer context and does
  not replace bundle verification's stronger checks against evaluation and
  live-audit pass/fail outcomes;
- this remains offline plan-vs-realized evidence binding with no live Harbor,
  Docker, model, scanner, PyPI, Sigstore, registry, or cloud contact by
  default, and no benchmark reproduction claim is made.

P84 proposal-validation evidence binding is implemented:

- `proposal_validation_manifest` is now a strict live evidence artifact class
  derived from an audit directory after capture, not authored by the proposer;
- the manifest records baseline split outcomes, per-candidate split outcomes,
  evaluation-repeat metadata, changed surfaces, edited-surface hashes,
  targeted-mechanism hashes, summary hashes, committed proposal ids, merge
  decisions, audit decisions, and non-empty rejection reasons for rejected,
  superseded, or invalid candidates;
- `capture-extract`, the package CLI, and `capture-admit` accept
  `--audit-run-dir`/`audit_run_dir` so operator-captured audit directories can
  be transformed into this artifact shape;
- reproduction bundle verification emits
  `cross_artifact_proposal_validation_binding` to bind validation evidence to
  the fixed protocol hash, Self-Harness round count, proposal width, proposer
  attempted/committed counts, two-repeat evaluation metadata, and
  proposer-context previous attempted edits;
- capture-manifest diffing emits `proposal-validation-derivation` to compare
  realized validation structure against the planned validation shape;
- rehearsal bundles now carry 13 required artifact classes including
  `proposal_validation_manifest`;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P85 proposal-validation split-total binding is implemented:

- reproduction bundle verification now requires a canonical
  `live_terminal_bench_split_manifest` whenever `proposal_validation_manifest`
  is present;
- every proposal-validation round's `baseline_split_outcomes` and every
  candidate's `split_outcomes` must bind `held_in_total`/`held_out_total` to
  the fixed live split's held-in and held-out counts;
- verifier metadata records split counts, baseline total violations, candidate
  total violations, and a boundary note explaining the totals-only invariant;
- pass counts are deliberately not compared with the post-commit
  `live_two_repeat_evaluation_report`, because baseline and per-candidate
  validation outcomes describe different harness states from the final
  cumulative two-repeat evaluation;
- per-candidate raw trace binding and a separate baseline evaluation artifact
  remain future work and would require new live evidence shapes;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P86 proposal-validation acceptance-rule binding is implemented:

- reproduction bundle verification now checks every accepted or merged
  proposal-validation candidate against its own round's baseline split
  outcomes;
- accepted or merged candidates must improve at least one split and degrade
  neither split, using the aggregate pass counts recorded for the same
  `evaluation_repeats` validation context;
- verifier metadata records `acceptance_rule_violations` and an explicit
  boundary note that the comparison is round-baseline versus candidate, not
  candidate versus the final post-commit two-repeat evaluation;
- rejected, superseded, and invalid candidates remain exempt from the
  improvement requirement but still require audit decisions and rejection
  reasons from the strict artifact shape;
- planned capture manifests and synthetic reproduction fixtures now encode
  accepted candidates as actual non-regressing improvements rather than labels
  with unchanged pass counts;
- semantic parsing of free-text rejection reasons, per-task candidate outcome
  disclosure, per-candidate raw trace binding, and separate baseline evaluation
  artifacts remain future work;
- this remains offline evidence binding with no live Harbor, Docker, model,
  scanner, PyPI, Sigstore, registry, or cloud contact by default, and no
  benchmark reproduction claim is made.

P87 proposal-validation invalid-candidate categories are implemented:

- `proposal_validation_manifest` candidates now include nullable
  `validation_failure_category`, closed to the two paper Section 3.4 invalid
  causes: `no_editable_surface` and `execution_failure`;
- non-invalid candidates must keep `validation_failure_category:null` and
  non-empty `changed_surfaces`;
- invalid `no_editable_surface` candidates may be represented with
  `changed_surfaces:[]`, making the paper's "does not modify any editable
  surface" rejection path machine-checkable for the first time;
- capture extraction infers the category from legacy audit rows without
  parsing free text: invalid rows with no `changed_surfaces` or `surface`
  become `no_editable_surface`, while invalid rows with an attempted surface
  become `execution_failure`;
- reproduction bundle metadata records
  `validation_failure_category_violations`, and invalid candidates remain
  exempt from P86 acceptance-rule comparisons because they did not produce an
  accepted candidate harness state;
- planned capture manifests and synthetic reproduction fixtures now carry an
  invalid no-surface candidate alongside the accepted candidate in each
  validation round;
- out of scope: semantic parsing of `rejection_reason`, new artifact classes,
  proposal-validation schema-version bumps, raw per-candidate trace binding,
  live Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud contact, and
  any benchmark reproduction claim.

P88 proposal-validation category derivation diffing is implemented:

- `capture_manifest_diff` now compares per-round
  `validation_failure_category_counts` for planned versus realized
  proposal-validation evidence, using the closed buckets `none`,
  `no_editable_surface`, and `execution_failure`;
- `proposal-validation-derivation` also compares
  `changed_surfaces_empty_count`, so a planned no-surface invalid candidate
  must be realized with the same empty-surface shape rather than only the same
  candidate total;
- synthetic reproduction fixtures now realize the same one accepted plus one
  no-surface invalid candidate shape in every validation round declared by the
  planned capture manifest;
- this is a stricter plan-vs-realized rehearsal check only: it does not bump
  artifact schemas, add new artifact classes, contact live Harbor/Docker/model
  services, or introduce a benchmark reproduction claim.

P89 proposal-validation task-outcome disclosure is implemented:

- `proposal_validation_manifest` split outcomes may now include optional
  `task_outcomes` rows with task id, split, pass value, and optional attempt
  index;
- artifact shape validation requires those rows, when present, to use closed
  held-in/held-out splits, boolean pass values, non-duplicated
  task/split/attempt keys, and pass/total counts that reconcile with the
  aggregate split outcome;
- capture extraction populates task outcomes from operator-captured audit
  evaluation rows when they are present and omits the optional field for
  legacy total-only audit material;
- reproduction bundle verification now checks that proposal-validation
  baseline task outcomes, when bundled with proposer context, mark every
  proposer held-in failure-pattern task as a baseline held-in failure;
- capture-manifest diffing now compares per-round candidate
  `task_outcomes` presence counts so rehearsals can detect planned-vs-realized
  loss of this task-level evidence;
- out of scope: proposal-validation schema-version bumps, a per-task
  candidate-vs-baseline acceptance rule, raw trace binding, a separate
  baseline evaluation artifact class, semantic rejection-reason parsing, live
  Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud contact, and any
  benchmark reproduction claim.

P90 proposal-validation task-outcome content digest binding is implemented:

- capture-manifest diffing now computes deterministic content digests for
  proposal-validation baseline `task_outcomes` and per-candidate
  `task_outcomes`;
- each digest normalizes the closed P89 task-outcome shape of task id, split,
  pass value, and optional attempt index, with absent attempt index distinct
  from attempt index `0`;
- `proposal-validation-derivation` now compares planned versus realized
  baseline and candidate task-outcome digests, so a rehearsal can detect task
  identity/pass drift even when aggregate split counts and task-outcome
  presence counts still match;
- candidate digest comparison intentionally skips candidates whose realized
  task outcomes are absent, leaving that case to the existing presence-count
  drift signal rather than reporting duplicate causes;
- this is a rehearsal-only content-drift check for operator capture plans. It
  does not change the Section 3.4 aggregate pass-count promotion criterion,
  bundle acceptance-rule binding, artifact schema versions, or reproduction
  claim boundary;
- out of scope: a per-task candidate-vs-baseline acceptance rule, raw trace
  binding, a separate baseline evaluation artifact class, semantic
  rejection-reason parsing, live Harbor/Docker/model/scanner/PyPI/Sigstore/
  registry/cloud contact, and any benchmark reproduction claim.

P91 proposal-validation proposer-round traffic binding is implemented:

- `proposal_validation_manifest` rounds may now include optional
  `proposer_round_request_sha256` and `proposer_round_response_sha256` fields
  that bind a validation round to the shaped `proposer_llm_request_log` round
  that generated its candidate proposals;
- `capture-extract` accepts `--proposer-request-log-artifact` for
  `proposal_validation_manifest` extraction and stamps the two hashes from the
  validated proposer-log artifact, failing closed when a validation round has
  no matching proposer round;
- reproduction bundle verification now records
  `proposer_round_traffic_violations` and fails when declared validation
  traffic hashes drift from the proposer LLM request log;
- legacy validation manifests that omit the optional traffic hashes remain
  valid, preserving reduced and pre-P91 evidence bundles;
- `proposal-validation-derivation` metadata includes the task-outcome digest
  version, making the P90 task-outcome digest definition explicit for future
  shape extensions. P95 later bumps the active digest version to `2` when
  terminal failure categories enter the normalized content;
- this is opaque hash-level traffic binding only. It does not store raw
  prompts, raw responses, or raw traces; it does not alter the Section 3.4
  aggregate pass-count acceptance rule, artifact schema versions, or
  reproduction claim boundary;
- out of scope: per-candidate proposer response chunking, raw trace binding, a
  separate baseline evaluation artifact class, semantic rejection-reason
  parsing, live Harbor/Docker/model/scanner/PyPI/Sigstore/registry/cloud
  contact, and any benchmark reproduction claim.

P92 proposer-context intermediate-baseline task binding is implemented:

- reproduction bundle verification now derives proposer-context held-in
  failure-pattern and passing-summary task sets from each same-round
  `proposal_validation_manifest.baseline_split_outcomes.task_outcomes` block
  rather than from the final `live_two_repeat_evaluation_report`;
- this aligns the bundle check with the paper's round-local weakness-mining
  contract: the evidence bundle for round `t` is based on failures under the
  current harness state `h_t`, not the final post-commit harness state;
- when `proposer_context_manifest` is bundled, the matching proposal-validation
  baseline must disclose task outcomes for every proposer context round or the
  bundle fails closed;
- synthetic reproduction fixtures now model an intermediate improvement:
  round 0 has two held-in baseline failures while later rounds and the final
  evaluation have one, proving that context evidence is not silently bound to
  final evaluation outcomes;
- this does not change artifact schema versions or require candidate-level
  task-outcome binding. It does not contact live Harbor/Docker/model/scanner/
  PyPI/Sigstore/registry/cloud services or introduce a benchmark reproduction
  claim.

P93 proposal grounding binding to proposer context is implemented:

- reproduction bundle verification now checks every current
  `proposal_validation_manifest` candidate against the same-round
  `proposer_context_manifest` when proposer context is bundled;
- candidate `targeted_mechanism_sha256` values must match a held-in failure
  pattern mechanism hash from that round's proposer context;
- candidates with non-empty `changed_surfaces` must bind their
  `edited_surface_sha256` to an editable-surface hash from that same proposer
  context round, while `no_editable_surface` invalid candidates remain allowed
  to carry no surface binding;
- each proposal-validation round now fails closed when two candidates share the
  same `(targeted_mechanism_sha256, edited_surface_sha256)` signature, preventing
  duplicate candidate shapes from satisfying the paper's diverse-proposal
  contract;
- this does not parse free-text rationales, change the aggregate pass-count
  acceptance rule, bump artifact schemas, contact live infrastructure, or
  introduce a benchmark reproduction claim.

P94 proposal changed-surface name grounding is implemented:

- reproduction bundle verification now checks that every non-empty
  `changed_surfaces` name on a current `proposal_validation_manifest` candidate
  exists in the same-round `proposer_context_manifest.editable_surfaces` block;
- this closes the remaining gap where a candidate could carry an allowed
  `edited_surface_sha256` while naming a surface the proposer was not shown;
- capture-manifest diffing now compares planned and realized per-candidate
  changed-surface names, so rehearsal plans catch surface-name drift before the
  bundle verifier is the only enforcement point;
- faithful proposer-context logs should keep editable-surface hashes coherent
  with the capture-extract convention:
  `sha256(stable_json({"changed_surfaces":[surface.name]}) + "\n")`;
- this does not enforce single-surface minimality, parse free-text rationales,
  change the aggregate pass-count acceptance rule, bump artifact schemas,
  contact live infrastructure, or introduce a benchmark reproduction claim.

P95 terminal failure-category binding is implemented:

- `proposer_context_manifest.held_in_failure_patterns.patterns[]` may now
  disclose optional `failure_category` values from the closed verifier terminal
  failure vocabulary;
- `proposal_validation_manifest` task outcomes may now disclose optional
  `failure_category` values on failing rows, while passing rows must omit them
  or keep them null;
- capture extraction propagates failing task-row `failure_category` evidence
  from captured audit evaluations and omits `verifier-pass` markers from
  shaped passing task outcomes;
- reproduction bundle verification now records
  `failure_pattern_category_violations` inside
  `cross_artifact_proposer_context_evidence_binding` and fails when a
  same-round held-in failure cluster mixes baseline terminal categories or
  declares a category that disagrees with baseline task outcomes;
- capture-manifest diffing now compares planned versus realized proposer
  context cluster categories and bumps `task_outcomes_digest_version` to `2`
  so category-only task-outcome drift rotates the deterministic digest;
- this remains optional additive evidence for live captures. Older reduced
  bundles without terminal categories remain shape-valid, but production
  paper-fidelity bundles that disclose categories now get machine-checkable
  failure-signature binding.

P96 failure-signature causal-status binding is implemented:

- `proposer_context_manifest.held_in_failure_patterns.patterns[]` may now
  disclose optional `causal_status_sha256` values, computed as
  `sha256(stable_json({"causal_status": value}) + "\n")`, for the paper's
  `q` component in `phi(r)=(c,q,m)`;
- `previous_attempted_edits.edits[]` may now carry the same
  `causal_status_sha256` when referencing a prior round's targeted failure
  mechanism, so prior attempted edits bind to both the mechanism hash and the
  causal-status hash that shaped the proposal context;
- capture extraction accepts raw nested `causal_status` strings in proposer
  context logs, emits only the stable hash in shaped artifacts, and rejects
  malformed strings or mismatched supplied hashes;
- reproduction bundle verification records `causal_status_violations` inside
  `cross_artifact_proposer_previous_edits_binding` and fails when a declared
  previous-edit causal-status hash is missing from, or disagrees with, the
  referenced prior round's failure pattern;
- capture-manifest diffing now compares planned versus realized
  `causal_status_sha256` values per failure cluster while leaving
  `task_outcomes_digest_version` at `2`, because P96 is cluster-level evidence
  rather than a task-outcome row extension;
- this is optional, opaque-hash evidence. It does not introduce a closed
  causal-status vocabulary, expose raw traces, bump audit/corpus schemas,
  contact live infrastructure, or introduce a benchmark reproduction claim.

P97 failure-pattern symptom and verifier-evidence hashes are implemented:

- `proposer_context_manifest.held_in_failure_patterns.patterns[]` may now
  disclose optional `shared_symptoms_sha256` and `verifier_evidence_sha256`
  values for the paper Section 3.2 cluster evidence between representative
  failing tasks and inferred mechanisms;
- capture extraction accepts raw nested `shared_symptoms` and
  `verifier_evidence` values as either strings or string lists, emits only
  stable hashes computed as `sha256(stable_json({field: values}) + "\n")`,
  and rejects malformed values or mismatched supplied hashes;
- reproduction bundle verification records the new opaque hashes in
  `cross_artifact_proposer_context_evidence_binding` failure-pattern metadata
  and counts how many pattern rows disclose symptom and verifier-evidence
  hashes, without requiring reduced bundles to include them;
- capture-manifest diffing now compares planned versus realized
  `shared_symptoms_sha256` and `verifier_evidence_sha256` values per failure
  cluster, so capture rehearsals detect Section 3.2 evidence drift alongside
  failure categories and causal-status hashes;
- this remains optional, opaque-hash evidence. It does not store raw traces in
  shaped bundles, create a closed symptom vocabulary, add an artifact class,
  bump audit/corpus schemas, contact live infrastructure, or introduce a
  benchmark reproduction claim.

P98 failure-pattern presentation-order and actionability binding is
implemented:

- `proposer_context_manifest.held_in_failure_patterns.patterns[]` may now
  disclose optional `presentation_order` values and opaque
  `actionability_hint_sha256` values for the paper Section 3.2 requirement
  that weakness clusters be ordered by support and estimated actionability;
- artifact-shape validation requires `presentation_order` values to be
  all-or-none within a failure-pattern block and, when present, to form a
  contiguous permutation from zero;
- support ordering is derived rather than stored: larger `size` sorts first,
  while equal-size ties remain available for actionability-led ordering;
- capture extraction accepts raw nested `actionability_hint` strings in
  proposer context logs, emits only the stable hash, and rejects malformed
  strings or mismatched supplied hashes;
- reproduction bundle verification records presentation-order counts,
  actionability-hint hash counts, and ordering violations inside
  `cross_artifact_proposer_context_evidence_binding` metadata while preserving
  reduced bundles that omit the optional fields;
- capture-manifest diffing now compares planned versus realized
  `presentation_order` and `actionability_hint_sha256` values per failure
  cluster;
- this remains optional additive evidence. It does not add a closed
  actionability vocabulary, stored support-rank field, artifact class,
  audit/corpus schema bump, live infrastructure contact, or benchmark
  reproduction claim.

P99 accepted-candidate editable-surface distinctness is implemented:

- `cross_artifact_proposal_validation_binding` now records
  `accepted_merged_surface_sha256s` per validation round and fails with
  `merge_surface_conflict_violations` when two accepted or merged candidates
  target the same `edited_surface_sha256`;
- the invariant is keyed by surface hash, not free-text names, because P94
  already binds `edited_surface_sha256` to the deterministic changed-surface
  name set;
- rejected, superseded, and invalid candidates are exempt because they do not
  contribute to the merged harness;
- capture-manifest diffing compares planned versus realized
  `accepted_merged_surface_sha256s`, catching accepted-surface drift before
  bundle verification;
- this is a conservative offline check for the paper Algorithm 1
  `MERGEACCEPTED` compatibility step. It does not enforce single-surface
  minimality per candidate, parse raw edits, define a closed compatibility
  vocabulary, add an artifact class, contact live infrastructure, or introduce
  a benchmark reproduction claim.

P100 proposal-validation single-surface minimality is implemented:

- artifact-shape validation now rejects every non-`no_editable_surface`
  candidate whose `changed_surfaces` list is empty or contains more than one
  surface;
- invalid `no_editable_surface` candidates remain the only permitted empty
  `changed_surfaces` case;
- this turns the paper Section 3.3 one-proposal/one-surface minimal-edit
  requirement into a deterministic bundle gate instead of a prose-only audit
  expectation;
- capture-manifest diffing now records and compares
  `single_surface_violation_count` in `proposal-validation-derivation`, so
  planned captures catch multi-surface proposal drift before release evidence is
  bundled;
- this remains offline evidence binding. It does not inspect raw patches, add a
  closed patch vocabulary, add an artifact class, contact live infrastructure,
  or introduce a benchmark reproduction claim.

P101 failure-pattern signature distinctness is implemented:

- `proposer_context_manifest.held_in_failure_patterns.patterns[]` are now
  required to have pairwise-distinct
  `(failure_category, causal_status_sha256, mechanism_sha256)` signatures within
  each round;
- the invariant makes the paper Section 3.2 exact-match clustering definition
  machine-checkable at the artifact-shape boundary;
- nullable optional evidence remains compatible: reduced patterns with omitted
  `failure_category` or `causal_status_sha256` still validate, but duplicate
  reduced signatures fail closed;
- this does not derive `cluster_id`, require distinct shared-symptom or
  verifier-evidence hashes, add an artifact class, contact live infrastructure,
  or introduce a benchmark reproduction claim.

P102 failure-pattern support ordering is implemented:

- when `proposer_context_manifest.held_in_failure_patterns.patterns[]` declare
  `presentation_order`, artifact-shape validation now requires larger clusters
  to precede smaller clusters;
- equal-size clusters remain unconstrained so the paper Section 3.2
  actionability criterion can decide among ties without storing or exposing raw
  actionability text;
- the invariant extends P98's contiguous-permutation check into a support-aware
  partial order while still avoiding a stored `support_rank` field;
- this does not derive `cluster_id`, enforce equal-size tie ordering, add an
  artifact class, contact live infrastructure, or introduce a benchmark
  reproduction claim.

P103 failure-pattern task-id disjointness is implemented:

- `proposer_context_manifest.held_in_failure_patterns.patterns[]` must now
  carry pairwise-disjoint `task_ids` within each proposer-context round,
  matching Section 3.2 exact-match clustering where each failed task maps to one
  failure signature and one cluster;
- artifact-shape validation rejects overlapping cluster task ids with a
  task/cluster-specific diagnostic;
- reproduction bundle verification records
  `failure_pattern_task_overlap_violations` inside
  `cross_artifact_proposer_context_evidence_binding` and fails when overlaps
  survive shape validation bypasses or future ingestion paths;
- capture-manifest diffing now compares `failure_pattern_task_overlap_count`,
  so rehearsed capture plans catch overlapping realized context before bundle
  validation;
- this does not enforce inter-round cluster stability, constrain passing
  summaries, add an artifact class, contact live infrastructure, or introduce a
  benchmark reproduction claim.

P104 previous-attempted-edit distinctness is implemented:

- `proposer_context_manifest.previous_attempted_edits.edits[]` must now carry
  pairwise-distinct `(proposal_round_index, targeted_mechanism_sha256,
  edited_surface_sha256)` signatures within each proposer-context round,
  matching Section 3.3's bounded context as a concise set of previous attempted
  edits rather than duplicated rows;
- artifact-shape validation rejects duplicate previous attempted edit
  signatures with the repeated row and first-seen row identified;
- reproduction bundle verification records
  `previous_edit_duplicate_violations` inside
  `cross_artifact_proposer_previous_edits_binding` and fails when duplicates
  survive shape validation bypasses or future ingestion paths;
- capture-manifest diffing now compares
  `previous_attempted_edit_signature_duplicate_count`, so rehearsed capture
  plans catch duplicated realized previous-edit summaries before bundle
  validation;
- this does not require additional previous edits, enforce cross-round edit
  stability, add an artifact class, contact live infrastructure, or introduce a
  benchmark reproduction claim.

P105 editable-surface distinctness is implemented:

- `proposer_context_manifest.editable_surfaces.surfaces[]` must now carry
  pairwise-distinct `sha256` values within each proposer-context round, matching
  Section 3.3's bounded context as a set of distinct harness configuration
  points the proposer can modify;
- artifact-shape validation rejects duplicate editable-surface hashes with the
  repeated row and first-seen row identified;
- reproduction bundle verification records
  `editable_surface_duplicate_violations` inside
  `cross_artifact_proposer_context_binding` and fails when duplicates survive
  shape validation bypasses or future ingestion paths;
- capture-manifest diffing now compares `editable_surface_duplicate_count`, so
  rehearsed capture plans catch duplicated realized editable surfaces before
  bundle validation;
- this does not close the editable-surface `kind` vocabulary, require
  cross-round surface stability, require every editable surface to be targeted,
  add an artifact class, contact live infrastructure, or introduce a benchmark
  reproduction claim.

P106 proposal-validation evaluation repeat consistency is implemented:

- each `proposal_validation_manifest` round now requires every candidate
  `split_outcomes.evaluation_repeats` value to match the same round's
  `baseline_split_outcomes.evaluation_repeats`, keeping Section 3.4 aggregate
  pass-count comparisons over comparable repeat counts;
- artifact-shape validation rejects mismatched baseline/candidate repeat
  metadata before a malformed validation manifest can pass;
- reproduction bundle verification records
  `evaluation_repeats_mismatch_violations` inside
  `cross_artifact_proposal_validation_binding` and fails when mismatches
  survive shape validation bypasses or future ingestion paths;
- this does not enforce cross-round repeat stability, compare validation pass
  counts with the final post-commit evaluation, add an artifact class, contact
  live infrastructure, or introduce a benchmark reproduction claim.

P108 proposal-validation harness-state hash continuity is implemented:

- proposal-validation rounds may now carry optional paired
  `harness_before_sha256` and `harness_after_sha256` fields, derived from audit
  `lineage.json` when `capture-extract` builds the live evidence artifact;
- once any validation round declares harness hashes, bundle verification
  requires complete adjacent hash evidence and checks no-op and single-commit
  transitions in harness-state hash space: no-op rounds keep the previous
  baseline harness hash, and single-commit rounds use the previous round's
  committed after-state hash as the next round's before-state hash;
- multi-commit transitions are recorded in
  `harness_continuity_skipped_rounds`, matching the P107 split-outcome rule
  because the merged harness state is not represented by one candidate row;
- capture-manifest diffing now records `harness_hash_presence_count` in
  `proposal-validation-derivation`, so rehearsal plans catch planned-versus-
  realized loss of this lineage evidence;
- this tightens Algorithm 1 `MERGEACCEPTED` evidence without adding a new
  artifact class, requiring raw harness snapshots in reproduction bundles,
  recomputing hashes from snapshots, contacting live services, or introducing a
  benchmark reproduction claim.

P109 multi-commit `MERGEACCEPTED` harness-state hash continuity is implemented:

- proposal-validation rounds may now carry optional
  `harness_after_merged_sha256` for rounds that commit two or more proposals;
- new-style multi-commit rounds that already declare harness hashes must also
  declare this merged hash, and the shape validator rejects the field on no-op
  or single-commit rounds;
- `capture-extract` derives the merged hash from audit lineage
  `harness_after_hash`, preserving the P108 boundary that raw patches and raw
  harness snapshots are not bundled or recomputed;
- reproduction bundle verification uses the merged hash as the expected next
  round `harness_before_sha256`, closing the previous multi-commit skip in
  harness-hash space while leaving split-outcome multi-commit lineage
  explicitly skipped;
- capture-manifest diffing compares merged-hash presence/value and a
  `multi_commit_merged_hash_violation_count`, so plan-vs-realized rehearsal
  evidence catches loss of multi-commit merged-hash binding;
- legacy reduced manifests that omit all harness hashes remain valid; this does
  not add an artifact class, contact live services, or introduce a benchmark
  reproduction claim.

P110 multi-commit split-outcome lineage continuity was initially blocked under
incomplete context:

- GLM convergence returned `CONVERGED: BLOCKED` for an optional
  `merged_split_outcomes` field when only capture-extract and bundle verifier
  context was provided;
- stamping such a field from the next round's `baseline_split_outcomes` would
  only prove internal manifest consistency, not a separate verifier observation
  of the merged harness state;
- no artifact schemas, canonical audit hashes, release gates, or reproduction
  claim semantics changed for this blocked slice.

P111 multi-commit `MERGEACCEPTED` split-outcome lineage continuity is
implemented using existing engine evidence:

- the engine already writes independent merged-harness evaluation rows as
  `proposal_id:"__merge__"`, `arm:"candidate"` when it composes compatible
  accepted edits;
- `proposal_validation_manifest/1.0` rounds may now carry
  `merged_split_outcomes`, using the same split-outcome shape as baseline and
  candidate rows;
- new-style multi-commit rounds that declare harness hashes must declare
  `merged_split_outcomes`, and `capture-extract` derives the field only from
  the existing `__merge__` audit rows, failing closed if they are missing;
- `cross_artifact_proposal_validation_binding` uses
  `merged_split_outcomes` as the expected next-round baseline, closing the
  P107 multi-commit split-outcome skip when independent merged-evaluation
  evidence exists;
- reduced legacy manifests that omit harness hashes and merged split outcomes
  still pass with an explicit `missing_merged_split_outcomes` skip;
- `capture_manifest_diff` compares merged split-outcome presence and digest so
  rehearsal plans catch loss or drift of this paper-fidelity evidence;
- this changes only derived evidence extraction/validation, not canonical audit
  output, live-service contact, or reproduction-claim semantics.

Package metadata hardening is implemented:

- the package root now exports `self_harness.__version__`, resolved from
  installed package metadata with a source-tree fallback matching
  `pyproject.toml`;
- `tests/test_package_metadata.py` asserts the source fallback stays aligned
  with the project version;
- isolated wheel/sdist builds and a fresh offline wheel install now prove the
  installed CLI and root package version surface are usable from distribution
  artifacts.

P107 proposal-validation lineage continuity is implemented:

- `cross_artifact_proposal_validation_binding` now checks that each
  machine-checkable validation round baseline follows the previous round's
  committed state, matching Algorithm 1's `MERGEACCEPTED` state transition;
- when the previous round committed no proposals, the next baseline must match
  the previous baseline; when it committed exactly one proposal, the next
  baseline must match that candidate's split outcomes;
- multiple committed proposals are explicitly recorded in
  `lineage_continuity_skipped_rounds` because the merged harness state is not
  represented by a single candidate row;
- synthetic capture and reproduction fixtures now carry a monotonic
  proposal-validation lineage instead of resetting held-in failures between
  rounds;
- this does not inspect raw patches, compare validation pass counts with the
  final post-commit evaluation, add an artifact class, contact live
  infrastructure, or introduce a benchmark reproduction claim.

P112 adds a package supply-chain reproducibility gate:

- `scripts/verify_reproducible_build.py` writes a deterministic
  `reproducible_build/1.0` report for the release source distribution and
  wheel;
- `make build` now uses the project `SOURCE_DATE_EPOCH` and no build isolation,
  making release artifact generation explicit about its build backend
  dependencies;
- `make reproducible-build-check` rebuilds the wheel from the source
  distribution without dependency resolution and fails when the rebuilt wheel
  filename or SHA-256 differs from the wheel in `dist/`;
- `.github/workflows/ci.yml` runs the gate across Python 3.11, 3.12, and 3.13,
  while `.github/workflows/release.yml` runs it before later release evidence
  and publishing steps;
- `scripts/release_candidate_evidence.py` consumes
  `dist/self-harness-reproducible-build.json` as a required gate and records
  its deterministic `report_hash`;
- this does not contact PyPI/TestPyPI, run Sigstore, validate trusted
  publishing, contact live infrastructure, or introduce a benchmark
  reproduction claim.

P113 external-evidence convergence is blocked, not locally incomplete:

- GLM convergence reviewed the post-P112 state and returned
  `CONVERGED: BLOCKED` because remaining paper-faithful progress requires live
  operator evidence rather than another local code or fixture slice;
- current package release evidence remains ready through the default
  non-reproduction release-candidate path, including the reproducible-build
  gate;
- benchmark reproduction readiness remains intentionally false until operators
  provision a live Harbor/Docker environment, the paper model backends
  MiniMax M2.5, Qwen3.5-35B-A3B, and GLM-5, live proposer/evaluation/audit
  captures, and hard-path PyPI/Sigstore release evidence;
- the next implementation slice should be scoped only after a concrete live
  evidence class is supplied, then routed through capture-extract,
  capture-admit, reproduction-bundle verification, reproduction-readiness, and
  `release-candidate-evidence-reproduction`;
- no new local artifact, fixture, or documentation-only slice should be treated
  as benchmark-reproduction progress while these external dependencies remain
  blocked.

Remaining production work is now deeper integration work: real benchmark
execution on a provisioned Harbor/Docker host, provider-specific KMS/HSM or
hardware-token wrapper scripts, provider-specific registry/OAuth/secret-manager
helpers, live Harbor discovery validation, real paper-backend model preflights,
real Sigstore/PyPI publishing and operator validation with actual signing
material, scanner database mirror credentials beyond path-based Trivy config
wiring, live policy promotion rollout with real operator-owned files, CI
invocation of real scanners against real images, and future concrete
major-version transform implementations when a new breaking audit schema is
introduced.

## Original State

The project is a paper-faithful toy implementation of the Self-Harness
algorithmic protocol. It has:

- typed dataclasses for tasks, traces, harness specs, patches, proposals,
  proposer context, evaluation results, and lineage;
- deterministic toy runner and heuristic proposer;
- held-in/held-out validation with repeated attempts and aggregate pass counts;
- bounded patch DSL over editable harness surfaces;
- deterministic JSON/JSONL audit artifacts;
- pytest coverage for mining, acceptance, patch reversal, proposer isolation,
  deterministic demo output, and enriched audit fields.

The project does not yet reproduce the paper's Terminal-Bench-2.0 experiments.

## Production Goal

Move the project from a research toy into a production-ready Python package
foundation without prematurely pretending to be a Terminal-Bench reproduction.

"Production-ready" for this step means:

- stable package metadata and Python version support;
- explicit runtime configuration instead of loosely threaded keyword arguments;
- robust error boundaries and invalid candidate handling;
- CLI options for production-relevant controls;
- deterministic audit artifacts with a clear protocol version;
- CI-quality local checks: tests, lint, formatting, typing;
- documentation that separates the package API, CLI demo, limitations, and next
  integration seams;
- a small but clean architecture that can accept real runners and LLM proposers.

## Proposed Implementation Slice

P0:

1. Add `self_harness/config.py` with immutable `EngineConfig` and validation for
   `rounds`, `evaluation_repeats`, proposal budget, and protocol/model metadata.
2. Add `self_harness/exceptions.py` with project-specific exceptions for invalid
   config, invalid patch/proposal, and evaluation failure.
3. Update `SelfHarnessEngine` to accept `EngineConfig`, keep backward-compatible
   constructor kwargs where useful, and write manifest metadata from config.
4. Expand CLI:
   - `--rounds`
   - `--seed`
   - `--out`
   - `--evaluation-repeats`
   - `--max-proposals`
   - `--max-payload-bytes`
   - `--fail-on-empty`
5. Add production tooling:
   - pyproject dev deps: `pytest`, `ruff`, `mypy`, `build`;
   - ruff config;
   - mypy config;
   - `.github/workflows/ci.yml`;
   - Makefile or equivalent task commands.
6. Add tests for config validation, CLI argument wiring, and invalid proposal
   audit behavior.
7. Update README with install/dev commands and production status.

Later slices, now implemented:

- Add source adapters: subprocess runner, filesystem task loader, LLM proposer.
- Add audit readback/summary API.
- Add richer editable surfaces for tools, skills, memory, and subagents.
- Add release automation and versioned artifact schema migration policy.

## Questions For GLM

- Is this P0 scope sufficient for "production project foundation" without
  overreaching?
- Should we move to `src/` layout now or keep the current package layout until
  the public API stabilizes?
- Are there production risks in adding mypy/ruff before the code is fully
  shaped?
- Which API boundaries should be considered stable now?
