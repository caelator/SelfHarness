# Audit Integrity Verification

`self-harness audit-verify` verifies the internal consistency of an existing
audit directory without re-running tasks.

```bash
self-harness audit-verify runs/demo --json --out dist/self-harness-audit-verify.json
```

The verifier checks:

- manifest schema version support;
- lineage rows and `rounds/<n>` directory coverage;
- `harness_before.json` and `harness_after.json` hashes against lineage;
- round-to-round harness continuity;
- proposal ids and accepted/merged proposal ids against lineage;
- proposal and evaluation row schema versions;
- baseline and committed split-total evaluation rows;
- proposal rows for held-out pattern or task evidence leakage;
- optional `migration_provenance` shape when present.

The report schema is `1.0` and includes `ok`, structured checks, a deterministic
`report_hash`, and a boundary statement. Exit codes are:

- `0`: verification passed;
- `2`: the audit loaded, but one or more consistency checks failed;
- `3`: core audit artifacts are missing, unsupported, or corrupt.

This is an offline release/operator gate. It reads an audit directory and may
write a report outside that directory, but it does not mutate audit artifacts,
execute tasks, invoke models, contact Harbor, Docker, registries, scanners,
PyPI, Sigstore, or cloud providers, or claim benchmark reproduction.
