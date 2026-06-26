# Round 1

CONVERGED: NO

## Verdict
Round 1 produces a strong initial P64 candidate—**capture admission**—that orchestrates the existing extract→shape-lint→bundle-build→bundle-verify→readiness pipeline into a single deterministic admission report. It is additive, offline-only, schema-stable, and binds the operator workflow without changing any existing gate. One round of revision is needed to lock the CLI surface, the no-new-shape-validator rule, and the interaction with `reproduction_bundle_build`'s explicit-metadata contract. After those revisions the slice is execution-ready.

## Critique

Evidence (from repo):
- P62/P63 extractors cover **all 8 raw-output-derived classes**; `_artifact_shapes.py` enforces shapes both inline in extractors and via `artifact_shape_error_from_payload`.
- `reproduction_bundle_build.py` requires explicit `bundle_id`/`operator_label`/`created_at`/`source_provider`/`source_captured_at` and already runs strict shape validation on every artifact; it is deterministic and injects nothing.
- `reproduction_bundle_verify.py` accepts an optional signature; `sign_reproduction_bundle.py` produces a sidecar; `reproduction_readiness_report.py` can consume a bundle as the sole artifact source.
- The current `Makefile` exposes each step as a separate target (`capture-extract-check`, `reproduction-bundle-build`, `reproduction-bundle-sign`, `reproduction-bundle-check`, `reproduction-readiness-check`), but there is **no single command that binds raw-input files → extracted artifacts → bundle → verification → readiness** into one auditable artifact.
- `benchmark_reproduction_requirements.json` drives the required class set; it is already the single source of truth for extractors, builder, verifier, shape-lint, and readiness.

Inference:
- The dominant local-implementable operator gap is **orchestration and provenance binding**, not new validators or new artifact classes. Each individual step exists; what is missing is one deterministic report that records which raw inputs produced which extracted artifacts, which bundle and signature resulted, and whether readiness passed.
- The right scope is a read-only orchestrator that **calls existing primitives in order** and emits a single admission report with a stable `report_hash`. It must not introduce a new shape validator, a new artifact class, or a new reproduction claim path.
- The slice preserves the no-reproduction contract by construction because every downstream primitive already fails closed on `reproduction_claimed:true`, non-live modes, missing digests, and ad-hoc artifact mixing.

Risks addressed:
- Workflow ordering drift: one command enforces the canonical sequence.
- Partial-evidence ambiguity: the report records per-class extraction status and per-step hashes so operators cannot claim a "pass" from a subset.
- Bundle-metadata injection: the orchestrator inherits the P55 explicit-metadata contract; it never supplies defaults for `bundle_id`, `created_at`, `operator_label`, or source fields.
- Reproduction-claim leakage: the orchestrator forwards `_contains_reproduction_claim` checks through the existing primitives.

## Required Changes
1. Lock the CLI surface to **one new subcommand** `self-harness capture-admit` and one script `scripts/capture_admit.py`; do **not** add per-step subcommands. Inputs: `--requirements`, `--readiness-matrix-result`, repeated `--raw-input CLASS=PATH`, repeated `--raw-flag KEY=VALUE`, explicit bundle metadata flags mirroring `reproduction_bundle_build.py`, optional `--bundle-signature` + `--bundle-public-key`, optional `--skip-readiness`, optional `--out` for the admission report, optional `--artifact-dir` for extracted artifacts.
2. Lock the **no-new-validator** rule: the orchestrator must not re-implement shape checks. It calls `extract_artifact_from_paths`, `artifact_shape_error_from_payload` is already invoked inside extractors, and bundle verification performs file-byte shape validation. The admission report records these as pass-through statuses.
3. Lock the **no-default-metadata** rule: bundle metadata is required exactly as in `reproduction_bundle_build.py`; the orchestrator fails closed with the same error if any field is missing. No `CAPTURE_ADMIT_*` env defaults.
4. Lock the readiness interaction: by default, admission runs readiness against the verified bundle using `--reproduction-bundle`; `--skip-readiness` produces an admission report whose `readiness` section is explicitly `skipped` and whose `report_hash` differs, so skipped and full admissions cannot be confused.
5. Lock the boundary: the admission report carries `reproduction_claimed:false`, the `REPRODUCTION_ADMIT_BOUNDARY` string, and explicit "this is an offline orchestrator only; it does not contact live services" language.
6. Lock the Makefile wiring: add `capture-admit-check` as a standalone target that runs the new tests against fixture raw inputs; do **not** add it to `check` or `release-smoke`.

