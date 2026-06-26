CONVERGED: YES

## Verdict
Round 1 left four open questions; all four can be decided by the brief plus existing precedent (P16 keyring semantics, P22 operator-held runtime material rule) without further repository evidence. With those resolutions the plan is execution-ready: the changes are additive, the trust boundary is unchanged, no schema is touched, and the deterministic dry-run hash can be preserved by fixture-scoping. Marking CONVERGED: YES.

## Critique
- Evidence: `ContainerVerifierTaskAdapter`/`Runner` already expose `image`/`image_digest` as operator fields and already reject corpus-supplied image/digest/auth/secret keys; the only missing piece is the allowlist gate.
- Evidence: live mode already supports preflight-then-exit before engine rounds (`run_container_preflight` + `return 2`); the policy gate belongs in the same early slot.
- Inference: enforcing the gate in `__post_init__` of the adapter/runner makes both dry-run and live fail closed before any engine round, satisfying the brief's "live should fail before engine rounds" without weakening dry-run determinism.
- Inference: the digest grammar should be the same one already used in the codebase (`sha256:<hex>` appears in tests), so a strict validator does not introduce a new external dependency.
- Risk mitigated: the canonical dry-run hash is preserved by gating against the existing fixture image (allowed), and rejected-image tests assert no `rounds/` directory is created.

## Required Changes
Resolved round 1 questions (all by inference + precedent, none blocking):
1. P23 is scoped strictly to `container-demo`. Harbor/Terminal-Bench image policy is a later slice.
2. Labels are informational only; they may appear in in-memory `RunRecord.trace` but are not written to audit JSONL (consistent with P22 operator-held material rule).
3. Digest grammar is `sha256:<64 lowercase hex>` only; revisitable only at a major version bump.
4. An empty policy file is valid and behaves as deny-all (supports lockdown operators).

## Revised Plan
Files, tests, docs, gates, and non-goals as specified in round 1, with the following clarifications:
- `evaluate_image_policy` treats an empty policy as deny-all: any non-empty image yields `missing-policy` (i.e., not allowlisted).
- Adapter `__post_init__` raises `ContainerVerifierError` for any non-`allowed` decision, before the runner is ever constructed, so both dry-run and live exit 2 with the existing JSON error payload and never produce `rounds/`.
- CLI loads the policy file once in `_run_container_demo` before constructing the adapter; the loaded `ImagePolicy` object is passed in (path string never stored on the adapter).
- No new trace kind is required for labels; if surfaced, labels ride on the existing `container-command` trace metadata and are not persisted to audit JSONL.
- `make readiness` canonical hash for the container dry-run path must be unchanged when the policy is omitted or when it allows the fixture image. A second hash variant for `--image-policy <allowing>` is optional; if added, it must be a new fixture file, not a replacement.

## Remaining Open Questions
None blocking. Optional follow-ups for implementation engineering judgment:
- Whether to render a structured preflight `image-policy` check line in live `preflight.json` (not required by the brief; the brief only requires failing before engine rounds, which adapter construction already guarantees).
- Whether to emit a stable `image-policy-decision` in in-memory trace metadata; recommended but implementation-detail, not architectural.
