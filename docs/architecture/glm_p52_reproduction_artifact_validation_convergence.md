# Round 1

CONVERGED: NO

## Verdict
The repo confirms the gap exactly: `reproduction_readiness._artifact_evidence_error` returns `None` (i.e., passes) for every artifact class except `model_backend_preflight_report`, while `test_reproduction_readiness_can_pass_with_synthetic_provisioned_evidence` proves a generic `{ok: true, reproduction_claimed: false}` JSON placeholder satisfies all 11 requirement rows once dependencies are flipped to `provisioned`. That is the fail-open hole P52 must close. An executable plan exists, but round 1 should land a revised candidate rather than converge because three material design choices (validator dispatch shape, schema-location policy, fixture-vs-test boundary) need a deliberate decision before code lands.

## Critique
Evidence (validated against repository):
- `src/self_harness/reproduction_readiness.py`: `_artifact_evidence_error` is a single special case keyed on `"model_backend_preflight_report"`. Everything else falls through to `None`. `_evaluate_requirement` then only enforces (a) artifact file exists and is non-empty, (b) artifact does not contain `reproduction_claimed: true`. That is the placeholder-acceptance hole.
- `docs/operations/benchmark_reproduction_requirements.json` enumerates 10 distinct `required_artifact_class` values across 12 requirements; only one (`model_backend_preflight_report`) is currently shape-checked.
- `tests/test_reproduction_readiness.py::test_reproduction_readiness_can_pass_with_synthetic_provisioned_evidence` writes the same `{"ok": True, "reproduction_claimed": False}` body for every class except the model preflight, and asserts `reproduction_ready is True`. This test currently *codifies* the bug and must be rewritten as part of P52.
- `benchmark_reproduction_readiness.md` already promises a fail-closed contract ("A requirement passes only when…at least one non-empty artifact exists for the required artifact class"), so adding shape validators aligns with the documented contract rather than changing it.
- `readiness_matrix.py` shows the project convention: closed enumerated surface sets (`ALLOWED_*`, `KNOWN_*`) plus hand-rolled validators, not a JSON-Schema library. P52 should follow that convention to keep the dependency surface unchanged.

Inference:
- The paper supplies enough invariant signal to define shape/count validators for every class without hard-coding private Terminal-Bench-2.0 task IDs: 64-case total, held-in/held-out disjoint, two repeated attempts, three fixed backends, fixed evaluator/tool/budget, Harbor/Docker execution, controlled network, no held-out leakage, auditable/reversible changes, no reproduction claim without live evidence.

Architecture risks:
1. **Shape sprawl** — ad-hoc per-class `if` branches in `_artifact_evidence_error` will become unmaintainable as the artifact set grows. Risk: medium. Mitigation: dispatch table mapping class → validator function, each returning `str | None` error.
2. **Hidden hard-coding of private task IDs** — easy to accidentally embed real Terminal-Bench-2.0 task IDs. Risk: high (explicitly forbidden by task). Mitigation: validators inspect count/disjointness/shape only; never compare against an enumerated task-ID list.
3. **Fixture-vs-test boundary blur** — round-1 tests using inline JSON blur which fixtures are release evidence vs. test scaffolding. Risk: medium. Mitigation: class-shaped fixtures under `tests/fixtures/release_candidate/artifacts/<class>.json`, distinct from test-only malformed inputs.
4. **Hash rotation surprise** — adding validators does not change the readiness *catalog*, but it does change what `evaluate_reproduction_readiness` accepts; canonical reproduction-readiness fixture hashes in `tests/fixtures/release_candidate/reproduction_readiness_result.json` will rotate because checks now fail on the placeholder. Risk: low but must be called out.
5. **Replay/dry-run ambiguity** — several classes legitimately have a "dry-run" form (e.g., `live_harbor_preflight_report`). Validators must reject dry-run/replay for *reproduction* evidence while a separate operator-inspection path can still produce them. Risk: medium. Mitigation: each validator requires `mode: live` (or class-equivalent) exactly as the model preflight validator already does.

