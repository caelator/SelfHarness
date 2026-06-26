# Round 1

CONVERGED: NO

## Verdict
Initial architecture plan is concrete and implementable using only the surfaced repository evidence. The schema, validator, extractor, fixtures, and tests all live in the files shown, so the slice can be delivered offline without contacting any external dependency. The plan is not yet ready to execute because one design decision (which aggregate fields are mandatory vs. forbidden) needs to be locked before code is written, and the backward-safety justification must be explicitly grounded in tests/docs.

## Critique
- Evidence: `_live_two_repeat_evaluation_report` in `_artifact_shapes.py` validates `attempts_per_task == 2`, per-task `attempts` length 2, and boolean `pass` values, but performs no aggregate reconciliation (task count, attempt count, pass count). A payload with `per_task_attempts` consistent but missing or inconsistent aggregate counts would pass.
- Evidence: `extract_live_two_repeat_evaluation_report` in `capture_extract.py` emits no aggregate count fields at all; the only check on counts happens implicitly via per-task row construction.
- Evidence: Test fixtures in `tests/test_reproduction_readiness.py::_class_shaped_payloads` and `tests/test_capture_extract.py::_fixture_paths` do not include aggregate fields, so they would need updating once the schema changes.
- Inference: Because the artifact is produced only by the in-tree extractor and consumed only by the readiness/bundle verifiers in the same repo, adding *required* aggregate fields is backward-safe for this codebase; the justification is the absence of any external producer/consumer plus the fixture-update requirement.
- Inference: The requirement catalog (`benchmark_reproduction_requirements.json`) is descriptive and does not encode artifact JSON schema, so no schema change is needed there — only a notes clarification if desired.

## Required Changes
- Lock the aggregate-field vocabulary before implementation. Recommend: `task_count: int`, `attempt_count: int` (= `task_count * attempts_per_task`), `pass_count: int` (sum over attempts of `pass is True`). Decide whether `fail_count` is also mandatory (recommended for symmetry and ease of cross-checking).
- Decide whether aggregate fields are required (recommended) or optional-but-validated-when-present. Required is safer for the stated goal; optional-but-checked is more backward-compatible. The constraints allow breaking the schema only if tests/docs justify it; since all producers/consumers are in-repo and fixtures are test-owned, required is justified.
- Decide whether the validator must also reject *unknown* aggregate fields (e.g., a stray `pass_rate`) to keep the shape deterministic. Recommend yes, by adding a closed-field check inside `_live_two_repeat_evaluation_report`, matching the closed-field discipline already used in `capture_extract.py`.
- Add explicit cross-check assertions in the validator:
  - `task_count == len(per_task_attempts)`
  - `attempt_count == attempts_per_task * task_count`
  - `pass_count == sum(1 for row in per_task_attempts for a in row["attempts"] if a["pass"])`
- Update `extract_live_two_repeat_evaluation_report` to compute and emit the chosen aggregate fields from `per_task` before calling `_validated`.
- Update `_class_shaped_payloads` and `_fixture_paths` test helpers to include the new aggregate fields with values consistent with their per-task data.
- Add unit tests:
  - Validator accepts a well-formed aggregate-consistent payload.
  - Validator rejects aggregate count drift (task_count off by one, attempt_count not equal to `attempts_per_task * task_count`, pass_count off by one).
  - Validator rejects an unknown aggregate field if the closed-field decision is yes.
  - `capture_extract` CLI/extractor emits aggregate fields that round-trip through `artifact_shape_error_from_payload`.
- Doc update: add a short note under `docs/operations/benchmark_reproduction_requirements.json` `two_repeated_attempts.notes` (or a sibling schema doc) stating that the artifact carries aggregate task/attempt/pass counts that must reconcile with per-task rows. The requirements file itself does not need a schema_version bump.

## Revised Plan
Scope: add aggregate reconciliation to `live_two_repeat_evaluation_report` only.

1. Schema changes (`src/self_harness/_artifact_shapes.py`)
   - In `_live_two_repeat_evaluation_report`, after per-task validation, require and reconcile:
     - `task_count: int >= 1`, equals `len(per_task_attempts)`
     - `attempt_count: int`, equals `attempts_per_task * task_count`
     - `pass_count: int >= 0`, equals total true `pass` values across all attempts
     - (Optional but recommended) `fail_count: int >= 0`, equals `attempt_count - pass_count`
   - Reject any unknown top-level field on this artifact class to keep the shape deterministic (introduce a small allowed-field set locally, mirroring the closed-field discipline in `capture_extract.py`).
