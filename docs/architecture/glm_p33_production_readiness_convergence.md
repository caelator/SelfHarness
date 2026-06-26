# Round 1

CONVERGED: YES
## Verdict
The architecture is ready to execute the next slice, **P33 Scanner DB Update Orchestration**. This slice completes the offline lifecycle for scanner database operations (adding update orchestration to the existing freshness validation from P32) while strictly adhering to the no-live-network and no-real-Trivy constraints. It provides high operational value by allowing CI to deterministically verify the exact commands operators must run for scanner DB maintenance.

## Critique
The remaining productionization items are heavily network/cloud-dependent (live Harbor, cloud KMS, real Trivy, Sigstore, PyPI). The scanner DB workflow has been carefully built offline up to P32, but lacks the corresponding command generation for *updating* the database. Operators currently have to guess or manually construct the correct Trivy DB update command that aligns with the cache directory validated by P32. Implementing a deterministic, dry-run testable orchestration layer for this update command is the most logical and highest-value offline step available.

## Required Changes
None. The slice is locally implementable, testable, and preserves all existing paper-fidelity invariants and schemas.

## Revised Plan
**Slice: P33 Scanner DB Update Orchestration**

1. **Schema Boundaries**:
   - Introduce `ScannerDbUpdateResult` schema version `1.0` in `scanner_db_update.py`.
   - No changes to audit schemas, corpus schemas, or readiness hashes.

2. **Files**:
   - `src/self_harness/scanner_db_update.py`:
     - `ScannerDbUpdateCommand` (fields: `cache_dir: Path`, `additional_args: tuple[str, ...]`).
     - `ScannerDbUpdateResult` (fields: `schema_version`, `scanner`, `mode`, `ok`, `exit_code`, `command: tuple[str, ...]`, `reason: str | None`).
     - `build_trivy_db_update_command(command, *, trivy_binary="trivy") -> list[str]`.
     - `run_scanner_db_update(command, *, dry_run=False, trivy_binary="trivy", timeout_seconds=300) -> ScannerDbUpdateResult`.
     - Validation: `cache_dir` must be provided to ensure updates target the directory validated by P32 freshness policies.
   - `scripts/scanner_db_update.py`:
     - CLI wrapper exposing `--cache-dir`, `--dry-run`, `--trivy-binary`, `--trivy-arg`.
     - Outputs structured JSON result.
   - `Makefile`:
     - Update `scanner-check` target to invoke `scripts/scanner_db_update.py --dry-run --cache-dir tests/fixtures/vuln/trivy_db`.
   - `tests/test_scanner_db_update.py`:
     - Unit tests for command construction (validates inclusion of `--download-db-only` and `--cache-dir`).
     - Tests for `dry_run=True` execution path.
     - Tests for validation errors (missing cache dir).
   - `docs/operations/scanner_execution.md`:
     - Document the new DB update command construction and dry-run capabilities.

3. **Acceptance Tests**:
   - `make scanner-check` successfully runs the DB update dry-run and validates the command structure.
   - `tests/test_scanner_db_update.py` passes.
   - `make check` and `make readiness` remain unaffected and green.

4. **Explicit Deferrals**:
   - Live execution of the DB update command (`dry_run=False` subprocess call) is implemented but not executed in CI to respect the "no real scanner DB updates" constraint.
   - Provider-specific registry login helpers for pulling scanner DBs from private registries (if using a mirroring setup).
   - Automated DB rotation and cleanup scripts.

## Remaining Open Questions
None blocking. Future slices will need to tackle live environment testing (Harbor/Docker/Trivy) which requires infrastructure outside the current local CI capabilities.

[usage] {"completion_tokens": 3629, "completion_tokens_details": {"reasoning_tokens": 2814}, "prompt_tokens": 20604, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 24233}