## Required Changes
1. Refactor `_artifact_evidence_error` into `_ARTIFACT_CLASS_VALIDATORS: Mapping[str, Callable[[Path], str | None]]` with one entry per required class in `benchmark_reproduction_requirements.json`. Keep the existing `model_backend_preflight_report` validator unchanged; add ten new validators.
2. Each validator must:
   - require valid JSON object;
   - require `reproduction_claimed is False` (defense in depth; the upstream `_contains_reproduction_claim` scan already covers this, but the validator should still assert shape);
   - require a class-specific `mode`/`source`/`provenance` field set to the live value;
   - require class-specific invariant fields per the paper;
   - return a human-readable error string on any failure, `None` on success.
3. Rewrite `test_reproduction_readiness_can_pass_with_synthetic_provisioned_evidence` to construct one correctly-shaped fixture per artifact class; rename to `..._with_class_shaped_provisioned_evidence`. Add a sibling negative test asserting that the old `{ok: true}` placeholder body fails every non-model class.
4. Add `tests/invariants/test_reproduction_readiness_artifact_shapes.py` enumerating every `required_artifact_class` in the requirements catalog and asserting a validator exists (closed dispatch), so future catalog additions fail closed until a validator is registered.
5. Add a docs section in `benchmark_reproduction_readiness.md` listing each artifact class and its required fields, citing the paper section that justifies each field.
6. Add release-verification commands: `make reproduction-readiness-check` (unchanged exit-code contract: 0 ready, 2 valid-but-not-ready, 3 corrupt) plus a new `make reproduction-readiness-artifact-shape-lint` target that runs only the shape validators over a supplied `--artifact-dir` without requiring readiness-matrix input, for fast operator feedback.
7. Regenerate `tests/fixtures/release_candidate/reproduction_readiness_result.json` exactly once in the landing PR; document the one-time rotation in the PR description and in `benchmark_reproduction_readiness.md`.

## Revised Plan

### 1. Validator dispatch (src/self_harness/reproduction_readiness.py)
- Replace `_artifact_evidence_error(artifact_class, path)` body with dispatch through a module-level `_ARTIFACT_CLASS_VALIDATORS` mapping.
- Keep signature and return type (`str | None`) unchanged so callers are untouched.
- Unknown artifact class ⇒ return `"unsupported required_artifact_class: <class>"` (fail closed) instead of `None`. This is the core P52 fix.

### 2. Per-class shape contracts (paper-justified, no private task IDs)
Each validator lives in a new private module `src/self_harness/_artifact_shapes.py` to keep `reproduction_readiness.py` readable. Required fields per class:

- `live_terminal_bench_split_manifest` — `{schema_version, mode: "live", source: "harbor", total_cases: 64, held_in_count: int, held_out_count: int, held_in_task_ids: [...], held_out_task_ids: [...], fixed_across_variants: true, reproduction_claimed: false}`. Invariants: `held_in_count + held_out_count == total_cases == 64`; `held_in_count == len(held_in_task_ids)`; `held_out_count == len(held_out_task_ids)`; `set(held_in) ∩ set(held_out) == ∅`. Do NOT compare task IDs against any baked-in list.
- `live_two_repeat_evaluation_report` — `{mode: "live", attempts_per_task: 2, per_task_attempts: [{task_id, attempts: [{pass: bool}]}], reproduction_claimed: false}`. Invariants: every entry has exactly 2 attempts; `attempts_per_task == 2`.
- `fixed_protocol_config` — `{mode: "live", models: ["minimax-m2.5", "qwen3.5-35b-a3b", "glm-5"], evaluator: str (non-empty), tool_set: str (non-empty), decoding_budget: object, fixed_across_variants: true, reproduction_claimed: false}`. Invariants: exactly the three paper backends; non-empty evaluator/tool fields.
- `live_harbor_preflight_report` — `{mode: "live", harbor_reachable: true, harbor_version: str, reproduction_claimed: false}`.
- `container_image_trust_report` — `{mode: "live", policy: "digest-bound", images: [{name, digest}], all_digest_bound: true, reproduction_claimed: false}`. Invariant: every image has a non-empty `digest`.
- `model_backend_preflight_report` — unchanged (`ok: true`, `mode: "live"`).
- `network_resource_controls_attestation` — `{mode: "live", outbound_bandwidth_cap_bps: int > 0, mirrored_resources: [str], reproduction_claimed: false}`.
- `live_harbor_audit` — `{mode: "live", trial_artifacts: [{task_id, verifier_outcome, captured: true}], reproduction_claimed: false}`. Invariant: non-empty list.
- `audit_verify_report` — `{mode: "live", held_out_leakage: false, proposer_evidence_inspected: true, changed_surfaces_recorded: true, evaluation_repeats_recorded: true, rejected_reasons_recorded: true, reproduction_claimed: false}`. Maps directly to the paper's auditability claim (Section 3.4).
- `release_candidate_evidence` — `{schema_version, bindings: {package_artifact, provenance, attestation_material, reproduction_readiness_report_hash}, reversible: true, reproduction_claimed: false}`. Invariant: every binding key present and non-empty.

