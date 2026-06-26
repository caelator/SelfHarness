CONVERGED: YES

## Verdict
The P22 scope is well-bounded and implementable against the existing P19/P20 seams using stdlib primitives. The codebase already enforces the corpus-metadata trust boundary, and audit JSONL is already known to exclude `RunRecord.trace` transport details. The remaining work is a contained hardening pass: add an operator-only TLS/material surface for the HTTP verifier, switch the container verifier off `-e KEY=VALUE` argv injection toward file/env-file based secret carriage, and add guardrails + tests proving non-leakage. No external decisions are required.

## Critique
Evidence (from supplied files):
- `http_verifier.py` uses `urllib.request.urlopen(request, timeout=...)` with no `context` parameter. mTLS / custom CA support is absent but trivially added via `ssl.SSLContext` passed to `urlopen`.
- `container_verifier.py` `build_container_run_command` emits `-e KEY=VALUE` for each `extra_env` entry. This places operator secrets in `docker run` argv (visible in `ps`, container `inspect`, and currently written into the `container-command` `TraceEvent.metadata["argv"]` in-memory). This is a real leak risk and the central defect to close in P22.
- `RunRecord.trace` for both adapters is in-memory only; brief docs state trace transport details are not written to audit JSONL. Therefore audit JSONL is currently safe, but the in-memory trace argv for containers is a leak vector for future derived artifacts and should be redacted.
- Corpus guardrails already forbid URLs (`DISALLOWED_URL_METADATA_KEYS`) and image/command/entrypoint/args (`DISALLOWED_CONTAINER_METADATA_KEYS`). P22 must extend these to cover registry credentials, TLS material, and secret headers.
- CLI parsing for container `--env KEY=VALUE` is the only path by which operators can pass env today; no `--env-file`, no registry-config path.

Inferences:
- Because P19 brief explicitly defers mTLS and P20 brief explicitly defers registry auth, P22 is the designated home for these and no schema/protocol change is implied.
- Switching container env injection from argv to `--env-file` (temp file, 0600, cleaned up) preserves the deterministic dry-run path (env file contents are not part of the canonical command spec for fixture replay) while removing secrets from argv.

## Required Changes
1. Extend corpus metadata guardrails (both HTTP and container) to reject: `ca_bundle`, `client_cert`, `client_key`, `tls_*`, `registry_*`, `docker_config`, `auth_*`, `secret_*`, and any header-shaped keys (`*_header`, `*_headers`).
2. HTTP verifier: add operator-only `ssl_context` constructed from `ca_bundle`, `client_cert`, `client_key` (all optional). Build via `ssl.create_default_context(cafile=...)` plus `load_cert_chain`. Pass to `urlopen(..., context=ssl_context)`. Never write these to `RunRecord.trace` or audit.
3. Container verifier: remove `-e KEY=VALUE` argv emission for `extra_env`. Replace with `--env-file <tempfile>` (path with 0600 perms, written under the per-attempt workdir, unlinked in `finally`). The in-memory `TraceEvent` for the container command must store a redacted argv (`-e` entries removed, `--env-file` path redacted to a label) so derived inspection reports cannot leak secrets.
4. Add an operator-only `docker_config_dir`/`docker_config_path` surface to the container adapter; when set, invoke docker with `DOCKER_CONFIG=<dir>` environment on the docker *parent* process (not inside the container). Do not place it in the container argv.
5. CLI additions:
   - `http-demo`: `--tls-ca-bundle PATH`, `--tls-client-cert PATH`, `--tls-client-key PATH` (mutually constrained: cert requires key).
   - `container-demo`: `--env-file PATH` (repeatable, file-based secret carriage), `--docker-config PATH` (operator-supplied registry config dir/file). `--env` is retained for non-secret passthrough but documented as not for secrets; long-term it can be deprecated. For P22, keep `--env` but do not log its values.