## Revised Plan

**P64 — Capture admission orchestrator**

Files (new):
- `src/self_harness/capture_admit.py` — pure orchestration: parse raw-input spec, extract each class into `--artifact-dir`, run `artifact_shape_error_from_payload` (already called by extractors; record result), build bundle via `build_reproduction_bundle`, optionally sign via `sign_bytes`/external signer, verify via `verify_reproduction_bundle`, optionally evaluate readiness via `evaluate_reproduction_readiness` with the bundle as artifact index. Returns a `CaptureAdmissionReport` dataclass with per-step statuses, per-class extraction hashes, bundle path, bundle sha256, signature fingerprint, readiness `reproduction_ready` flag (or `skipped`), and a deterministic `report_hash`.
- `scripts/capture_admit.py` — CLI dispatcher mirroring the installed `self-harness capture-admit` subcommand.
- `tests/test_capture_admit.py` — happy path: fixture raw inputs for all 8 extractable classes + `audit_verify_report` fixture → admission report `ok:true`, `reproduction_claimed:false`, `report_hash` matches committed fixture, bundle verification `ok:true`, readiness `reproduction_ready:true` when the readiness matrix fixture is provisioned. Failure cases: missing raw input for a required class, missing bundle metadata, extractor failure (unknown field), bundle signature required but absent, readiness not skipped vs skipped `report_hash` differs, `reproduction_claimed:true` in any raw input fails closed.
- `tests/fixtures/capture_admit/raw_inputs/` — minimal fixture raw inputs covering the 8 extractable classes (deterministic, no live data; reuses the shapes already in `tests/test_capture_extract.py` fixtures).
- `tests/fixtures/capture_admit/audit_verify_report.json` — reuses the existing `tests/fixtures/release_candidate/audit_verify_result.json` shape with `mode:"live"` so the `audit_verify_report` class is satisfied for the full-readiness path.
- `tests/fixtures/capture_admit/admission_report.json` — committed canonical report for hash-stability assertion.
- `docs/operations/capture_admit.md` — operator doc placing admission as the post-capture step that produces one auditable artifact binding raw inputs, extracted artifacts, bundle, signature, and readiness.

Files (edited, additive only):
- `src/self_harness/cli.py` — add `capture-admit` subparser forwarding to `scripts/capture_admit.py` semantics; reuse the same argparse shape so installed CLI and script are identical.
- `Makefile` — add `capture-admit-check` target depending only on `tests/test_capture_admit.py`; do **not** add to `check`/`release-smoke`.
- `docs/architecture/productionization_brief.md` — append a P64 section with scope, boundary, no-default-release-path change, and no-reproduction-claim statement.

Admission report schema (`capture_admission/1.0`):
```json
{
  "schema_version": "1.0",
  "ok": true,
  "admission_id": "<operator-supplied>",
  "operator_label": "<operator-supplied>",
  "created_at": "<operator-supplied>",
  "requirements_path": "docs/operations/benchmark_reproduction_requirements.json",
  "raw_inputs": [
    {"artifact_class": "live_harbor_preflight_report", "raw_input_paths": [...], "raw_flags": {"harbor_version": "2.10.0"}}
  ],
  "extractions": [
    {"artifact_class": "live_harbor_preflight_report", "extracted_path": "dist/reproduction-artifacts/live_harbor_preflight_report.json", "sha256": "...", "byte_size": 1234, "shape_valid": true}
  ],
  "bundle": {"path": "...", "bundle_id": "...", "bundle_sha256": "...", "operator_label": "...", "created_at": "..."},
  "signature": {"present": true, "fingerprint": "...", "key_id": "...", "provider": "..."} | {"present": false},
  "bundle_verification": {"ok": true, "report_hash": "...", "checks": [...]},
  "readiness": {"skipped": false, "ok": true, "reproduction_ready": true, "report_hash": "..."} | {"skipped": true},
  "report_hash": "<sha256 of the report without report_hash>",
  "reproduction_claimed": false,
  "boundary": "<CAPTURE_ADMIT_BOUNDARY>"
}
```