### 3. Tests
- `tests/test_reproduction_readiness.py`: replace the placeholder-acceptance test with a class-shaped acceptance test and a placeholder-rejection test.
- `tests/invariants/test_reproduction_readiness_artifact_shapes.py`: closed-dispatch invariant (every catalog class has a validator) + per-class positive/negative cases.
- `tests/fixtures/release_candidate/artifacts/<class>.json`: one well-formed fixture per class, used by the acceptance test.

### 4. Docs
- `docs/operations/benchmark_reproduction_readiness.md`: new "Required artifact shapes" section with one row per class, field list, paper citation, and the explicit rule that validators inspect supplied artifacts only and never contact Harbor/Docker/registries/scanners/PyPI/Sigstore/model APIs/cloud.
- `docs/operations/release_verification.md` (new or appended): the two `make` targets and their exit-code contract.

### 5. Release verification commands
```bash
# Full readiness gate (unchanged exit-code contract: 0 ready, 2 not-ready, 3 corrupt)
make reproduction-readiness-check

# Fast artifact-shape lint over a supplied artifact dir; same exit codes
make reproduction-readiness-artifact-shape-lint ARTIFACT_DIR=dist/reproduction-artifacts
```

### 6. Hash rotation
- Regenerate `tests/fixtures/release_candidate/reproduction_readiness_result.json` once; the `not ready` outcome is preserved because real dependencies stay blocked, only the *failure detail strings* change.
- Add a one-line note in `benchmark_reproduction_readiness.md` documenting that P52 rotates this fixture hash exactly once.

## Remaining Open Questions
1. **Validator location** — preferred: new private `src/self_harness/_artifact_shapes.py`. Alternative: inline in `reproduction_readiness.py`. Decide in round 2; either is acceptable, the new module keeps the file readable as classes grow.
2. **JSON-Schema vs. hand-rolled** — preferred: hand-rolled, matching `readiness_matrix.py` convention and avoiding a new dependency. Confirm in round 2.
3. **`audit_verify_report` provenance** — this class is already produced locally by existing audit tooling per `tests/test_reproduction_readiness.py` (the file `audit_verify_result.json` is read via `--audit-verify-result`). Need to confirm in round 2 whether the existing local audit report already carries the four auditability fields the new validator requires, or whether the audit tool must be extended in parallel. If the latter, scope that work into P52 or split it out.
4. **`release_candidate_evidence` schema source of truth** — confirm whether `release_candidate_evidence` already has a declared schema elsewhere (release tooling) that P52 must align with rather than invent. Round 2 should grep `release_candidate_evidence` across `scripts/` and `src/` before locking the field list.

[usage] {"completion_tokens": 3733, "completion_tokens_details": {"reasoning_tokens": 984}, "prompt_tokens": 27293, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 31026}

---

# Round 2

CONVERGED: YES