2. Extractor (`src/self_harness/capture_extract.py`)
   - In `extract_live_two_repeat_evaluation_report`, compute `task_count`, `attempt_count`, `pass_count`, `fail_count` from the built `per_task` list and include them in the payload passed to `_validated`.
3. Tests
   - `tests/test_reproduction_readiness.py::_class_shaped_payloads`: add the new aggregate fields, consistent with the existing two-task fixture.
   - `tests/test_capture_extract.py::_fixture_paths`: update the JSONL-backed fixture expectations so the extractor emits the new fields; add assertions that the extracted payload includes the new fields and that `artifact_shape_error_from_payload` accepts it.
   - New tests in `tests/test_capture_extract.py` or a focused `tests/test_artifact_shapes.py`:
     - Reject `task_count` drift.
     - Reject `attempt_count` not equal to `attempts_per_task * task_count`.
     - Reject `pass_count` drift.
     - Reject unknown aggregate field (if closed-field check is added).
4. Docs
   - `docs/operations/benchmark_reproduction_requirements.json`: append to `two_repeated_attempts.notes` a sentence that the artifact now carries aggregate task/attempt/pass counts that must reconcile with per-task rows; no schema_version bump.
5. Stop conditions
   - All existing tests pass after fixture updates.
   - New negative tests fail on pre-change code and pass after change.
   - `reproduction_bundle_build` / `capture_admit` end-to-end fixtures still produce verified-ready reports with the new fields.
   - No contact with Harbor/Docker/providers/PyPI/Sigstore/cloud (offline slice).
   - `reproduction_claimed` remains `false` everywhere.

## Remaining Open Questions
- Should the closed-field restriction be applied to `live_two_repeat_evaluation_report` only, or rolled out to all artifact classes in a separate slice? (Recommend: this slice only, to keep scope tight.)
- Should `pass_rate` be an emitted field or deliberately omitted to avoid implying reproduction metrics? (Recommend: omit; counts only, to stay consistent with the "do not mark reproduction claimed" boundary.)
- Is there an external consumer of these JSON artifacts outside this repository (e.g., a release dashboard) that would break on newly-required fields? The repo evidence shows none; confirm before marking CONVERGED: YES in round 2.

[usage] {"completion_tokens": 2009, "completion_tokens_details": {"reasoning_tokens": 487}, "prompt_tokens": 31440, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 33449}

---

# Round 2

CONVERGED: YES

## Verdict
The candidate plan is execution-ready. The schema gap is real and repository-confirmed, the producer/consumer surface is entirely in-tree, and all required code/test/doc touchpoints are identified. Open design questions from round 1 can now be locked by architect decision because they are scope/discipline choices, not missing evidence; the "external consumer" question is non-blocking because the repo exposes no external artifact contract and the slice cannot be blocked on a hypothetical outside consumer.

## Critique
- Evidence: `_live_two_repeat_evaluation_report` in `src/self_harness/_artifact_shapes.py` validates `attempts_per_task == 2`, non-duplicate `task_id`s, two attempts per task, and boolean `pass` values, but performs no aggregate reconciliation. A payload with per-task rows consistent but aggregate counts missing or wrong would pass.
- Evidence: `extract_live_two_repeat_evaluation_report` in `src/self_harness/capture_extract.py` emits `schema_version`, `ok`, `mode`, `attempts_per_task`, `per_task_attempts`, `capture_run_id`, `reproduction_claimed`, `boundary` — no aggregate counts.
- Evidence: `tests/test_reproduction_readiness.py::_class_shaped_payloads` ships a `live_two_repeat_evaluation_report` fixture with no aggregate fields (2 tasks, 2 attempts each → expected `task_count=2`, `attempt_count=4`, `pass_count=3`, `fail_count=1`).
- Evidence: `tests/test_capture_extract.py::_fixture_paths` produces an attempts JSONL with task-a (1 pass/1 fail) and task-b (2 passes); once the extractor emits aggregate fields, the existing CLI/round-trip test will also cover them.
- Evidence: `capture_admit.py` and `reproduction_readiness.py` consume artifacts only through `artifact_shape_error` / `artifact_shape_error_from_payload`, so they require no code changes; only fixtures that build shaped payloads need updates.
- Inference: The artifact's only producer is the in-tree extractor; the only consumers are the in-tree bundle/readiness/shape verifiers. Adding required aggregate fields is therefore backward-safe within this repository, justified by the absence of any external producer/consumer contract.
- Inference: No external consumer is discoverable from the repository; the offline boundary statement (`CAPTURE_EXTRACT_BOUNDARY`) and the requirements catalog frame these artifacts as internal evidence. Treating this as backward-safe is the correct architect call.

