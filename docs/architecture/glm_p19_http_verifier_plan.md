CONVERGED: YES

## Verdict
The P19 slice is well-scoped, follows the established P18 pattern, and has a clean architectural seam. The HTTP adapter is a natural sibling to the in-process Python adapter: same `TaskAdapter`/`Runner` protocols, same `VerifierResult`-shaped structured outcomes, same closed `FailureCategory` mapping, same per-attempt workspace discipline. No audit schema change is required, no new dependencies are required, and the trust boundary (operator-supplied URL, corpus carries only opaque `verifier_selector`) is clear. The plan below is execution-ready; remaining open questions are non-blocking and can be resolved during implementation.

## Critique
Evidence (from repository):
- `TaskAdapter` protocol (`load` + `runner`) and `Runner.run(task, harness, attempt_index) -> RunRecord` are the established seams.
- `InProcessPythonRunner` shows the exact pattern: fresh per-attempt workdir, optional setup hook, closed `FailureCategory` mapping with fail-closed on unknown categories, exceptions mapped to `ENVIRONMENT_ERROR`.
- `FailureCategory` enum is closed: `VERIFIER_PASS`, `VERIFIER_FAIL`, `TIMEOUT`, `MISSING_ARTIFACT`, `ASSERTION_FAIL`, `ENVIRONMENT_ERROR`.
- `verifier_selector` is validated only as an opaque string up to 256 chars; corpus JSON never selects executable behavior.
- CLI pattern: `python-demo CORPUS --trust-verifier-module PATH ...` with signature/keyring gates.
- Audit schema is currently `1.2`/`1.3`; P18 added no schema change.

Alternatives critique:
- **Container verifier adapter**: higher-value but out of scope for a stdlib-only, no-external-service slice. Correctly deferred. P19 should not preclude a future container adapter; the `verifier_selector` opacity is preserved.
- **Subprocess JSON output**: already covered by `LocalSubprocessRunner` and lower-fidelity than structured HTTP responses; no reason to duplicate.
- **KMS/HSM**: orthogonal key-management work; explicitly deferred in the brief. P19 should not touch signing code paths.
- **Harbor live run**: reproduction surface; out of scope and rejected by existing invariants.
- **Migration shims**: not needed; no schema change.

Risks addressed by the plan:
- **Network determinism in tests**: use `http.server` (stdlib) bound to ephemeral localhost ports in fixtures; no real network; no default network access in the package.
- **Timeout discipline**: hard `socket`/`urllib` timeout per attempt; map to `FailureCategory.TIMEOUT`.
- **Response validation**: strict JSON schema validation with fail-closed `InProcessVerifierError`-equivalent (`HttpVerifierError`) for malformed/invalid responses; preserves audit semantics.
- **URL trust**: operator-supplied `--trust-verifier-url`; corpus metadata must not carry URLs.
- **Schema stability**: no audit schema bump; HTTP-specific trace metadata stays in-memory on `RunRecord.trace` like P18.

## Required Changes
None blocking. Implementation guidance (for the executing slice):
1. Use `urllib.request` with a `socket._GLOBAL_DEFAULT_TIMEOUT`-equivalent explicit `timeout` argument; do not introduce `requests` or any third-party HTTP client.
2. Reuse the exact `VerifierResult`/normalization logic shape from `in_process_python.py` (consider extracting a shared `_normalize_verifier_result` helper in a new `adapters/_verifier_result.py` to avoid drift — optional but recommended).
3. Request body must be deterministic JSON (`stable_json_dumps`) so per-attempt requests with the same task+attempt hash identically; include `task_id`, `split`, `attempt_index`, `verifier_selector`, and `workdir` path.
4. Response must be validated as the same `VerifierResult` shape; reject unknown `failure_category` values with a typed exception.
5. Test fixture: a tiny `http.server.BaseHTTPRequestHandler` subclass running on `127.0.0.1:0` (ephemeral) threaded inside the test; record no network calls outside localhost.

## Revised Plan
**P19 — Trusted HTTP verifier adapter (stdlib-only)**

Scope:
- New module `src/self_harness/adapters/http_verifier.py` exporting `HttpVerifierTaskAdapter` and `HttpVerifierRunner`, plus `HttpVerifierError`.
- New CLI subcommand `http-demo` mirroring `python-demo`.
- No audit schema change. No new dependencies. No default network access.

API:
```python
@dataclass(frozen=True)
class HttpVerifierTaskAdapter(TaskAdapter):
    verifier_url: str
    timeout_seconds: float = 30.0
    keep_workdir: bool = False
    extra_headers: tuple[tuple[str, str], ...] = ()  # operator-supplied only

    def load(self, corpus: TaskCorpus) -> list[Task]: ...
    def runner(self) -> HttpVerifierRunner: ...

@dataclass(frozen=True)
class HttpVerifierRunner:
    verifier_url: str
    timeout_seconds: float
    keep_workdir: bool
    extra_headers: tuple[tuple[str, str], ...]

    def run(self, task, harness, attempt_index=0) -> RunRecord: ...
```

