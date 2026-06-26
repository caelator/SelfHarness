CONVERGED: YES

## Verdict
The highest-value, locally testable next slice is **P31: automated scanner execution orchestrator**. It closes the loop opened by P28-P30 (offline Trivy parsing, image-policy binding, freshness validation) by adding a deterministic, dry-run/replay-capable Trivy execution seam. This mirrors the established pattern from P20 (container verifier boundary), P24 (Harbor command construction), and P26 (provenance signing): construct argv explicitly, support fixture-backed tests, gate live execution behind preflight, and change no audit/corpus/manifest schema. It is execution-ready because every acceptance test can run offline using fixture reports and argv snapshots.

## Critique
Initial temptation was to pick provider-specific KMS wrappers (P21 external signer is already a seam) or pre-run Harbor image discovery. Both are higher-friction: KMS wrappers are illustrative scripts with low reusable test value until a real signer exists, and Harbor image discovery requires fixture catalogs that may not match real Harbor API shapes without a live endpoint to validate against. Scanner execution, by contrast, directly makes the P28-P30 evaluation pipeline operator-usable, has a clear local test surface (argv snapshot + replay), and preserves paper fidelity because it touches only release/operator material — not audit, corpus, readiness hashes, or reproduction claims. The only material risk is overpromising "automated scanning" in CI; the plan must explicitly defer live Trivy invocation and require operator-supplied Trivy binaries.

## Required Changes
- Keep all scanner execution artifacts strictly operator/release material: no new audit schema field, no readiness hash change, no reproduction-claim path.
- Live execution must be opt-in and fail closed when Trivy is missing, returning exit code 2 with a structured JSON error, mirroring P20 Docker preflight behavior.
- Replay mode must reuse the exact P28-P30 parsers (`load_trivy_report`, `load_trivy_image_references`, `load_trivy_report_timestamp`) rather than duplicating logic.
- Document explicit deferral of: KMS/HSM wrappers, registry OAuth helpers, Sigstore/PyPI attestations, Harbor image discovery, and migration shims.

## Revised Plan

**Slice: P31 — Scanner execution orchestrator (offline-testable Trivy boundary)**

Files to add:
- `src/self_harness/scanner_execution.py`
  - `ScannerCommand` frozen dataclass: `image`, `digest`, `format="json"`, `output_path`, `db_dir`, `additional_args: tuple[str, ...]`.
  - `build_trivy_command(cmd: ScannerCommand) -> list[str]` — deterministic argv construction.
  - `ScannerPreflightReport` frozen dataclass with `ok`, `reason`, `trivy_path`, `db_path`.
  - `preflight_scanner(cmd, *, trivy_binary="trivy") -> ScannerPreflightReport` — checks binary presence and DB metadata file existence; never executes Trivy.
  - `ScannerRunResult` with `ok`, `exit_code`, `report_path`, `command`, `preflight`.
  - `run_scanner(cmd, *, dry_run=False, replay_report=None, trivy_binary="trivy") -> ScannerRunResult` — in `dry_run` returns the command without executing; in `replay_report` mode copies a fixture report to `output_path` and skips execution; otherwise executes Trivy via subprocess with bounded timeout.
  - Raises `ScannerExecutionError` for malformed inputs, missing binary in live mode, or non-zero Trivy exit.
- `scripts/scanner_run.py`
  - CLI: `--image`, `--digest`, `--out`, `--format trivy`, `--dry-run`, `--replay <path>`, `--trivy-binary`, `--db-dir`, `--image-policy`, `--freshness-policy`, `--vuln-policy`, `--today`.
  - Orchestrates: build command → preflight → run (or replay/dry-run) → invoke existing `run_vulnerability_check` from `scripts/vuln_check.py` on the produced report.
  - Writes structured JSON result combining execution metadata and vulnerability policy decision.
- `tests/fixtures/trivy_sample_report.json` — minimal valid Trivy JSON with `Metadata.RepoDigests`, `Metadata.CreatedAt`, and one `Results` entry.
- `tests/fixtures/trivy_db_metadata.json` — sample DB metadata for preflight tests.
- `tests/test_scanner_execution.py` — covers:
  - Deterministic argv construction (snapshot test).
  - Dry-run returns command, no subprocess, no output file.
  - Replay mode copies fixture and produces a passing vuln/image/freshness decision.
  - Preflight fails closed when binary is missing (live mode).
  - Image-policy mismatch in replayed report fails closed.
  - Stale freshness in replayed report fails closed.
- `docs/operations/scanner_execution.md` — operator doc showing dry-run, replay (for CI determinism), and live invocation patterns; explicitly states no reproduction claim and no schema impact.
- `Makefile` — add `scanner-check` target wrapping `scripts/scanner_run.py --dry-run` against a sample image to validate command construction in CI without Trivy.

Files to modify:
- `RELEASE.md` — add a "Scanner Execution" subsection under release process documenting `make scanner-check` as a dry-run gate and explicitly deferring live Trivy execution to operator environments.
- `docs/architecture/productionization_brief.md` — mark P31 implemented and narrow the remaining-work list.

Schema boundaries:
- No audit schema change.
- No corpus schema change.
- No manifest schema change.
- No readiness hash change.
- Scanner execution report is operator/release material only; `reproduction_claimed` must not appear in any scanner artifact.

Acceptance tests:
1. `make scanner-check` runs offline, exits 0, prints deterministic argv.
2. `python scripts/scanner_run.py --replay tests/fixtures/trivy_sample_report.json --image-policy <fixture-policy> --freshness-policy <fixture-policy> --out /tmp/report.json` exits 0 and the produced vuln report has `ok: true`.
3. Replaying a report whose digest is not in the image policy exits 2 with `image_policy.allowed == false`.
4. Replaying a report with a stale timestamp exits 2 with `freshness.allowed == false`.
5. Live mode without `trivy` on PATH exits 2 with a `preflight.ok == false` JSON error and does not create the output report.
6. `make check` and `make readiness` remain green; canonical audit hash fixture is unchanged.

Explicit deferrals (not in this slice):
- Real Harbor/Docker benchmark execution on a provisioned host.
- Provider-specific KMS/HSM/hardware-token corpus-signing wrappers (P21 external signer protocol already supports them; concrete wrappers deferred).
- Provider-specific registry/OAuth/secret-manager helpers.
- Pre-run Harbor image discovery from live Harbor API.
- Sigstore and PyPI attestations.
- CI invocation of real Trivy against real images.
- Scanner DB auto-refresh logic (only preflight presence check here).
- Future major-version migration shims.

## Remaining Open Questions
- Should `make scanner-check` also assert the constructed argv matches a committed golden snapshot file, or is stdout comparison in the test sufficient? Non-blocking: current plan uses pytest snapshot inside `tests/test_scanner_execution.py`; golden file can be added later if operators want a repo-level reference.
- Should the orchestrator eventually support `grype` as an alternative scanner format? Non-blocking: P28 parser is Trivy-specific; a future `ScannerKind` enum can extend without schema change.
- Is a `--db-refresh` subprocess wrapper (invoking `trivy db update`) in scope for a later slice? Non-blocking: explicitly deferred here; preflight only checks DB metadata presence.
