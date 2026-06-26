# Round 1

CONVERGED: NO

## Verdict
An executable plan is reachable using existing repository primitives (transport-injected paper model clients, the preflight-surface framework, and the fail-closed reproduction readiness contract). The abstract architecture is clear, but several concrete decisions need confirmation before the plan is ready to execute: (a) whether to introduce a new `model_backend_preflight` surface vs. extend `operator_preflight`, (b) the exact artifact class semantics, and (c) hash-rotation policy details. The plan below is decision-ready but still has one non-blocking ambiguity that warrants a reviewer decision.

## Critique

Evidence (validated against the repository):
- `src/self_harness/adapters/llm/paper_models.py` already injects a `ChatCompletionTransport` and raises `LLMClientError` when no transport is supplied. This is the seam a preflight command should exercise.
- `src/self_harness/readiness_matrix.py` already validates `preflight_surface` against `ALLOWED_READINESS_PREFLIGHT_SURFACES = {"harbor_discovery_check","none","operator_preflight","release_smoke","scanner_check"}` and stores per-entry `operator_action`.
- `src/self_harness/readiness_drift.py` already accepts operator-supplied surface results, already fails entries that are `provisioned` + `reproduction_relevant` without a passing surface, and already rejects any input that contains `reproduction_claimed: true`.
- `docs/operations/benchmark_reproduction_requirements.json` already binds the three paper backends to the `model_backend_preflight_report` artifact class and to the readiness-matrix rows.
- `docs/operations/readiness_matrix.json` currently lists all three model rows as `blocked`, `preflight_surface: "none"`, `reproduction_relevant: true`.

Inferences (not yet repository-validated):
- A new CLI script `scripts/model_backend_preflight.py` is the cleanest location; mirrors `scripts/operator_preflight.py` and `scripts/harbor_discovery.py`.
- The command must support `--live` and `--replay/--dry-run` modes, and must default to a no-contact mode to preserve the package's no-live-contact invariant.
- The artifact written should be a plain JSON report with `reproduction_claimed: false` and a `boundary` field, matching the existing pattern.
- Tests should cover: live mode that actually exercises the transport; replay mode that reads a captured chat completion; dry-run mode that emits an "ok:false not-run" report; failure paths (missing credentials, transport error, unexpected `reproduction_claimed:true` in replay data).

Risks:
1. **Surface naming collision.** Adding a brand-new `model_backend_preflight` surface requires updating `ALLOWED_READINESS_PREFLIGHT_SURFACES`, `KNOWN_READINESS_AFFECTS`, and the readiness-drift surface-result mapping. Reusing `operator_preflight` avoids schema churn but conflates operator bundle preflight with model backend preflight. → Needs reviewer decision.
2. **Readiness drift completeness.** If the readiness matrix keeps the three model rows as `blocked`, drift will fail them. If they are flipped to `provisioned` *without* a passing surface result, drift will fail them for missing surface coverage. The plan must specify the precise status policy: rows must be marked `provisioned` only after a live `model_backend_preflight_report` artifact exists; otherwise they remain `blocked` and drift reports them as advisory.
3. **Hash rotation.** Any new surface added to the drift report changes `report_hash`. Rotation policy must state: the readiness-drift schema_version stays at "1.0"; canonical hashes in `tests/fixtures/canonical_*.txt` must be regenerated exactly once after the surface is added; the report must be reproducible from checked-in fixtures.
4. **Live-contact invariant.** `make readiness-drift-check` and `make reproduction-readiness-check` must NOT call the live model backend command by default; only an explicit operator-invoked target should. This mirrors how `harbor-discovery-check` uses `--dry-run` in CI and reserves live mode for operators.

## Required Changes
1. Decide: introduce `model_backend_preflight` as a new surface (preferred for traceability) vs. extend `operator_preflight`. My recommendation: new surface, because the artifact class `model_backend_preflight_report` is already referenced in the reproduction requirements catalog.
2. Specify the operator-invoked Make target, e.g. `model-backend-preflight`, that supports `MODEL_BACKEND_PREFLIGHT_MODE=live|replay|dry-run` and writes `dist/self-harness-model-backend-preflight.json`. The default mode must be `dry-run`.
3. Specify how `readiness-drift-check` ingests the report only when the artifact exists; missing artifact must not break CI. Mirror the existing pattern where optional surface results are `None`.
4. Specify hash-rotation: which fixtures are authoritative, when hashes are regenerated, and how reproduction readiness guarantees `reproduction_claimed=false`.

## Revised Plan