Request schema (deterministic JSON, POST, `Content-Type: application/json`):
```json
{
  "task_id": "...",
  "split": "held_in|held_out",
  "attempt_index": 0,
  "verifier_selector": "opaque-string-or-null",
  "workdir": "/abs/path",
  "task_metadata": { ... opaque, no URL, no executable code ... }
}
```

Response schema (strict; same shape as `VerifierResult`):
```json
{
  "passed": true|false,
  "failure_category": "verifier-fail|timeout|missing-artifact|assertion-fail|environment-error|null",
  "mechanism": "http-verifier",
  "message": "..."
}
```

Outcome mapping:
- HTTP success + valid JSON + known category → `VerifierOutcome` mirroring P18.
- HTTP non-2xx → `ENVIRONMENT_ERROR` / `http-status-error`.
- Timeout / `URLError` / `socket.timeout` → `TIMEOUT`.
- Malformed JSON / invalid shape / unknown category → raise `HttpVerifierError` (fail-closed), engine surfaces as verifier error (consistent with P18's `InProcessVerifierError` handling).
- Transport exceptions during setup-template copy are unchanged from P18.

Workspace discipline:
- Fresh `tempfile.mkdtemp(prefix=f"self-harness-http-{task.id}-{attempt_index}-")` per attempt.
- `workspace_template` copy reused from existing shared helper.
- Cleanup in `finally` unless `--keep-workdir`.

CLI:
```
self-harness http-demo CORPUS \
  --trust-verifier-url http://127.0.0.1:8080/verify \
  [--timeout-seconds 30] \
  [--keep-workdir] \
  [--header "Authorization: Bearer ..." (repeatable)] \
  [standard --rounds/--seed/--out/--evaluation-repeats/--max-proposals/--max-payload-bytes] \
  [--require-corpus-signature PATH | --require-corpus-keyring PATH]
```
- `--trust-verifier-url` required.
- Mutual-exclusive trust group identical to `python-demo`.
- EngineConfig `model_id="http-verifier"`, no `benchmark_protocol`, `reproduction_claimed=false`.
- Output prints "This is not a benchmark reproduction." like other demos.

Corpus metadata:
- `verifier_selector` opaque string, same validation as P18 (non-empty, ≤256 chars).
- Explicit rejection of `verifier_url` / `url` / any executable field in corpus JSON at load time (`TaskLoadError`).

Tests (local deterministic, no network):
- `tests/test_http_verifier.py`:
  - stdlib `http.server.HTTPServer(("127.0.0.1", 0), ...)` with handler returning canned `{passed: true}` / `{passed: false, failure_category: "assertion-fail"}`.
  - Pass/fail outcome mapping, including mechanism `http-verifier`.
  - Fresh workdir per attempt (mirror P18 test).
  - Timeout mapping via handler `time.sleep` + `--timeout-seconds 0.1`.
  - Fail-closed on unknown `failure_category`.
  - Fail-closed on malformed JSON body.
  - Non-2xx → `ENVIRONMENT_ERROR` / `http-status-error`.
  - Selector validation.
  - Deterministic audit tree across two runs (canonical hash fixture).
- CLI test for `http-demo` requiring `--trust-verifier-url` and running end-to-end against the in-test server.
- Readiness hash fixture: `tests/fixtures/canonical_http_audit_hash.txt` added; readiness gate extended to cover the new audit shape.

Docs:
- `docs/architecture/p19_http_verifier_brief.md` mirroring P18 brief: status, purpose, implemented, trust boundary, deferred (container adapter, auth/mTLS, KMS, async), schema (no audit change).
- README: new `http-demo` section; note stdlib-only, no default network, operator-supplied URL trust boundary, opaque `verifier_selector`.
- `docs/architecture/productionization_brief.md`: add P19 entry under Implemented.

Stop conditions:
- All tests green under `make check` and `make readiness`.
- Canonical HTTP audit hash fixture committed and validated.
- No audit schema version bump.
- No new runtime dependency in `pyproject.toml`.

Out of scope:
- Container-backed verifier adapter.
- mTLS, OAuth, bearer-token refresh, KMS-backed secrets.
- Async verifier protocol.
- Verifier registry / plugin discovery.
- Any reproduction claim.
- Corpus-carried URLs or executable code.

## Remaining Open Questions
1. **Shared `_normalize_verifier_result` extraction**: optional refactor to dedupe P18/P19 normalization. Non-blocking; can defer to a follow-up. Recommendation: extract now to prevent drift.
2. **Request `task_metadata` passthrough**: include full opaque `task.metadata` or only `verifier_selector`? Recommendation: include full metadata dict (already validated to be JSON-serializable corpus data) so HTTP verifiers can operate, but document that the harness does not interpret it. Non-blocking.
3. **Default `timeout_seconds` value**: 30s (matches local subprocess default) vs 10s (tighter for HTTP). Non-blocking; recommend 30s for parity.
4. **Per-request retry/backoff**: explicitly out of scope; verifier errors are terminal per attempt. Confirm acceptable. Non-blocking.

These can be decided during implementation without another architect round.
