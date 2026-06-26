# Live Audit Verification

`self-harness audit-verify` is intentionally replay-mode release evidence. It
checks an audit tree's internal consistency, but it cannot satisfy the paper
reproduction requirement for a live audit verification report.

Use live audit verification after an operator has already captured live Harbor
trial artifacts and converted them into an audit-compatible directory:

```bash
python scripts/audit_verify_live.py \
  --audit-dir ops/live-audit \
  --live-harbor-audit ops/live_harbor_audit.json \
  --provenance ops/live-audit-provenance.json \
  --provenance-signature ops/live-audit-provenance.sig \
  --public-key keys/live-audit-provenance.ed25519.pub \
  --require-signature \
  --out dist/self-harness-audit-verify-live.json
```

The installed CLI exposes the same verifier:

```bash
self-harness audit-verify-live \
  --audit-dir ops/live-audit \
  --live-harbor-audit ops/live_harbor_audit.json \
  --provenance ops/live-audit-provenance.json \
  --provenance-signature ops/live-audit-provenance.sig \
  --public-key keys/live-audit-provenance.ed25519.pub \
  --require-signature \
  --json
```

The verifier emits `mode:"live"` only when all of these checks pass:

- the underlying replay audit verifier passes;
- the live audit provenance document is schema-valid and keeps
  `reproduction_claimed:false`;
- the detached Ed25519 signature verifies over the exact provenance bytes;
- the provenance document resolves to the supplied live Harbor audit artifact;
- the live Harbor audit `capture_run_id` matches the signed provenance
  `capture_run_id`;
- the live Harbor audit artifact has `ok:true`, `mode:"live"`, and captured
  trial artifacts;
- audit task ids exactly match live Harbor trial task ids;
- `task_source_hash` values match when the audit records them.

If any required condition fails, the report uses `mode:"live_blocked"` and
`ok:false`; it does not silently downgrade to replay mode.

`make audit-verify-live` runs a deterministic offline fixture path. It creates a
synthetic Harbor artifact tree, ingests it into an audit directory, writes a
live-shaped Harbor artifact, signs live-audit provenance with temporary local
key material, and writes `dist/self-harness-audit-verify-live.json`.

This workflow validates existing files only. It does not execute Terminal-Bench
tasks, invoke models, contact Harbor, Docker, registries, scanners, PyPI,
Sigstore, model providers, or cloud providers, and it never claims benchmark
reproduction.