### 1. Schema and allowed-surface updates
- Add `model_backend_preflight` to `ALLOWED_READINESS_PREFLIGHT_SURFACES` in `readiness_matrix.py`.
- Add `scripts/model_backend_preflight.py` to `KNOWN_READINESS_AFFECTS` as `"scripts/model_backend_preflight.py"` (and as `"scripts/model_backend_preflight.py live"` if live mode is gated by a CLI flag).
- Extend `evaluate_readiness_drift` in `readiness_drift.py` to accept `model_backend_preflight_result: Mapping[str, object] | None = None` and include it in `surface_results`.

### 2. New operator-invoked command
- `scripts/model_backend_preflight.py`:
  - Args: `--mode {live,replay,dry-run}` (default `dry-run`), `--replay PATH`, `--backend {minimax,qwen,glm,all}` (default `all`), `--out PATH`, `--today DATE`.
  - Live mode: build transports from env (`MINIMAX_API_KEY`, `MINIMAX_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `QWEN_SGLANG_BASE_URL`); invoke each client with a tiny prompt; capture usage.
  - Replay mode: load captured chat-completion fixtures from `tests/fixtures/model_backend/` and replay through the client to validate response parsing.
  - Dry-run mode: emit `ok:false`, `mode:dry-run`, `checks:[{name:"backend_reachable",status:"not-run"}]` per backend.
  - Always emit `reproduction_claimed:false` and a boundary string. Reject any replay fixture containing `reproduction_claimed:true`.
  - Exit 0 on live success, 2 on dry-run/non-live `ok:false`, 3 on corrupt input.

### 3. Readiness matrix update
- Update `docs/operations/readiness_matrix.json` for the three model rows: set `preflight_surface: "model_backend_preflight"`. Keep `status: "blocked"` by default; operators flip to `"provisioned"` only after producing a live `dist/self-harness-model-backend-preflight.json`.
- Keep `reproduction_relevant: true`.

### 4. Drift and reproduction wiring
- In `Makefile`, add:
  ```make
  model-backend-preflight:
      $(PYTHON) scripts/model_backend_preflight.py --mode $${MODEL_BACKEND_PREFLIGHT_MODE:-dry-run} --out dist/self-harness-model-backend-preflight.json
  ```
- `readiness-drift-check` passes `--model-backend-preflight-result dist/self-harness-model-backend-preflight.json` only when the file exists (use a shell guard). Missing artifact → surface result is `None` → drift marks provisioned model rows as fail, which is correct.
- `reproduction-readiness-check` remains fail-closed. No change to `reproduction_readiness.py` is required because the artifact class `model_backend_preflight_report` is already expected, and the loader already rejects any artifact containing `reproduction_claimed:true`.

### 5. Tests
- Unit: `tests/scripts/test_model_backend_preflight.py` covering dry-run shape, replay parsing, live path via injected fake transport, failure when transport raises, and rejection of replay fixtures claiming reproduction.
- Invariant: `tests/invariants/test_readiness_drift_model_backend.py` covering (a) provisioned model row with passing surface → pass; (b) provisioned model row with missing surface → fail; (c) blocked model row → advisory; (d) surface claiming reproduction → fail.
- Contract: extend `tests/adapters/llm/test_*_client_contract.py` to assert that the client raises cleanly without a transport (already true) and that `on_usage` is invoked when the transport returns usage data.

### 6. Fixtures and hash rotation
- Add `tests/fixtures/model_backend/{minimax,qwen,glm}_chat_completion_replay.json` captured from a transport mock (not from a live provider). Each must contain `reproduction_claimed:false`.
- Regenerate canonical hashes in `tests/fixtures/canonical_*.txt` once, in the same PR that lands the schema additions.
- Document rotation policy in `docs/operations/benchmark_reproduction_readiness.md`: readiness hashes are regenerated only when a declared surface set or readiness catalog schema changes; live evidence does not rotate hashes because live artifacts are never checked in.

### 7. Docs
- New `docs/operations/model_backend_preflight.md` describing modes, env vars, exit codes, and the rule that reproduction_claimed stays false until a separate live benchmark reproduction artifact exists.
- Update `docs/operations/benchmark_reproduction_readiness.md` with the new surface and the operator flow.

## Remaining Open Questions
1. Should `model_backend_preflight` be a first-class preflight surface (my recommendation), or should it be embedded under `operator_preflight` to avoid schema changes? This is a single decision and does not require new evidence.
2. For the Qwen backend, which has `credential_env=None` and uses an operator-provisioned SGLang endpoint, should the live mode require an explicit `QWEN_SGLANG_BASE_URL` check, or also a readiness probe of the SGLang `/v1/models` endpoint? Recommendation: require only the base URL plus a single chat completion, to avoid broad dependencies.
3. Should the dry-run report set `ok:false` (forcing operators to opt in) or `ok:true` with `not-run` checks (allowing CI to pass)? Recommendation: `ok:false` to keep drift honest; CI only runs drift without this surface result, so advisory rows stay advisory.

[usage] {"completion_tokens": 2678, "completion_tokens_details": {"reasoning_tokens": 411}, "prompt_tokens": 31466, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 34144}

---

# Round 2

CONVERGED: YES

## Verdict
The candidate plan is executable, repo-grounded, and preserves all required invariants (no-live-contact defaults, operator-only live mode, fail-closed reproduction readiness with `reproduction_claimed=false`, narrow dependency surface). The three open questions from round 1 are decidable now without new evidence, and I am resolving them below. No further reviewer loop is required before implementation.

## Critique
Evidence (validated against the repository):
- `src/self_harness/adapters/llm/paper_models.py` exposes the `ChatCompletionTransport` seam and fail-closes without a transport via `LLMClientError`. This is exactly the seam a live preflight must exercise.
- `src/self_harness/readiness_matrix.py` enforces `ALLOWED_READINESS_PREFLIGHT_SURFACES` and `KNOWN_READINESS_AFFECTS`; both must be extended for the new surface and script, otherwise catalog validation fails closed.
- `src/self_harness/readiness_drift.py` already (i) accepts operator-supplied surface results, (ii) fails provisioned+reproduction-relevant rows lacking a passing surface, (iii) rejects any input carrying `reproduction_claimed: true`, and (iv) keeps `reproduction_claimed: false` in its report. The new surface only requires a new keyword argument plus an entry in `surface_results`.
- `docs/operations/benchmark_reproduction_requirements.json` already binds the three paper model rows to `required_artifact_class: "model_backend_preflight_report"`, so the artifact class is contractually expected downstream.
- `docs/operations/readiness_matrix.json` currently lists all three model rows as `status: "blocked"`, `preflight_surface: "none"`, `reproduction_relevant: true`, matching the plan's "blocked by default, provisioned only after live artifact" policy.
- `scripts/reproduction_readiness_report.py` indexes artifacts by stem from `--artifact-dir`, so placing `model_backend_preflight_report.json` under a scanned directory (or passing `--artifact model_backend_preflight_report=PATH`) is sufficient; no schema change to reproduction readiness is required.

Inferences:
- A new `scripts/model_backend_preflight.py` mirroring `scripts/operator_preflight.py` and `scripts/harbor_discovery.py` is the lowest-churn location and matches existing conventions (argparse, stable JSON output, boundary string, exit code policy).
- The Makefile wiring should follow the harbor_discovery_check pattern: dry-run by default in CI, live mode reserved for an explicit operator target.

Risks (resolved or bounded):
1. **Surface naming collision** — resolved: introduce `model_backend_preflight` as a first-class surface. The reproduction-requirements catalog already names the artifact class this way, so reusing `operator_preflight` would force aliasing and lose traceability.
2. **Hash rotation** — bounded: `READINESS_DRIFT_SCHEMA_VERSION` stays "1.0"; canonical hashes in `tests/fixtures/canonical_*.txt` are regenerated exactly once in the landing PR; live artifacts are never checked in and do not affect hashes.
3. **No-live-contact invariant** — bounded: default mode is `dry-run`; `make readiness-drift-check` does not invoke `model-backend-preflight` by default; only an explicit `model-backend-preflight` target runs it, and `reproduction-readiness-check` ingests the artifact only when present.

## Required Changes
1. **Resolve Open Question 1 (new surface):** Introduce `model_backend_preflight` as a first-class surface. Add it to `ALLOWED_READINESS_PREFLIGHT_SURFACES`. Add `"scripts/model_backend_preflight.py"` to `KNOWN_READINESS_AFFECTS` (the dry-run/replay form is the CI-safe entry; live mode is still gated by CLI flag, so no separate `live` affects entry is required, mirroring `scripts/harbor_discovery.py`).
2. **Resolve Open Question 2 (Qwen live probe):** Live mode for Qwen requires `QWEN_SGLANG_BASE_URL` to be set and issues a single tiny chat completion through the client. Do NOT add a `/v1/models` probe; that would broaden dependencies beyond the package's chat-completion transport contract.
3. **Resolve Open Question 3 (dry-run semantics):** Dry-run emits `ok: false` with per-backend `status: "not-run"`. This keeps drift honest: missing surface result = advisory if row is still `blocked`, fail if `provisioned`.
4. **Drift wiring:** Extend `evaluate_readiness_drift` with `model_backend_preflight_result: Mapping[str, object] | None = None` and include it in `surface_results`. Update `scripts/readiness_drift_report.py` to accept `--model-backend-preflight-result` (guarded so missing file ⇒ `None`, preserving CI behavior).
5. **Make targets:** Add operator-only `model-backend-preflight` (default `MODEL_BACKEND_PREFLIGHT_MODE=dry-run`). Do NOT add it to the `readiness-drift-check` dependency chain; drift ingests the artifact only when present, mirroring how optional operator material is handled.
6. **Hash rotation policy doc:** State explicitly in `docs/operations/benchmark_reproduction_readiness.md` that readiness hashes are rotated only when the declared surface set or readiness catalog schema changes, never in response to live evidence, and that checked-in fixtures must always produce reproducible hashes.

## Revised Plan
### 1. Schema and allowed-surface updates
- Add `"model_backend_preflight"` to `ALLOWED_READINESS_PREFLIGHT_SURFACES` in `readiness_matrix.py`.
- Add `"scripts/model_backend_preflight.py"` to `KNOWN_READINESS_AFFECTS`.
- Extend `evaluate_readiness_drift(...)` in `readiness_drift.py` with `model_backend_preflight_result: Mapping[str, object] | None = None` and include it in `surface_results`.

### 2. New operator-invoked command
`scripts/model_backend_preflight.py`:
- Args: `--mode {live,replay,dry-run}` (default `dry-run`), `--replay PATH`, `--backend {minimax,qwen,glm,all}` (default `all`), `--out PATH`, `--today DATE`.
- Live: build transports from env (`MINIMAX_API_KEY`, `MINIMAX_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `QWEN_SGLANG_BASE_URL`); issue one tiny chat completion per backend through the existing client; record usage via `on_usage`.
- Replay: load fixtures under `tests/fixtures/model_backend/`, replay through the client to validate response parsing.
- Dry-run: emit `ok: false`, per-backend `checks: [{"name": "backend_reachable", "required": true, "status": "not-run"}]`.
- Always emit `reproduction_claimed: false` plus boundary string. Reject any replay fixture containing `reproduction_claimed: true`.
- Exit codes: `0` live success; `2` dry-run/replay `ok:false`; `3` corrupt input.

