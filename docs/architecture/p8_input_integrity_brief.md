# P8 Input Integrity Brief

## Status

Implemented after GLM convergence in
`docs/architecture/glm_p8_provenance_matrix_plan.md` and
`docs/architecture/glm_p8_provenance_matrix_convergence.md`.

P8 hardens the production boundary around input provenance without claiming a
Terminal-Bench reproduction.

## Implemented

- Optional Ed25519 corpus signature verification through
  `load_corpus(..., verify_signature_key=...)`.
- `self-harness validate-tasks --require-corpus-signature <public-key>` and
  `self-harness local-demo --require-corpus-signature <public-key>`.
- Structured `invalid-signature` task-load failures for missing, malformed, or
  mismatched signatures.
- Stable corpus integrity payload shared by checksums and signatures. The
  payload covers `corpus_version`, `corpus_id`, and `tasks`; `checksum` and
  `signature` are metadata outside the signed payload.
- Captured Terminal-Bench dry-run fixtures now include `task_source_hash`.
- `HarborRunner` refuses captured fixture replay when the current manifest task
  hash does not match the captured fixture hash.
- GitHub Actions CI now runs on Python 3.11, 3.12, and 3.13.
- Provenance verification is packaged as the optional `provenance` extra.

## Remaining Boundary

These changes make local corpora and captured fixtures harder to misuse, but
they still do not prove the paper's Terminal-Bench-2.0 benchmark result. A
paper-style reproduction still requires a provisioned Harbor/Docker host, the
intended benchmark corpus, live execution, and aggregate benchmark reporting.
