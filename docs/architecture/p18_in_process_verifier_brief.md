# P18 Trusted In-Process Verifier Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p18_structured_verifier_plan.md`.

## Purpose

P2 added shell-backed local subprocess tasks. P18 adds a richer non-subprocess
verifier path for operators who can provide trusted Python code and want
structured verifier outcomes without parsing stdout or shell exit codes.

This is not a benchmark reproduction and does not execute Python named by task
corpus JSON. The operator must supply the trusted verifier module path or
dotted module name explicitly.

## Implemented

- `InProcessPythonTaskAdapter` and `InProcessPythonRunner`.
- `VerifierResult` with `passed`, `failure_category`, `mechanism`, and
  `message` fields.
- Fresh per-attempt work directories with optional `workspace_template` copying.
- Optional setup hook executed before verification.
- Closed mapping through `FailureCategory`; unknown categories raise
  `InProcessVerifierError` instead of silently changing audit semantics.
- Exceptions from setup or verification become environment-error verifier
  outcomes.
- `python-demo CORPUS --trust-verifier-module PATH` CLI with the existing corpus
  signature/keyring gates.
- Canonical in-process verifier audit hash fixture in
  `tests/fixtures/canonical_python_audit_hash.txt`.

## Trust Boundary

Corpus metadata may include `verifier_selector`, but that value is opaque to the
harness. The harness validates only that it is a short non-empty string. Only
the operator-supplied trusted module chooses executable Python behavior.

## Deferred

- Async verifier protocols.
- Network or HTTP verifier integrations.
- Container-backed verifier integrations.
- Trusted verifier registries or plugin discovery.
- Benchmark reproduction claims.

## Schema

No audit schema change. Workdir paths and hook traces remain in memory on
`RunRecord.trace` and are not written to audit JSONL.
