CONVERGED: YES

## Verdict

P60 closed the offline capture-rehearsal loop, but the live operator path has a silent contract gap that is offline-testable and materially blocks the first real reproduction attempt. The reproduction-readiness `audit_verify_report` artifact class requires `mode: "live"`, `ok: true`, `held_out_leakage: false`, and auditability flags set to true. The current `audit-verify` implementation emits `mode: "replay"` and cannot produce a `mode: "live"` report under any operator input. The capture-manifest rehearsal materializes a synthetic stub that passes shape validation, but no tool path exists to transform a real captured `live_harbor_audit` artifact into the `audit_verify_report` evidence class that reproduction-readiness demands. This is the single highest-value offline slice because it closes the gap between P11 Harbor ingest, P39 audit verification, and P49 reproduction readiness without requiring live Harbor/Docker/model access.

## Critique

- **Evidence (validated):** `dist/self-harness-reproduction-readiness.json` shows the `no_held_out_leakage` requirement fails with `invalid artifact evidence: dist/self-harness-audit-verify.json: audit verify report mode must be live`. Every operator run will hit this exact failure.
- **Evidence (validated):** `src/self_harness/capture_manifest_build.py` `_planned_artifact_stub` emits an `audit_verify_report` stub with `mode: "live"`, which passes shape validation, but no production tool produces that shape from real captured artifacts. The rehearsal therefore proves the plan is internally consistent but not executable.
- **Evidence (validated):** P11 `harbor-ingest` converts Harbor artifacts into schema `1.4` audit directories, and P39 `verify_audit_run` verifies an existing audit directory. But `verify_audit_run` hardcodes `mode: "replay"` semantics; there is no seam to mark a verification as backed by live Harbor execution.
- **Inference:** Operators preparing a costly live run currently have no offline way to prove that their captured Harbor audit will pass the reproduction-readiness `audit_verify_report` gate. They will discover the gap only after spending live resources.
- **Risk addressed:** Without this slice, the first live reproduction attempt is blocked at the final evidence gate despite every upstream offline gate passing. The slice stays offline by introducing a verifiable live-audit provenance contract rather than live execution.
- **Risk addressed:** The slice preserves `reproduction_claimed=false` everywhere because the new seam only describes how a live capture *would* be verified; it does not execute or claim anything.

## Required Changes

None blocking. The plan is additive, offline-only, introduces one new minor provenance schema, and rotates no canonical hashes.

## Revised Plan

**P61: Live-mode audit verification provenance seam**

1. **`src/self_harness/audit_verify_live.py`**
   - New module that wraps `verify_audit_run` with an explicit live-capture provenance contract.
   - Inputs: existing audit directory (produced by `harbor-ingest` or equivalent), operator-supplied `LiveAuditProvenance` dataclass with `capture_run_id`, `harbor_version`, `captured_at`, `operator_label`, `live_harbor_audit_artifact_path`, and a required detached Ed25519 signature over the provenance payload.
   - The wrapper re-runs the existing `verify_audit_run` checks, then attaches the provenance block and emits `mode: "live"` instead of `mode: "replay"` only when all of the following hold:
     - the underlying audit verification passed;
     - the provenance signature verifies against an operator-supplied public key;
     - the referenced `live_harbor_audit_artifact_path` exists, is non-empty, and itself has `reproduction_claimed: false` and `mode: "live"`;
     - the audit directory's `task_source_hash` entries match the referenced live Harbor audit artifact's task ids.
   - Refuses to emit `mode: "live"` if any check fails; falls back to a structured `mode: "live_blocked"` report with per-check failure detail rather than silently downgrading.
   - Returns a `LiveAuditVerifyReport` with `schema_version: "1.0"`, `ok`, `mode`, `report_hash`, `reproduction_claimed: false`, the underlying replay report hash, the provenance fingerprint, and the boundary string.

2. **`scripts/audit_verify_live.py`**
   - CLI wrapper: `--audit-dir`, `--live-harbor-audit`, `--provenance`, `--provenance-signature`, `--public-key`, `--require-signature`, `--out`.
   - Exit codes: `0` clean live verification, `2` live verification blocked with structured report, `3` corrupt inputs.

3. **`self-harness audit-verify-live`** subcommand
   - Mirrors the existing `audit-verify` installed-CLI surface with the additional provenance inputs.

4. **Makefile targets**
   - `audit-verify-live` (standalone, fixture-backed, no live contact).
   - Extend `capture-rehearsal` to include the live-audit-verify seam in the rehearsal chain when a synthetic `live_harbor_audit` artifact and synthetic provenance are supplied.

5. **Tests** (`tests/test_audit_verify_live.py`)
   - Fixture audit directory from P39 + synthetic `live_harbor_audit` artifact + signed provenance → report has `mode: "live"`, `ok: true`, `report_hash` deterministic.
   - Failure cases: missing provenance signature, provenance signature mismatch, referenced live artifact missing, referenced live artifact claims reproduction, referenced live artifact not `mode: "live"`, task id mismatch between audit directory and live artifact, underlying audit verification failure, and fallback to `mode: "live_blocked"`.
   - Cross-check: the produced report must pass the reproduction-readiness `audit_verify_report` shape validator.

6. **Docs** (`docs/operations/audit_verify_live.md`)
   - Document the operator workflow: after `harbor-ingest`, sign a `LiveAuditProvenance` payload, run `audit-verify-live`, and use the resulting report as the `audit_verify_report` artifact class evidence in reproduction bundles.

7. **Acceptance criteria**
   - `make audit-verify-live` runs offline across Python 3.11/3.12/3.13 and produces a deterministic report with `mode: "live"` and `ok: true` on the fixture inputs.
   - The report passes the existing reproduction-readiness `audit_verify_report` shape validator.
   - No audit schema change, corpus schema change, manifest schema change, canonical readiness hash rotation, default release path change, or reproduction-claim semantics change.
   - No live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud contact is added.

8. **Explicit non-goals**
   - Live Harbor execution or artifact capture.
   - New audit schema versions (the live provenance is a wrapper, not a schema bump).
   - Changing the default `audit-verify` emit mode.
   - Hash rotation of any committed release-evidence or reproduction-readiness fixture.
   - Validation of operator-supplied provenance payloads beyond signature validity and live-artifact binding.

## Remaining Open Questions

1. **Provenance schema versioning.** Default to `live_audit_provenance/1.0`. If operators want it ingested by release-candidate evidence later, add it as advisory metadata in a follow-up rather than expanding the release gate now.
2. **Whether to add a `--strict-live` flag** to `reproduction-readiness-check` that fails when `audit_verify_report` is missing or not `mode: "live"`. Default: do not add; the existing shape validator already enforces this for the artifact class.
3. **Whether `harbor-ingest` should emit the provenance payload automatically.** Default: no. Operators sign provenance explicitly so custody stays outside the ingest tool.