## Required Changes
1. Schema (`src/self_harness/_artifact_shapes.py::_live_two_repeat_evaluation_report`)
   - Require and reconcile, after per-task validation:
     - `task_count: int >= 1`, equals `len(per_task_attempts)`
     - `attempt_count: int`, equals `attempts_per_task * task_count`
     - `pass_count: int >= 0`, equals number of attempts with `pass is True`
     - `fail_count: int >= 0`, equals `attempt_count - pass_count`
   - Add a closed-field check on the top-level object for this artifact class only. Allowed top-level fields:
     `schema_version, ok, mode, attempts_per_task, per_task_attempts, task_count, attempt_count, pass_count, fail_count, capture_run_id, reproduction_claimed, boundary`
   - Do not introduce `pass_rate`; counts only, consistent with the no-reproduction-claim boundary.
2. Extractor (`src/self_harness/capture_extract.py::extract_live_two_repeat_evaluation_report`)
   - Compute `task_count`, `attempt_count`, `pass_count`, `fail_count` from the built `per_task` list and include them in the payload passed to `_validated`.
3. Tests
   - Update `tests/test_reproduction_readiness.py::_class_shaped_payloads` `live_two_repeat_evaluation_report` entry to include `task_count=2`, `attempt_count=4`, `pass_count=3`, `fail_count=1`.
   - Update `tests/test_capture_extract.py::_fixture_paths` so the extractor-emitted fixture expectations include the four aggregate fields consistent with the JSONL data (`task_count=2`, `attempt_count=4`, `pass_count=3`, `fail_count=1`), and assert round-trip acceptance via `artifact_shape_error_from_payload`.
   - Add focused negative tests (new file `tests/test_artifact_shapes.py` or appended to `tests/test_capture_extract.py`):
     - reject `task_count` drift (off by one),
     - reject `attempt_count != attempts_per_task * task_count`,
     - reject `pass_count` drift,
     - reject `fail_count != attempt_count - pass_count`,
     - reject unknown aggregate field (e.g., `pass_rate`),
     - accept a well-formed aggregate-consistent payload.
4. Docs
   - Append to `docs/operations/benchmark_reproduction_requirements.json` `two_repeated_attempts.notes` a sentence: "The artifact carries aggregate `task_count`, `attempt_count`, `pass_count`, and `fail_count` that must reconcile with `per_task_attempts`." No `schema_version` bump in the catalog.
5. Stop conditions
   - All existing tests pass after fixture updates.
   - New negative tests fail on pre-change code and pass after change.
   - `reproduction_bundle_build` / `capture_admit` end-to-end fixtures still produce verified-ready reports with the new fields.
   - No contact with Harbor/Docker/model providers/PyPI/Sigstore/cloud.
   - `reproduction_claimed` remains `false` everywhere; `pass_rate` is never emitted.

## Revised Plan
Scope: aggregate reconciliation on `live_two_repeat_evaluation_report` only; no other artifact class touched.

1. `src/self_harness/_artifact_shapes.py`
   - In `_live_two_repeat_evaluation_report`, after per-task validation, assert the four aggregate invariants above and add a closed-field check using a locally-scoped `frozenset` (mirrors the discipline already in `capture_extract.py`).
2. `src/self_harness/capture_extract.py`
   - In `extract_live_two_repeat_evaluation_report`, after building `per_task`, compute the four counts and inject them into the `_validated(...)` payload. No change to input validation or boundary.
3. Tests as listed in Required Changes 3.
4. Docs as listed in Required Changes 4.
5. Verify stop conditions via the existing test suite plus the new negative tests.

## Remaining Open Questions
- Closed-field rollout to other artifact classes: deferred to a separate slice; locked to this class only here.
- `pass_rate`: deliberately omitted; counts only.
- External artifact consumer: none visible in the repository and none implied by the offline boundary; non-blocking. If an out-of-tree consumer is later introduced, a `schema_version` bump + optional-field migration would be the correct follow-up, out of scope for this slice.

CONVERGED: YES

[usage] {"completion_tokens": 1778, "completion_tokens_details": {"reasoning_tokens": 332}, "prompt_tokens": 34510, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 36288}