## Verdict
The round-1 candidate plan is executable, repo-grounded, and faithful to the paper. The four open questions are decidable now without new experiments: the codebase conventions (`readiness_matrix.py` hand-rolled validators, closed dispatch sets, `scripts/` operator tools, `Makefile` operator-gated targets) make each choice clear-cut, and the paper supplies enough invariant signal to define every artifact shape without hard-coding private Terminal-Bench task IDs. P52 can land.

## Critique
Evidence (validated against repository):
- `src/self_harness/reproduction_readiness.py::_artifact_evidence_error` is the fail-open hole: it special-cases only `model_backend_preflight_report` and returns `None` for every other class. `_evaluate_requirement` then accepts any non-empty JSON that does not contain `reproduction_claimed: true`.
- `tests/test_reproduction_readiness.py::test_reproduction_readiness_can_pass_with_synthetic_provisioned_evidence` writes the same `{"ok": True, "reproduction_claimed": False}` body for every class except model preflight and asserts `reproduction_ready is True`. This test codifies the bug.
- `docs/operations/benchmark_reproduction_requirements.json` enumerates 10 distinct `required_artifact_class` values; only one is shape-checked today.
- `docs/operations/benchmark_reproduction_readiness.md` already promises a fail-closed contract ("at least one non-empty artifact exists for the required artifact class"), so adding per-class shape validators aligns with the documented contract rather than changing it.
- `src/self_harness/readiness_matrix.py` shows the project convention: closed enumerated surface sets (`ALLOWED_*`, `KNOWN_*`) plus hand-rolled validators, no JSON-Schema dependency. P52 must follow the same convention.
- `scripts/reproduction_readiness_report.py` is already invoked by `test_reproduction_readiness.py` via `scripts/reproduction_readiness_report.py`; adding a sibling `scripts/reproduction_readiness_artifact_shape_lint.py` matches the existing operator-tool pattern.

Inference:
- The paper supplies enough signal to define every class without hard-coding private task IDs: fixed 64-case subset (Section 4.1), held-in/held-out disjointness and never-shown-to-proposer (Section 4.1), two repeated attempts (Section 4.1), three fixed backends (Section 4.1, Appendix A.1), fixed evaluator/tool/budget (Section 4.2), Harbor/Docker execution and 2 MB/s outbound cap plus mirrored stable resources (Appendix A.1), and auditable/reversible changes (Section 3.4).

Architecture risks (resolved or bounded):
1. **Shape sprawl** — resolved by closed dispatch table `_ARTIFACT_CLASS_VALIDATORS: Mapping[str, Callable[[Path], str | None]]` plus invariant test that every catalog class has a registered validator.
2. **Hidden hard-coding of private task IDs** — bounded by the rule that validators inspect count/disjointness/shape only and never compare against an enumerated task-ID list; invariant test asserts the validator module contains no Terminal-Bench-2.0 task-id string literals.
3. **Fixture-vs-test boundary blur** — resolved by placing well-formed fixtures under `tests/fixtures/release_candidate/artifacts/<class>.json` and malformed inputs inline in tests.
4. **Hash rotation surprise** — bounded by the documented one-time rotation policy in `benchmark_reproduction_readiness.md`.
5. **Replay/dry-run ambiguity** — resolved by requiring `mode: "live"` (or class-equivalent) in every validator, mirroring the existing `model_backend_preflight_report` rule.

