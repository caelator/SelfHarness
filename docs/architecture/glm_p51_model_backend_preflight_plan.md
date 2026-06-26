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
