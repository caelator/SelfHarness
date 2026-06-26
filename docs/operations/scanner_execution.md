# Scanner Execution

Self-Harness can orchestrate Trivy scanner execution while keeping scanner
outputs as release/operator material. The scanner path does not change audit
schemas, corpus schemas, readiness hashes, or benchmark reproduction claims.

## Dry Run

Use dry-run mode in CI or release checks to validate deterministic command
construction without requiring Trivy, Docker, registry access, or a scanner
database:

```bash
make scanner-check
python scripts/scanner_run.py \
  --dry-run \
  --image registry.example/trusted/verifier:1 \
  --digest sha256:<digest> \
  --out dist/self-harness-trivy-report.json
```

Dry-run mode prints the command in the structured JSON result and does not
create the scanner report.

## Replay

Replay mode copies a supplied Trivy JSON report to the requested output path and
then evaluates it through the existing vulnerability, image-policy, and
freshness policy gates:

```bash
python scripts/scanner_run.py \
  --image registry.example/trusted/verifier:1 \
  --digest sha256:<digest> \
  --out dist/image-vulns.json \
  --replay tests/fixtures/vuln/trivy_fresh_with_timestamp.json \
  --image-policy security/image-policy.json \
  --freshness-policy security/freshness-policy.json \
  --vuln-policy security/vulnerability-policy.json
```

Replay mode is deterministic and is the preferred path for offline CI coverage.

## Live

Live mode omits `--dry-run` and `--replay`. It preflights the Trivy executable
and, when `--db-dir` is supplied, requires Trivy DB metadata at either
`metadata.json` or `db/metadata.json` under that directory before running.
When `--db-freshness-policy` is supplied, the metadata must also satisfy the
operator-owned scanner DB freshness policy:

```bash
python scripts/scanner_run.py \
  --image registry.example/trusted/verifier:1 \
  --digest sha256:<digest> \
  --out dist/image-vulns.json \
  --trivy-binary trivy \
  --db-dir "$TRIVY_CACHE_DIR" \
  --db-registry-config "$TRIVY_REGISTRY_CONFIG" \
  --db-freshness-policy security/scanner-db-freshness-policy.json \
  --image-policy security/image-policy.json \
  --freshness-policy security/freshness-policy.json \
  --vuln-policy security/vulnerability-policy.json
```

Live mode fails closed with exit code 2 when preflight fails, Trivy exits
non-zero, the report is missing required image digest metadata, or policy
evaluation rejects the report.

Replay mode also evaluates DB freshness when `--db-dir` and
`--db-freshness-policy` are supplied, but it does not require the Trivy binary.

## DB Freshness Policy

Scanner DB freshness policies use schema version `1`:

```json
{
  "policy_version": "1",
  "max_age_days": 7,
  "require_next_update": true
}
```

`require_next_update` defaults to `true` and rejects metadata whose
`NextUpdate` is missing or earlier than the evaluation date. `max_age_days`
rejects metadata whose `UpdatedAt` is older than the configured number of
calendar days. The policy must require `NextUpdate`, set `max_age_days`, or
both. Missing, malformed, stale, or future-dated metadata fails closed.

## DB Update

Operators can dry-run the Trivy DB update command that should populate the same
cache directory later validated by DB freshness preflight:

```bash
python scripts/scanner_db_update.py \
  --dry-run \
  --cache-dir "$TRIVY_CACHE_DIR"
```

Live mode omits `--dry-run` and executes:

```bash
trivy image --cache-dir "$TRIVY_CACHE_DIR" --download-db-only
```

Additional Trivy options, such as an internal mirror registry, must be passed
explicitly with repeated `--trivy-arg` values:

```bash
python scripts/scanner_db_update.py \
  --dry-run \
  --cache-dir "$TRIVY_CACHE_DIR" \
  --db-registry-config "$TRIVY_REGISTRY_CONFIG" \
  --trivy-arg=--db-repository \
  --trivy-arg registry.example/trivy-db:2
```

`make scanner-check` exercises this path in dry-run mode only; CI does not
download scanner databases.

## Registry Config Files

`--db-registry-config` maps to Trivy's `--registry-config` flag using an
absolute path in the constructed command. Dry-run mode checks command
construction without reading the file. Live scanner and DB-update modes fail
closed when the requested registry config file is missing.

Registry config files are operator-owned secret material. Self-Harness records
the config path in command traces for reproducibility, but it never reads,
prints, copies, or writes the file contents to release artifacts.

## Boundaries

- The orchestrator shells out only to the configured Trivy binary.
- Additional scanner options must be passed explicitly with repeated
  `--trivy-arg` values. Use `--trivy-arg=--flag` when the argument itself
  begins with `--`.
- Registry auth files are passed by path only; their contents must stay outside
  audit artifacts, scanner result JSON, release notes, and CI logs.
- The policy evaluation path reuses `scripts/vuln_check.py`; scanner execution
  does not duplicate vulnerability, image, or freshness parsing.
- Scanner DB freshness validates supplied metadata only; it does not update or
  download scanner databases.
- Scanner DB update orchestration constructs and optionally runs the Trivy DB
  update command, but CI only dry-runs it.
- Scanner reports are time-sensitive release evidence, not benchmark
  reproduction evidence.