### 3. Readiness matrix update
- For the three model rows in `docs/operations/readiness_matrix.json`: set `preflight_surface: "model_backend_preflight"`. Keep `status: "blocked"`; operators flip to `"provisioned"` only after producing a live `dist/self-harness-model-backend-preflight.json`. Keep `reproduction_relevant: true`.

### 4. Drift and reproduction wiring
- Add to `Makefile`:
  ```make
  model-backend-preflight:
      $(PYTHON) scripts/model_backend_preflight.py --mode $${MODEL_BACKEND_PREFLIGHT_MODE:-dry-run} --out dist/self-harness-model-backend-preflight.json
  ```
- `scripts/readiness_drift_report.py` gains `--model-backend-preflight-result PATH` (optional; missing file ⇒ `None`). `readiness-drift-check` is unchanged and does NOT invoke the new target; if operators want their result included, they run `make model-backend-preflight` first.
- `reproduction-readiness-check` ingests the artifact when present (via existing `--artifact-dir` or `--artifact model_backend_preflight_report=dist/self-harness-model-backend-preflight.json`). Loader already rejects any artifact with `reproduction_claimed: true`.

### 5. Tests
- Unit `tests/scripts/test_model_backend_preflight.py`: dry-run shape; replay parsing; live path via injected fake transport; failure when transport raises; rejection of replay fixtures claiming reproduction.
- Invariant `tests/invariants/test_readiness_drift_model_backend.py`: (a) provisioned + passing surface ⇒ pass; (b) provisioned + missing surface ⇒ fail; (c) blocked ⇒ advisory; (d) surface claiming reproduction ⇒ fail.
- Extend `tests/adapters/llm/test_*_client_contract.py`: assert clean failure without transport; assert `on_usage` fires when transport returns usage.

### 6. Fixtures and hash rotation
- Add `tests/fixtures/model_backend/{minimax,qwen,glm}_chat_completion_replay.json` captured from a transport mock (not from a live provider); each must contain `reproduction_claimed: false`.
- Regenerate `tests/fixtures/canonical_*.txt` exactly once in the landing PR.
- Document the rotation policy in `docs/operations/benchmark_reproduction_readiness.md`: hashes rotate only on declared-surface or catalog-schema changes; live evidence never rotates hashes because live artifacts are never checked in.

### 7. Docs
- New `docs/operations/model_backend_preflight.md`: modes, env vars, exit codes, the rule that `reproduction_claimed` stays `false` until a separate live benchmark reproduction artifact exists.
- Update `docs/operations/benchmark_reproduction_readiness.md` with the new surface and operator flow.

## Remaining Open Questions
None blocking. All three round-1 questions resolved by this architect decision:
1. New first-class surface `model_backend_preflight` (chosen).
2. Qwen live probe = base URL + one chat completion only; no `/v1/models` probe (chosen).
3. Dry-run `ok: false` with `not-run` checks (chosen).

[usage] {"completion_tokens": 2354, "completion_tokens_details": {"reasoning_tokens": 174}, "prompt_tokens": 36027, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 38381}