Admission sequence (strict ordering, fail-closed between steps):
1. Validate `--requirements` loads; derive required class set.
2. Parse `--raw-input CLASS=PATH` and `--raw-flag KEY=VALUE`; group by class.
3. For each required class with raw inputs, call `extract_artifact_from_paths`; write to `--artifact-dir/<class>.json`; record sha256/byte_size.
4. For each required class without raw inputs but present in `--artifact-dir`, record as "supplied-pre-extracted" with shape check result; reject if neither raw input nor pre-extracted artifact exists.
5. Call `build_reproduction_bundle` with explicit operator metadata; write bundle.
6. If signature custody inputs supplied, call `sign_bytes`/external signer; write `.sig` sidecar.
7. Call `verify_reproduction_bundle` with `require_signature=True` if custody supplied; record `bundle_verification` block.
8. Unless `--skip-readiness`, call `evaluate_reproduction_readiness` with `reproduction_bundle_artifact_index(bundle)` and the supplied `--readiness-matrix-result`; record `readiness` block. Use the bundle as the sole artifact source; fail closed if `--artifact-dir` is also supplied alongside the bundle (same rule as `reproduction_readiness_report.py`).
9. Compute `report_hash` over the report sans `report_hash`; write report.

Stop conditions:
- No live Harbor/Docker/model/registry/scanner/PyPI/Sigstore/cloud contact.
- No new artifact class, no new shape validator, no new readiness gate, no new reproduction-claim path.
- No audit schema, corpus schema, manifest schema, canonical readiness hash, or canonical audit hash change.
- No default `make check`/`make smoke`/release path change; `capture-admit-check` is operator-only.

Acceptance gates:
1. `make capture-admit-check` passes offline.
2. Admission report `report_hash` matches the committed fixture; the fixture is regenerated only when the admission surface set changes.
3. Every extractor output in the admission report validates via `artifact_shape_error_from_payload`.
4. `dist/self-harness-reproduction-readiness.json` is unchanged unless an operator explicitly supplies the new artifacts via admission; the readiness contract remains fail-closed.
5. Skipped-readiness and full-readiness admission reports produce different `report_hash` values and cannot be confused.

## Remaining Open Questions
- Should the admission report support **incremental admission** (admit a subset of classes across multiple runs, then merge)? Recommendation: out of scope for P64; the current contract is one admission per complete bundle. A future slice could add `capture-admit-merge` if operators need it.
- Should the admission report embed the **full bundle verification checks array**, or only a summary hash reference to the bundle verification report? Recommendation: embed the full checks array so the admission report is self-contained for operator audit; this matches the existing `reproduction_bundle_report_to_jsonable` shape and does not leak secrets.
- Should `capture-admit` accept a **signed capture manifest** and diff the realized bundle against it (reusing `capture_manifest_diff.py`) as an additional admission step? Recommendation: out of scope for P64; admission binds raw inputs to bundle/readiness, while manifest diff binds plan to realization. They are complementary and should remain separate commands; a future slice could add `--require-capture-manifest-diff` if operators want both in one artifact.

[usage] {"completion_tokens": 3176, "completion_tokens_details": {"reasoning_tokens": 432}, "prompt_tokens": 58615, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 61791}

---

# Round 2

CONVERGED: YES

## Verdict
Round 1's revised P64 candidate is execution-ready. The capture admission orchestrator is the correct seam: it composes existing primitives (extract→bundle→sign→verify→readiness) into one auditable, hash-stable report without introducing new validators, new artifact classes, new metadata defaults, or new reproduction-claim paths. All six locking decisions in round 1 are concrete and material; the three remaining open questions are non-blocking with defensible out-of-scope recommendations.

