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
