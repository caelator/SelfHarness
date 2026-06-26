# P19 Trusted HTTP Verifier Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p19_http_verifier_plan.md`.

## Purpose

P18 added trusted in-process Python verifiers. P19 adds a stdlib-only HTTP
verifier boundary for operators who need service-backed structured verification
without adding runtime dependencies or claiming benchmark reproduction.

## Implemented

- `HttpVerifierTaskAdapter` and `HttpVerifierRunner`.
- `http-demo CORPUS --trust-verifier-url URL` CLI with existing corpus
  signature/keyring gates.
- Deterministic JSON POST request body with `task_id`, `split`,
  `attempt_index`, `verifier_selector`, `workdir`, and opaque `task_metadata`.
- Strict response validation using the shared structured verifier result
  contract.
- Closed `FailureCategory` mapping, with unknown categories failing closed.
- Timeout and HTTP status failure mapping to verifier outcomes.
- Canonical HTTP verifier audit hash fixture in
  `tests/fixtures/canonical_http_audit_hash.txt`.

## Trust Boundary

Only the operator-supplied `--trust-verifier-url` chooses the HTTP endpoint.
Corpus metadata may include `verifier_selector`, but it may not include URL or
endpoint fields. Tests bind a local `127.0.0.1` server on an ephemeral port and
do not require external network access.

## Deferred

- Container-backed verifier adapters.
- OAuth, bearer-token refresh, and secret-manager integrations.
- Retry/backoff policies.
- Async verifier protocols.
- Benchmark reproduction claims.

## Schema

No audit schema change. HTTP request details and workdir paths remain in memory
on `RunRecord.trace` and are not written to audit JSONL.