6. Tests (local-only, no registry/cloud/network):
   - HTTP mTLS handshake against a local `127.0.0.1` `ssl.SSLContext`-enabled server using self-signed CA/client cert fixtures generated in-test via `cryptography` is **out of scope** if `cryptography` is not already a dev dep; instead, generate fixtures with stdlib `ssl` + an in-repo precomputed PEM pair under `tests/fixtures/tls/` and skip the test gracefully if the pair is absent. Validate that the request succeeds with the supplied context and fails without it.
   - HTTP corpus metadata rejection of TLS/secret/header keys.
   - Container dry-run still produces the canonical audit hash (env-file must not perturb dry-run determinism).
   - Container live-mode fake-docker test asserting the argv contains `--env-file <redacted>` and never contains `KEY=VALUE` secret values; assert the env-file content reaches the fake docker via the file.
   - Container `DOCKER_CONFIG` passthrough assertion via fake-docker that records its parent env.
   - Redaction assertion: `RunRecord.trace` container-command event metadata does not contain any operator-supplied secret material.
   - Corpus metadata rejection of `docker_config`, `registry_*`, `auth_*`, `tls_*`.
7. Docs: add `docs/architecture/p22_auth_mtls_brief.md` mirroring the P19/P20 brief format, listing non-goals (no OAuth refresh, no secret-manager, no KMS/HSM, no benchmark reproduction, no schema change). Update `productionization_brief.md` status list. README must warn that `--env` is not for secrets.

## Revised Plan
**Files to add/modify**
- `src/self_harness/adapters/http_verifier.py`
  - Add `tls_ca_bundle`, `tls_client_cert`, `tls_client_key` to `HttpVerifierTaskAdapter`/`Runner`.
  - Add `_build_ssl_context(ca_bundle, client_cert, client_key) -> ssl.SSLContext | None`.
  - Pass `context=` to `urlopen`.
  - Extend `DISALLOWED_URL_METADATA_KEYS` with TLS/secret/header keys.
- `src/self_harness/adapters/container_verifier.py`
  - Replace `-e KEY=VALUE` emission in `build_container_run_command` with `--env-file` semantics; introduce a redacted command form for tracing.
  - Add `docker_config_dir` field; when set, runner invokes docker with `env={"DOCKER_CONFIG": ...}` on the parent process.
  - Extend `DISALLOWED_CONTAINER_METADATA_KEYS` with registry/auth/tls/docker_config keys.
  - Update `ContainerCommandSpec` to carry `env_file: Path | None` instead of (or in addition to) inline env; ensure dry-run determinism (env_file excluded from hash-affecting spec equality for fixture replay).
- `src/self_harness/cli.py`
  - Add TLS flags to `http-demo`; wire to adapter.
  - Add `--env-file` (repeatable) and `--docker-config` to `container-demo`; deprecate/documented-warning on `--env`.
  - Ensure no new audit JSONL fields.
- `tests/fixtures/tls/` — checked-in CA/client cert/key pair for the local mTLS test (generated offline; gitignored if repo policy forbids, in which case test is skipped).
- `tests/test_http_verifier_tls.py` — local mTLS + corpus metadata rejection.
- `tests/test_container_verifier_auth.py` — env-file argv hygiene, DOCKER_CONFIG parent-env passthrough, redaction, corpus metadata rejection, dry-run determinism preserved.
- `docs/architecture/p22_auth_mtls_brief.md` and update `productionization_brief.md`.

**Acceptance gates**
- `make check`, `make readiness`, and canonical audit hash fixtures for both HTTP and container dry-run paths remain byte-stable.
- No operator-supplied secret material appears in: audit JSONL, manifest JSON, `RunRecord.trace` JSON-serialized form, container `docker run` argv (only `--env-file <redacted>`), or CLI `--help` examples.
- Corpus metadata rejection tests pass for the extended key set.
- Local mTLS handshake test passes when fixture material is present; otherwise skipped (not failed).
- Dry-run path remains daemon-free and deterministic (env-file never written in dry-run mode).

**Non-goals**
- OAuth/OIDC, bearer-token refresh, secret-manager/KMS/HSM integrations.
- Async verifier protocols, retry/backoff.
- Image vulnerability scanning or registry policy enforcement.
- Any audit schema change.
- Any Terminal-Bench/Harbor reproduction claim.
- Real registry or cloud credential tests.

## Remaining Open Questions
None blocking. Two follow-ups for future slices only:
- Whether to deprecate `--env KEY=VALUE` entirely in P23+ (preferred) or keep as non-secret passthrough.
- Whether to ship a checked-in TLS fixture pair or document an offline generation script; both are acceptable for P22 and do not affect convergence.