## Critique
Evidence-backed strengths:
- Extractors (P62/P63) cover all 8 raw-derived classes; `_artifact_shapes.py` enforces shape inside extractors, inside `build_reproduction_bundle`, and inside readiness evaluation — so an orchestrator that records pass-through status introduces no new validation surface.
- `build_reproduction_bundle` already requires explicit `bundle_id`/`operator_label`/`created_at`/`source_provider`/`source_captured_at`; mirroring those as required CLI flags (with no env defaults) preserves the no-injection contract.
- `verify_reproduction_bundle` already accepts optional signature and supports `require_signature`; the orchestrator's optional custody wiring is a pure passthrough.
- `evaluate_reproduction_readiness` already accepts a bundle as the sole artifact source and fails closed when `--artifact-dir` is also supplied — the orchestrator inherits that contract.
- Existing fixtures (`tests/fixtures/release_candidate/audit_verify_result.json`, `_class_shaped_payloads` in `tests/test_reproduction_readiness.py`) prove that a `mode:"live"` `audit_verify_report` shape is achievable offline, so the happy-path admission fixture is realizable without live data.

Inferred:
- The dominant local-implementable gap is provenance binding across the post-capture workflow; no new validator/class/gate is required.
- Deterministic `report_hash` over the report-minus-`report_hash` body matches the established pattern across bundle/readiness/release reports and gives operators one auditable artifact.
- Recording `readiness.skipped` distinctly from a full readiness evaluation (and making the two produce different `report_hash` values) prevents confusion without weakening the fail-closed contract.

Risks addressed:
- Workflow ordering drift → strict ordered sequence with fail-closed between steps.
- Partial-evidence ambiguity → per-class extraction status plus bundle verification and readiness sections.
- Metadata injection → no `CAPTURE_ADMIT_*` env defaults; same explicit-metadata rule as bundle builder.
- Reproduction-claim leakage → forward through every existing primitive that already fails closed on `reproduction_claimed:true`.

## Required Changes
None blocking. The round 1 lock-ins are sufficient:
- CLI surface locked to one subcommand `self-harness capture-admit` + `scripts/capture_admit.py`.
- No new validators, no new classes, no new readiness gates, no new reproduction-claim paths.
- Explicit bundle metadata required; no env defaults.
- `--skip-readiness` produces a distinct, explicitly-marked report with a distinct `report_hash`.
- Boundary string and offline-only language locked into the report.
- `make capture-admit-check` is standalone; not added to `check`/`release-smoke`.

## Recommended Next Moves
Execute the P64 slice as specified:
1. Implement `src/self_harness/capture_admit.py` as a pure orchestrator calling existing primitives in order; emit `CaptureAdmissionReport` dataclass + stable `report_hash`.
2. Wire `scripts/capture_admit.py` and the `capture-admit` CLI subparser to identical argparse semantics.
3. Add `tests/test_capture_admit.py` with: happy-path 8-class fixture + live `audit_verify_report`; fail-closed cases for missing raw input, missing bundle metadata, extractor failure, missing required signature, skipped-vs-full readiness hash distinction, and `reproduction_claimed:true` leakage.
4. Commit `tests/fixtures/capture_admit/{raw_inputs/,audit_verify_report.json,admission_report.json}` for hash stability; document regeneration policy (only on admission-surface change).
5. Add `docs/operations/capture_admit.md` and a P64 note in `docs/architecture/productionization_brief.md` restating scope, offline boundary, and no-reproduction-claim.
6. Add `make capture-admit-check` standalone target.

## Remaining Open Questions (non-blocking, defaults accepted)
- Incremental admission: out of scope for P64 (one admission per complete bundle).
- Embedding full bundle verification checks array: yes, embed for self-contained operator audit (matches existing `reproduction_bundle_report_to_jsonable`).
- Capture-manifest diff integration: out of scope; complementary commands remain separate.

Stop conditions remain exactly as specified in round 1: no live contact, no schema/hash rotation, no default release-path change, no reproduction claim.

[usage] {"completion_tokens": 1380, "completion_tokens_details": {"reasoning_tokens": 362}, "prompt_tokens": 64130, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 65510}
