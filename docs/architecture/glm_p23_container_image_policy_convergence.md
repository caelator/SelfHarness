# Round 1

CONVERGED: NO

## Verdict
An initial architecture plan is feasible from the brief plus the P20/P22 evidence. The trust model, corpus guardrails, preflight-before-engine pattern, and "no schema change" constraint are already established, so the image policy slice is a natural extension. However, one design decision is material enough to require explicit confirmation before execution: whether the policy gate should also bind the dry-run path (not just live), since the brief says "live mode should be able to fail before engine rounds" but also says "dry-run remains daemon-free and deterministic" — these can coexist, but the strictness semantics need a decision.

## Critique
- Evidence: `ContainerVerifierTaskAdapter`/`ContainerVerifierRunner` already accept `image` and `image_digest` as operator-supplied values and already reject corpus metadata carrying image/digest/registry/auth/secret keys (`DISALLOWED_CONTAINER_METADATA_KEYS` plus prefix/suffix heurics in `_disallowed_metadata_keys`). The remaining gap is purely that there is no allowlist/policy gate comparing the operator's chosen image+digest against an operator-supplied policy file.
- Evidence: live mode already has a preflight-then-exit-before-engine pattern in `_run_container_demo` (`run_container_preflight` → `write_preflight_report` → return 2 if not passed). The image-policy gate can plug into the same pre-exit slot.
- Evidence: no audit schema change is consistent with P22's "operator-held runtime material not written to signed corpora/manifests/audit" rule.
- Inference: a new `ImagePolicy` dataclass + JSON loader + `validate(image, digest, require_digest)` predicate is the smallest change that satisfies the brief. It should be a pure stdlib JSON parse (no network).
- Risk: policy enforcement that only runs in live mode would leave dry-run as an unenforced trust surface (e.g., a dry-run audit could be produced with an image that the operator's policy would have rejected). The brief leans toward "live mode should fail before engine rounds" but does not explicitly forbid dry-run enforcement. Safer default: enforce in both modes, but make the failure behavior mode-appropriate.
- Risk: status/label semantics. The brief says "statuses/labels" but does not enumerate statuses. Without an enumerated status set, two operators could write incompatible policies. Recommend a small closed enum (`active`/`retired`/`revoked`), mirroring the corpus keyring model in P16.
- Risk: digest format. `--require-image-digest` must define the accepted digest grammar (`sha256:<64 hex>`). Current code accepts a free-form `image_digest` string. The plan should validate format when `--require-image-digest` is set or when a policy entry pins a digest.

## Required Changes
Before converging, confirm or reject the following decisions:
1. Enforce `--image-policy` in **both** dry-run and live modes (with the same fail-closed behavior), not only live. This keeps dry-run deterministic and prevents producing audit artifacts for non-allowlisted images.
2. Use a closed status enum `{active, retired, revoked}` with only `active` entries permitted for execution (mirrors P16 keyring semantics).
3. Make `--require-image-digest` an independent flag: when set, execution fails if the operator did not supply a digest, regardless of whether a policy entry exists. Policy entries may also mandate digests.
4. Treat the policy file as operator-held runtime material: never written to corpus, manifest, or audit JSON. Policy path and outcome may appear only in `preflight.json` (live) and in-process trace metadata that is already not persisted to audit JSONL.

## Revised Plan

**Files**
- `src/self_harness/image_policy.py` (new)
  - `ImagePolicyEntry` dataclass: `image: str`, `digest: str | None`, `status: Literal["active","retired","revoked"]`, `labels: tuple[str,...]`.
  - `ImagePolicy` dataclass: `policy_version: Literal["1"]`, `entries: tuple[ImagePolicyEntry,...]`.
  - `load_image_policy(path: Path) -> ImagePolicy` — pure stdlib JSON parse, deterministic key ordering, reject duplicates of `(image, digest)`.
  - `empty_image_policy() -> ImagePolicy`.
  - `evaluate_image_policy(policy, image, digest, *, require_digest) -> ImagePolicyDecision` — returns a closed decision (`allowed`, `missing-policy`, `missing-digest`, `digest-mismatch`, `not-active`).
  - `ImagePolicyError(Exception)` with structured `.decision` and `.message`.
  - Digest format validation: `sha256:<64 lowercase hex>` when present or required.
- `src/self_harness/adapters/container_verifier.py`
  - Add `image_policy: ImagePolicy | None` and `require_image_digest: bool` fields to `ContainerVerifierTaskAdapter` and `ContainerVerifierRunner`.
  - In `ContainerVerifierRunner.__post_init__`, evaluate the policy against `self.image`/`self.image_digest` and raise `ContainerVerifierError` on any non-`allowed` decision. This enforces in both dry-run and live modes at adapter construction time, before any engine round.
  - No change to `build_container_run_command`; digest pinning already works.
- `src/self_harness/cli.py`
  - Add `container-demo` flags: `--image-policy PATH` and `--require-image-digest` (store_true).
  - In `_run_container_demo`, load the policy file (if provided) before constructing the adapter, so the failure path mirrors the existing `ContainerVerifierError` handling and returns exit code 2 with a JSON payload.
  - Do not add the policy path or digest to `EngineConfig.benchmark_metadata` or to any audit artifact. Keep it operator-held.
- `docs/architecture/p23_image_policy_brief.md` (new) — mirror P20/P22 brief shape: status, purpose, implemented, trust boundary, deferred, schema (no audit schema change).

**Tests**
- `tests/test_image_policy.py` (new)
  - Loading: valid policy, malformed JSON, duplicate `(image,digest)`, unknown status, missing version.
  - Decisions: `allowed`, `missing-policy` (when policy provided but image absent), `missing-digest` (under `require_image_digest`), `digest-mismatch`, `not-active` (retired/revoked).
  - Digest format validation: reject non-`sha256:` and bad hex.
- Extend `tests/test_container_verifier.py`
  - Dry-run with `--image-policy` that allows the trusted image: succeeds and audit bytes are deterministic (reuses existing canonical hash fixture path concept).
  - Dry-run with policy that omits the image: CLI exits 2, no `rounds/` directory produced.
  - Live path: policy rejection happens before the Docker preflight subprocess is invoked (assert via fake-docker argv file absence).
  - `--require-image-digest` without a digest: exits 2 in both modes.
  - Corpus metadata still cannot supply policy entries or digests (extend disallowed-keys assertion).

**Docs**
- Update `docs/architecture/productionization_brief.md` P23 section with the implemented bullet list after implementation.
- README: add `container-demo --image-policy`/`--require-image-digest` to the operator controls table; restate "not a benchmark reproduction".

**Gates**
- `make check` (ruff, mypy, pytest) must pass.
- `make readiness` canonical hash for the container dry-run path must remain stable when the policy allows the existing fixture image; add a second canonical-hash variant if needed for the policy-allowed path.
- No network, no real Docker daemon, no real registry in any test.

**Non-goals**
- No vulnerability scanning, SBOM gating, or sigstore/cosign integration.
- No provider-specific registry login or KMS/HSM integration.
- No async or distributed verifier execution.
- No audit schema version bump; policy inputs are not persisted.
- No Terminal-Bench/Harbor reproduction claim.

## Remaining Open Questions
1. Should `--image-policy` enforcement also apply to the experimental `terminal-bench` CLI's Harbor path, or is P23 strictly scoped to `container-demo`? (Inference: strictly `container-demo`; Harbor image policy is a later slice. Confirm.)
2. Should labels on policy entries be purely informational, or should they be wireable into `RunRecord.trace` metadata (which is not persisted to audit JSONL)? (Inference: purely informational + optional in-memory trace; no audit surface.)
3. Is `sha256` the only accepted digest algorithm for P23, or must we leave room for `sha512`/future algorithms? (Inference: `sha256` only for P23, with a closed validator; revisit at a major version bump.)
4. Should an empty policy file be valid (allow-list-of-zero, i.e., deny all) or rejected at load time? (Inference: valid and deny-all, to support lockdown operators. Confirm.)

[usage] {"completion_tokens": 2336, "completion_tokens_details": {"reasoning_tokens": 405}, "prompt_tokens": 24527, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 26863}

---

# Round 2

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

[usage] {"completion_tokens": 828, "completion_tokens_details": {"reasoning_tokens": 59}, "prompt_tokens": 28415, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 29243}
