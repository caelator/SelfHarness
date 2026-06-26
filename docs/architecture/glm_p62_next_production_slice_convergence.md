# Round 1

CONVERGED: NO

## Verdict

Initial plan proposed below. The strongest implementable P62 slice that satisfies the brief (reduce operator manual gaps for post-capture live evidence, no live contact, clear acceptance tests, not just docs, preserves `reproduction_claimed:false`) is a set of **offline post-capture live-evidence extractors**: deterministic builders that transform operator-captured raw JSON into the strict reproduction artifact-class shapes already enforced by `_artifact_shapes.py`. This directly attacks the biggest manual gap surfaced by P58–P61: operators can plan, rehearse, and verify, but after a live Harbor/Docker/model run they still have to hand-author each required artifact-class JSON against tight validators. The slice is additive, offline, schema-stable, and testable against the existing validators.

This is returned as `CONVERGED: NO` because two design choices (scope of raw-input coverage and whether to introduce a unified `capture-extract` CLI vs. per-class scripts) should be confirmed before execution. Neither is blocking on evidence; both are cheap to resolve in round 2.

## Critique

Evidence (from repo):
- `src/self_harness/_artifact_shapes.py` already defines strict per-class validators for all 10 required artifact classes (`live_terminal_bench_split_manifest`, `live_two_repeat_evaluation_report`, `fixed_protocol_config`, `live_harbor_preflight_report`, `container_image_trust_report`, `model_backend_preflight_report`, `network_resource_controls_attestation`, `live_harbor_audit`, `audit_verify_report`, `release_candidate_evidence`).
- `benchmark_reproduction_requirements.json` binds each requirement to a `required_artifact_class` and `required_state: provisioned`.
- P55 `reproduction_bundle_build.py` packages files but does not author their contents; it requires operators to supply already-shaped artifacts under `--artifact-dir`.
- P58–P61 give operators plan/sign/rehearse/diff/verify tooling, but every required artifact-class JSON is still hand-authored from raw captured output.

Inference:
- The dominant remaining operator manual gap is content authoring of the 10 artifact classes from raw Harbor/Docker/model/scanner outputs. A wrong-shaped file fails closed with a single line error; there is no guided transformation.
- Extractors keep the no-reproduction contract trivially: they are deterministic transforms that inject explicit operator metadata and force `reproduction_claimed:false`; they never mark readiness rows provisioned (that remains operator/promotion-driven).

Risks:
- Scope creep: covering all 10 classes in one slice is large. Recommend covering the 6 "raw-output-derived" classes in P62 and deferring the 3 "synthesis-only" classes (`live_terminal_bench_split_manifest`, `fixed_protocol_config`, `release_candidate_evidence`) and `audit_verify_report` (already produced by `audit-verify-live`).
- Coupling to raw provider schemas: extractors must be strict and fail closed on unknown fields to avoid drift if Harbor/Docker/Trivy change shapes.
- Determinism: extractors must refuse to inject wall-clock values; `captured_at` etc. must come from raw inputs or explicit operator flags.

## Required Changes

1. **Confirm P62 scope** = the 6 raw-output-derived artifact classes:
   - `live_harbor_preflight_report` ← Harbor discovery JSON + operator `harbor_version`.
   - `container_image_trust_report` ← Harbor discovery `RepoDigests` + image policy (reuse P29 logic).
   - `model_backend_preflight_report` ← three provider preflight raw outputs (reuse P51 shape, promote `mode` to `live` only when raw inputs prove live capture).
   - `network_resource_controls_attestation` ← operator attestation inputs (cap, mirrored resources) plus a captured Harbor run envelope; pure authoring helper with strict fields.
   - `live_harbor_audit` ← captured Harbor trial-artifact directory + verifier outcomes (reuse P11 ingest shape but emit the reproduction-class artifact).
   - `live_two_repeat_evaluation_report` ← captured per-task attempt log requiring exactly 2 boolean attempts per task.