## Required Changes
1. **Resolve Open Question 1 (validator location):** introduce a new private module `src/self_harness/_artifact_shapes.py`. The new module keeps `reproduction_readiness.py` readable as the artifact catalog grows and matches the project's pattern of one concern per module (`readiness_matrix.py`, `readiness_drift.py`).
2. **Resolve Open Question 2 (JSON-Schema vs hand-rolled):** hand-rolled validators returning `str | None`, matching `readiness_matrix.py` and `reproduction_drift.py` conventions. No new dependency.
3. **Resolve Open Question 3 (`audit_verify_report` provenance):** the existing local audit tool (`tests/fixtures/release_candidate/audit_verify_result.json`, read via `--audit-verify-result`) currently produces a minimal shape. P52 must extend the audit tool in parallel to emit the four paper auditability fields (`held_out_leakage`, `proposer_evidence_inspected`, `changed_surfaces_recorded`, `evaluation_repeats_recorded`, `rejected_reasons_recorded`), all set to `true`/`false` as appropriate, plus `mode: "live"` only when the audit is over a live run. Scope this work into P52; it is a small additive change to the existing audit emitter and avoids splitting P52 into two PRs.
4. **Resolve Open Question 4 (`release_candidate_evidence` schema source of truth):** P52 must grep `release_candidate_evidence` across `scripts/` and `src/` before locking the field list. The round-1 proposed shape (`schema_version`, `bindings`, `reversible`, `reproduction_claimed`) is the *minimum required*; if release tooling already declares additional binding keys, the P52 validator must accept a superset rather than reject the existing shape. This is a one-time alignment step in the landing PR, not a blocking design decision.
5. **Closed dispatch:** unknown `required_artifact_class` ⇒ return `"unsupported required_artifact_class: <class>"` (fail closed) instead of `None`. This is the core P52 fix.
6. **Invariant test:** `tests/invariants/test_reproduction_readiness_artifact_shapes.py` enumerates every `required_artifact_class` in the requirements catalog and asserts a validator exists, so future catalog additions fail closed until a validator is registered.
7. **Rewrite the placeholder-acceptance test:** replace `test_reproduction_readiness_can_pass_with_synthetic_provisioned_evidence` with `test_reproduction_readiness_can_pass_with_class_shaped_provisioned_evidence`, and add a sibling negative test asserting the old `{ok: true}` placeholder body fails every non-model class.
8. **Docs:** add a "Required artifact shapes" section in `benchmark_reproduction_readiness.md` with one row per class, field list, paper citation, and the explicit rule that validators inspect supplied artifacts only and never contact Harbor/Docker/registries/scanners/PyPI/Sigstore/model APIs/cloud. Document the one-time hash rotation of `tests/fixtures/release_candidate/reproduction_readiness_result.json`.
9. **Release verification commands:** `make reproduction-readiness-check` (unchanged exit-code contract) plus a new `make reproduction-readiness-artifact-shape-lint ARTIFACT_DIR=...` for fast operator feedback.
10. **Auditability alignment:** extend the audit emitter to produce the four auditability fields; update `tests/fixtures/release_candidate/audit_verify_result.json` accordingly; regenerate `reproduction_readiness_result.json` once.

## Revised Plan

### 1. Validator dispatch (`src/self_harness/reproduction_readiness.py`)
- Replace `_artifact_evidence_error(artifact_class, path)` body with dispatch through `_ARTIFACT_CLASS_VALIDATORS: Mapping[str, Callable[[Path], str | None]]` defined in `src/self_harness/_artifact_shapes.py`.
- Unknown artifact class ⇒ return `"unsupported required_artifact_class: <class>"`.
- Keep the public signature and return type unchanged.

### 2. Per-class shape contracts (`src/self_harness/_artifact_shapes.py`)
Hand-rolled validators, no external schema library. Each returns `str | None`, requires valid JSON object, asserts `reproduction_claimed is False`, requires `mode: "live"` (or class-equivalent), and enforces paper-justified invariants. No validator may compare against a baked-in Terminal-Bench-2.0 task-id list.

