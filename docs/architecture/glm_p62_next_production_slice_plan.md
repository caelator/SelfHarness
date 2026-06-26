CONVERGED: YES

## Verdict

Round 1 produced an execution-ready P62 slice: offline post-capture extractors that transform operator-captured raw JSON into the strict reproduction artifact-class shapes already enforced by `_artifact_shapes.py`. The four open questions are cheap defaults to lock in now and do not require additional repository evidence or user decision. The slice is additive, schema-stable, offline-only, testable against existing validators, and preserves `reproduction_claimed:false` by construction.

## Critique

Evidence (from repo):
- `_artifact_shapes.py` defines strict validators for all 10 required artifact classes; `artifact_shape_error_from_payload` allows in-process validation in tests without file round-trips.
- `reproduction_readiness.py` rejects any input containing `reproduction_claimed:true` recursively, so extractor outputs must fail closed on that field.
- P55 `reproduction_bundle_build.py` packages operator-authored artifacts but never authors their contents; P58–P61 give plan/sign/rehearse/diff/verify tooling but every required artifact-class JSON is still hand-authored.
- `dist/self-harness-reproduction-readiness.json` currently reports all 12 requirements as failing, primarily due to blocked dependencies and missing artifacts; the three model-backend rows fail additionally because the dry-run model preflight has `mode != live`.

Inference:
- The dominant remaining operator manual gap is content authoring of artifact-class JSON from raw captured output against the existing strict validators.
- Extractors preserve the no-reproduction contract trivially because they are deterministic transforms that force `reproduction_claimed:false` and never mutate readiness rows.
- Scope of 6 raw-output-derived classes is the right cut: the 3 synthesis-only classes (`live_terminal_bench_split_manifest`, `fixed_protocol_config`, `release_candidate_evidence`) require operator policy decisions, and `audit_verify_report` is already produced by P61's `audit-verify-live`.

Risks addressed:
- Provider schema drift: extractors fail closed on unknown fields.
- Wall-clock injection: extractors require explicit operator-supplied timestamps from raw inputs or flags.
- Scope creep: deferred the 4 synthesis/already-produced classes to P63+.
- Coupling to bundle builder: extractors remain strictly upstream; no `reproduction_bundle_build` changes in P62.

## Required Changes

None blocking. Lock in the round 1 open-question defaults:
1. CLI = single `self-harness capture-extract` dispatcher with `--class` and class-specific raw-input flags; `scripts/capture_extract.py` mirrors it for parity with P55/P59.
2. `live_two_repeat_evaluation_report` extractor requires a captured-live envelope plus a per-task attempts JSONL with exactly 2 boolean pass entries per task; reject local engine JSONL without the envelope.
3. `network_resource_controls_attestation` is declarative (cap + mirrored resources + operator envelope); optional `capture_run_id` metadata only; no binding to a specific Harbor run id.
4. Bundle integration deferred; `reproduction_bundle_build.py` is unchanged in P62.

## Revised Plan

**P62 — Post-capture live-evidence extractors**

Files (new):
- `src/self_harness/capture_extract.py` — pure extractor functions, one per class, returning validated JSONable payloads; each takes raw captured inputs plus explicit operator metadata and fails closed on unknown fields, missing digests, non-live mode markers, wall-clock injection, or `reproduction_claimed:true` leakage.
- `scripts/capture_extract.py` — CLI dispatcher (`--class`, `--out`, class-specific raw input flags).
- `tests/test_capture_extract.py` — happy paths assert `artifact_shape_error_from_payload(class, payload) is None`; failure cases cover unknown raw field, missing digest, wrong attempt count, injected `captured_at`, `reproduction_claimed:true` leakage, and mode drift.
- `tests/fixtures/capture_extract/<class>/*.json` — minimal raw inputs and expected outputs (deterministic, no live data).

Files (edited, additive only):
- `Makefile` — add standalone `capture-extract-check` target depending only on the new tests; do **not** add to default `check`/`release-smoke`.
- `docs/operations/capture_extract.md` — operator doc placing extractors between live capture and `reproduction_bundle_build.py` in the P58 sequence.

Extractor scope (6 classes):
- `live_harbor_preflight_report` ← Harbor discovery JSON + explicit operator `harbor_version`; force `mode:"live"`, `ok:true`, `harbor_reachable:true`.
- `container_image_trust_report` ← Harbor discovery `RepoDigests` + operator image policy; enforce `policy:"digest-bound"`, `all_digest_bound:true`, non-empty `images` with `sha256:` digests.
- `model_backend_preflight_report` ← three provider preflight raw outputs; promote `mode` to `live` only when raw inputs prove live capture and all required checks pass.
- `network_resource_controls_attestation` ← operator declarative inputs (positive `outbound_bandwidth_cap_bps`, non-empty `mirrored_resources`, optional `capture_run_id`).
- `live_harbor_audit` ← captured Harbor trial-artifact directory + verifier outcomes; emit `ok:true`, `mode:"live"`, non-empty `trial_artifacts` with `task_id`, `captured:true`, `verifier_outcome`.
- `live_two_repeat_evaluation_report` ← captured-live envelope + per-task JSONL requiring exactly 2 boolean `pass` entries per task; enforce `attempts_per_task:2`.

Deferred to P63+: `live_terminal_bench_split_manifest`, `fixed_protocol_config`, `release_candidate_evidence`, `audit_verify_report`.

Acceptance gates:
1. `make capture-extract-check` passes offline with no network/Docker/Harbor/model/PyPI/Sigstore contact.
2. Every extractor output validates clean through the existing `_artifact_shapes` validator for its class.
3. Extractors reject any input or flag combination that would produce `reproduction_claimed:true`, a missing digest, a non-live `mode`, or an implicit wall-clock value.
4. `make reproduction-readiness-check` unchanged; canonical readiness hash unchanged; `dist/self-harness-reproduction-readiness.json` continues to report `reproduction_ready:false`.
5. No audit/corpus/manifest/readiness schema version changes.

## Remaining Open Questions

None blocking for P62. Future slices may revisit:
- Bundle integration (`--extract-dir` mode in `reproduction_bundle_build.py`) — revisit in P63 once extractors are exercised by operators.
- Synthesis-only class helpers for the 3 deferred classes once operator workflow clarifies whether they should be authored, templated, or generated from existing release-evidence artifacts.
