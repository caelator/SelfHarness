CONVERGED: YES

## Verdict
The proposed P35 slice is ready to implement. It is local-only, aligned with the brief’s remaining production work, and does not require live Harbor/Docker/registry/cloud/Sigstore/PyPI/real Trivy. The two open questions are non-blocking and can be resolved as implementation decisions rather than user decisions: synthesize an older-schema fixture if none exists, and support upgrade-only migration. Material architecture risks can be mitigated with explicit copy semantics, release/operator boundary docs, and redaction rules.

## Critique
- Evidence: `RELEASE.md` explicitly requires a migration shim before a breaking schema change can become the default writer version, so an audit migration framework is a stated production readiness gap.
- Evidence: the productionization brief records P21 as implementing the external signer protocol, but the repository context does not show a reference signer script; adding one validates the boundary locally.
- Evidence: P33/P34 added scanner DB update orchestration and Harbor discovery, while `scripts/scanner_db_update.py` and the Makefile show the existing dry-run/replay surface. A structured `--db-registry-config` path is a natural local-hardening addition.
- Inference: historical schema `1.0` fixtures may not exist in the test tree. This is not blocking; the slice can synthesize a minimal older-schema fixture for migration tests.
- Inference: downgrade support is not required by the release policy and would imply data loss. Upgrade-only migration is sufficient for the major-version shim obligation.

## Required Changes
- Resolve the open questions in-plan:
  - If no older audit fixture exists, synthesize a minimal `1.0` or `1.1` fixture in this slice and document it as synthetic.
  - Implement upgrade-only migration to the current default writer version. Reject downgrade requests with a structured error.
- Add explicit release/operator boundary language to docs:
  - `audit-migrate` operates on a copied audit tree, never mutates the source, and is release/operator tooling. It must not be used to rewrite historical audit evidence in place.
  - Migration does not change the canonical readiness hash fixture; it is not a benchmark reproduction path.
  - The example external signer is local-only reference material, must not be used with production KMS/HSM material, and is not installed as a supported production signer.
  - Scanner DB registry config files are operator-owned secrets; their contents must never be logged, echoed in command traces, or written to release artifacts.
- Ensure command construction uses absolute paths for `--db-registry-config` and fails closed when the file is missing in live mode. Dry-run mode may validate path construction without requiring the file.
- Ensure migration recomputes canonical hashes using existing audit hashing helpers whenever structural bytes change, and validates/updates metadata hashes for version-only changes.
- Add CI gates through existing offline make targets and tests, not through live scanner/registry execution.

## Revised Plan
**Slice P35: Audit Migration and Operator Trust Boundary Templates**

1. Audit migration core
   - Add `src/self_harness/audit_migration.py`.
   - Implement `migrate_audit_tree(src_dir, dest_dir, target_version)` with copy-first semantics.
   - Provide an upgrade-only migrator registry, initially supporting forward migration to the current default writer version.
   - Fail closed for unknown schema versions, downgrade requests, malformed manifests, or hash mismatches after migration.
   - Recompute or validate canonical hashes using the existing audit hashing path.
2. Migration CLI
   - Add `self-harness audit-migrate`.
   - Require explicit source and destination directories.
   - Emit a structured JSON migration report as release/operator material.
3. Reference external signer
   - Add `scripts/example_external_signer.py`.
   - Implement the P21 stdin/stdout JSON contract exactly.
   - Read a PEM private key path from an environment variable.
   - Fail closed on missing key, malformed request, or signing failure.
   - Document as local-only reference material.
4. Scanner DB auth file support
   - Extend `ScannerDbUpdateCommand` and related scanner orchestration dataclasses with optional `db_registry_config_path`.
   - Map the path to Trivy `--registry-config <absolute_path>`.
   - Update `scripts/scanner_db_update.py` and scanner command construction to accept `--db-registry-config`.
   - Redact registry config file contents from all logs, JSON reports, and command traces.
5. Tests and gates
   - Add migration tests using either an existing older-schema fixture or a synthesized minimal fixture; include forward migration, already-current rejection, downgrade rejection, malformed manifest rejection, and hash behavior.
   - Add a fixture-based test for `example_external_signer.py` through the `corpus-sign --external-signer` boundary.
   - Add tests for scanner DB update command construction with and without `--db-registry-config`, including absolute path handling and absence of secret content in output.
   - Extend `make scanner-check` to dry-run the DB auth config path.
   - Add docs under `docs/operations/` for audit migration, the example external signer, and scanner DB registry config handling.
6. Boundary documentation
   - Update `RELEASE.md` and operations docs with explicit release/operator boundary language for migration, reference signer, and scanner DB auth.

## Remaining Open Questions
None blocking. Implementation should proceed with:
- synthetic older-schema fixture if no real historical fixture exists;
- upgrade-only migration;
- absolute path enforcement and redaction for scanner DB registry config files.