- `live_terminal_bench_split_manifest` — fields `{schema_version, mode: "live", source: "harbor", total_cases: 64, held_in_count, held_out_count, held_in_task_ids: [...], held_out_task_ids: [...], fixed_across_variants: true, reproduction_claimed: false}`. Invariants: `held_in_count + held_out_count == total_cases == 64`; `held_in_count == len(held_in_task_ids)`; `held_out_count == len(held_out_task_ids)`; `set(held_in) ∩ set(held_out) == ∅`.
- `live_two_repeat_evaluation_report` — `{mode: "live", attempts_per_task: 2, per_task_attempts: [{task_id, attempts: [{pass: bool}]}], reproduction_claimed: false}`. Invariant: every entry has exactly 2 attempts; `attempts_per_task == 2`.
- `fixed_protocol_config` — `{mode: "live", models: ["minimax-m2.5", "qwen3.5-35b-a3b", "glm-5"], evaluator: non-empty str, tool_set: non-empty str, decoding_budget: object, fixed_across_variants: true, reproduction_claimed: false}`. Exactly the three paper backends.
- `live_harbor_preflight_report` — `{mode: "live", harbor_reachable: true, harbor_version: non-empty str, reproduction_claimed: false}`.
- `container_image_trust_report` — `{mode: "live", policy: "digest-bound", images: [{name, digest}], all_digest_bound: true, reproduction_claimed: false}`. Every image has non-empty `digest`.
- `model_backend_preflight_report` — unchanged (`ok: true`, `mode: "live"`).
- `network_resource_controls_attestation` — `{mode: "live", outbound_bandwidth_cap_bps: int > 0, mirrored_resources: [str], reproduction_claimed: false}`.
- `live_harbor_audit` — `{mode: "live", trial_artifacts: [{task_id, verifier_outcome, captured: true}], reproduction_claimed: false}`. Non-empty list.
- `audit_verify_report` — `{mode: "live", held_out_leakage: false, proposer_evidence_inspected: true, changed_surfaces_recorded: true, evaluation_repeats_recorded: true, rejected_reasons_recorded: true, reproduction_claimed: false}`. Aligns with paper Section 3.4 auditability.
- `release_candidate_evidence` — `{schema_version, bindings: {package_artifact, provenance, attestation_material, reproduction_readiness_report_hash}, reversible: true, reproduction_claimed: false}`. Round-2 alignment step: grep `release_candidate_evidence` in `scripts/` and `src/` and accept any superset declared by existing release tooling.

### 3. Audit emitter extension (in scope)
- Extend the audit tool producing `audit_verify_result.json` to emit the five auditability fields. Default to `mode: "replay"` for offline runs (so the validator rejects it for reproduction evidence but operators can still inspect); only `mode: "live"` satisfies the reproduction gate.
- Update `tests/fixtures/release_candidate/audit_verify_result.json` with the extended shape and `mode: "replay"`.

### 4. Tests
- `tests/test_reproduction_readiness.py`: replace the placeholder-acceptance test with a class-shaped acceptance test and a placeholder-rejection test.
- `tests/invariants/test_reproduction_readiness_artifact_shapes.py`: closed-dispatch invariant (every catalog class has a validator) + per-class positive/negative cases + an assertion that no validator module string-literal matches a Terminal-Bench-2.0 task-id pattern.
- `tests/fixtures/release_candidate/artifacts/<class>.json`: one well-formed fixture per class.

### 5. Docs
- `docs/operations/benchmark_reproduction_readiness.md`: new "Required artifact shapes" section with one row per class, field list, paper citation, the explicit no-live-contact rule, and the one-time hash-rotation note.
- `docs/operations/release_verification.md`: the two `make` targets and the exit-code contract (0 ready, 2 not-ready, 3 corrupt).

### 6. Release verification commands
```bash
make reproduction-readiness-check
make reproduction-readiness-artifact-shape-lint ARTIFACT_DIR=dist/reproduction-artifacts
```

### 7. Hash rotation
- Regenerate `tests/fixtures/release_candidate/reproduction_readiness_result.json` exactly once in the landing PR; the not-ready outcome is preserved because real dependencies stay blocked, only the failure detail strings change.
- Document the rotation in `benchmark_reproduction_readiness.md`.

## Remaining Open Questions
None blocking. All four round-1 questions resolved by this architect decision:
1. New private module `src/self_harness/_artifact_shapes.py` (chosen).
2. Hand-rolled validators, no JSON-Schema dependency (chosen).
3. Extend the existing audit emitter in scope to produce the four auditability fields; default `mode: "replay"` for offline, `mode: "live"` for reproduction (chosen).
4. Grep `release_candidate_evidence` in the landing PR and accept any superset declared by existing release tooling; validator never rejects the existing shape (chosen).

[usage] {"completion_tokens": 2887, "completion_tokens_details": {"reasoning_tokens": 31}, "prompt_tokens": 32817, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 35704}
