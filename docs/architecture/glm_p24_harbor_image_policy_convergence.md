# Round 1

CONVERGED: YES

## Verdict
The P24 Harbor/Terminal-Bench image policy enforcement plan is execution-ready against the supplied repository evidence. The existing `ImagePolicy` module, `HarborRunner` live path, and `parse_harbor_output` already expose the required hooks (`container_digest`), and the CLI already demonstrates the fail-closed exit-code-2 pattern for `container-demo`. The plan reuses P23 policy primitives, adds no audit schema change, preserves dry-run determinism by gating only on operator-supplied material before any Harbor invocation, and fails closed in live mode by raising `ImagePolicyError` from the runner and catching it in the CLI.

## Critique
- Evidence shows `HarborRunResult.container_digest` is parsed from live Harbor output, satisfying the digest source requirement.
- Evidence shows `ImagePolicy.evaluate_image_policy(policy, image, digest, require_digest=...)` already implements allow/deny, digest pinning, and `sha256:<64 hex>` validation; no new policy logic is needed.
- Evidence shows Harbor output does not expose an image name; therefore name-bound policy evaluation requires an operator-supplied `--trust-container-image`, while digest-only pinning can work without it.
- Evidence shows the CLI already returns structured JSON with exit code 2 for `ImagePolicyError` in `container-demo`; the same pattern applies to `terminal-bench`.
- Dry-run determinism is preserved because no Harbor process runs; policy is only used as a pre-flight schema/name gate.
- Audit schema is untouched: image policy is operator-held runtime material; rejections surface as CLI-level structured JSON and a halted run, not as new audit fields.

## Required Changes
1. Add CLI flags to `terminal-bench`:
   - `--image-policy PATH` (optional)
   - `--trust-container-image NAME` (optional; required when `--image-policy` is set)
   - `--trust-container-image-digest DIGEST` (optional; direct digest pin without a policy file)
   - `--require-image-digest` (flag)
2. In `_run_terminal_bench`, before preflight/engine:
   - Load policy via `load_image_policy` when path supplied.
   - Validate `trusted_digest` with `validate_image_digest` when supplied.
   - If `--image-policy` is set, require `--trust-container-image` and run `evaluate_image_policy(policy, trusted_image, trusted_digest, require_digest=require_image_digest)`. On deny, print structured JSON and return 2.
   - If `--require-image-digest` is set and neither policy nor `--trust-container-image-digest` supplies a digest, fail closed with exit 2.
3. Extend `HarborRunner` with fields:
   - `image_policy: ImagePolicy | None = None`
   - `trusted_image: str | None = None`
   - `trusted_image_digest: str | None = None`
   - `require_image_digest: bool = False`
4. In `HarborRunner._live_run`, after `parse_harbor_output`, evaluate trust:
   - If `trusted_image_digest` is set: compare to `parsed.container_digest`; mismatch → raise `ImagePolicyError(decision)` with code `digest-mismatch`.
   - If `image_policy` is set: call `evaluate_image_policy(image_policy, trusted_image, parsed.container_digest, require_digest=require_image_digest)`. On deny, raise `ImagePolicyError(decision)`.
   - If `require_image_digest` and `parsed.container_digest` is None → raise `ImagePolicyError` with code `missing-digest`.
5. In `_run_terminal_bench`, wrap `engine.run()` in `try/except ImagePolicyError`; on catch, print structured JSON (`{"ok": false, "reason": "invalid-verifier", "message": ...}`) and return 2. This fails closed on the first violating task without requiring audit post-scan.
6. Dry-run path (`_dry_run`) performs no digest verification; the pre-engine CLI gate already validates policy schema and name binding deterministically.
7. Tests:
   - Pre-gate: invalid policy JSON, unsupported version, missing `--trust-container-image` with `--image-policy`, retired/revoked entry, digest-pin mismatch at gate → exit 2 before rounds.
   - Dry-run with active policy entry → exit 0, audit unchanged.
   - Live fake-harbor returning matching digest → pass.
   - Live fake-harbor returning mismatched digest → raises `ImagePolicyError`, CLI exits 2, no `rounds/` written.
   - Live fake-harbor omitting digest with `--require-image-digest` → exit 2.
   - Dry-run determinism: repeated runs produce identical audit bytes.
8. Docs:
   - Add `docs/architecture/p24_harbor_image_policy_brief.md`.
   - Update `docs/architecture/harbor_protocol_assumptions.md` to note image policy enforcement is operator-supplied and live-only for digest verification; Harbor output still lacks image name.

## Revised Plan
- Trust boundary: only CLI flags select policy, trusted image, trusted digest, and require-digest behavior. Corpus JSON and Harbor output cannot influence these.
- Validation timing:
  - Construction/pre-engine: policy schema, name binding, digest format, require-digest preconditions.
  - Per live task, post-Harbor: parsed `container_digest` verified against policy entry or pinned digest; failure halts the run via `ImagePolicyError`.
- Exit codes:
  - Preflight failure → 2 (existing).
  - Pre-engine policy rejection → 2 with structured JSON.
  - Live per-task policy rejection → 2 with structured JSON; no further rounds.
  - Normal completion → 0.
- Stop conditions: any trust-boundary violation stops execution immediately and emits exit code 2.
- Audit impact: none. No schema field added; policy files are not persisted into corpus/manifest/audit.

## Remaining Open Questions
- None blocking. Implementation may choose to additionally record the rejection reason in `RunRecord.metadata` before raising, but since the CLI halts on first violation and emits structured JSON, this is optional polish rather than a convergence requirement.

[usage] {"completion_tokens": 4561, "completion_tokens_details": {"reasoning_tokens": 3261}, "prompt_tokens": 25749, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 30310}
