# P5 Readiness Gate And Release Automation Brief

## Status

GLM convergence completed in
`docs/architecture/glm_p5_fidelity_gate_plan.md`. P5 is implemented.

Implemented:

- `make readiness` target;
- paper-fidelity invariant tests in `tests/invariants/`;
- explicit `PaperFidelityError` for held-out proposer-context leakage;
- stable audit-tree hash helper;
- canonical audit hash fixture;
- schema changelog parity check;
- ambient-environment determinism check;
- README naming guardrail outside the explicit limitations section;
- CI wiring for `make check` and `make readiness`;
- tag-driven release workflow with RC dry-runs;
- `RELEASE.md` release and hash-rotation policy;
- package metadata classifiers and release optional dependencies.

## Production Meaning

The project can now machine-check the paper-aligned protocol invariants it
claims locally:

- held-out traces are not exposed to the proposer context;
- failure mining clusters exact verifier-grounded signatures;
- harness edits are constrained to whitelisted surfaces;
- repeated evaluation aggregates pass counts;
- acceptance rejects ties and split regressions;
- lineage hashes match committed harness snapshots;
- supported audit schema versions match the schema changelog;
- audit bytes are deterministic under ambient environment changes.

## Remaining Limits

This is still not a benchmark reproduction. P5 does not add real benchmark
adapters, provider SDKs, parallel evaluation, corpus signing, or a cross-Python
canonical-hash matrix. Those remain future production-hardening slices.