2. **Defer** `live_terminal_bench_split_manifest`, `fixed_protocol_config`, `release_candidate_evidence`, and `audit_verify_report` to P63+; they are synthesis-only or already produced.
3. **Decide CLI shape** (open question): recommend a single `self-harness capture-extract <class>` subcommand backed by `scripts/capture_extract_<class>.py` mirrors, consistent with P55/P59 pattern.
4. **Acceptance gate** = every extractor output must pass `artifact_shape_error_from_payload` for its class, plus fixture-backed tests for malformed/unknown-field/missing-digest/captured-at-injection failures.
5. **No schema bumps**: do not change audit/corpus/manifest/readiness schemas; do not rotate canonical readiness hash; do not touch reproduction-claim semantics.

## Revised Plan

**P62 — Post-capture live-evidence extractors**

Files (new):
- `src/self_harness/capture_extract.py` — pure extractor functions, one per artifact class, returning validated JSONable payloads; each function takes raw captured inputs plus explicit operator metadata and fails closed on unknown fields, missing digests, non-live mode markers, or wall-clock injection.
- `scripts/capture_extract.py` — CLI dispatcher (`--class`, `--out`, class-specific raw input flags) plus per-class fixtures for `make capture-extract-check`.
- `tests/test_capture_extract.py` — fixture-backed tests:
  - happy path per class asserts `artifact_shape_error_from_payload(class, payload) is None`;
  - failure cases: unknown raw field, missing digest, wrong attempt count, injected `captured_at`, `reproduction_claimed:true` leakage, mode drift.
- `tests/fixtures/capture_extract/<class>/*.json` — minimal raw inputs and expected outputs (deterministic, no live data).

Files (edited, additive only):
- `Makefile` — add standalone `capture-extract-check` target depending only on the new tests; do **not** add to default `check`/`release-smoke`.
- `docs/operations/capture_extract.md` — operator doc placing extractors between live capture and `reproduction_bundle_build.py` in the P58 sequence.

Acceptance gates:
1. `make capture-extract-check` passes offline with no network/Docker/Harbor/model/PyPI/Sigstore contact.
2. Every extractor output validates clean through the existing `_artifact_shapes` validator for its class.
3. Extractors reject any input or flag combination that would produce `reproduction_claimed:true`, a missing digest, a non-live `mode`, or an implicit wall-clock value.
4. `make reproduction-readiness-check` unchanged in behavior; canonical readiness hash unchanged; `dist/self-harness-reproduction-readiness.json` continues to report `reproduction_ready:false`.
5. No audit/corpus/manifest/readiness schema version changes.

## Remaining Open Questions

1. **CLI shape**: single `capture-extract` subcommand with `--class` dispatch vs. one CLI per class? Recommend the dispatcher for parity with `reproduction_bundle_build` and easier future extension; confirm in round 2.
2. **`live_two_repeat_evaluation_report` raw input format**: should the extractor consume the engine's local per-task attempts JSONL, a captured Harbor attempt log, or both behind a flag? The reproduction requirement is live-only, so the extractor should require a captured-live envelope; confirm the expected raw shape in round 2 (does not require repo evidence beyond the existing class validator).
3. **`network_resource_controls_attestation`**: is the operator attestation purely declarative (cap + mirrored resources + run envelope) or should it bind to a specific Harbor run id? Recommend declarative + optional `capture_run_id` metadata field to keep P62 minimal.
4. **Bundle integration**: should `reproduction_bundle_build.py` gain a `--extract-dir` mode that runs extractors before packaging, or keep extractors strictly upstream? Recommend keeping them upstream in P62 to avoid coupling; revisit in P63.

[usage] {"completion_tokens": 2536, "completion_tokens_details": {"reasoning_tokens": 831}, "prompt_tokens": 41364, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 43900}

---

# Round 2

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

[usage] {"completion_tokens": 1638, "completion_tokens_details": {"reasoning_tokens": 216}, "prompt_tokens": 44801, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 46439}
