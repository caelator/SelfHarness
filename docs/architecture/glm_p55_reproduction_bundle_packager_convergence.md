# Round 1

CONVERGED: NO

## Verdict
Initial architecture plan is directionally sound and within the established P0–P54 pattern (offline, deterministic, schema-versioned, fail-closed, non-reproduction). The slice is implementable locally without live services. Convergence is not yet reached because two schema/custody decisions must be pinned before execution, and the plan needs to explicitly separate the builder from the existing verifier to avoid drift.

## Critique
- **Evidence:** `src/self_harness/reproduction_bundle.py` currently only *loads and verifies* bundles. No first-party authoring/builder exists in the shown files. Inference: a builder is a legitimate gap.
- **Evidence:** Existing signing patterns are split across at least three custody models — corpus signing (`corpus_signing.py`, P15–P17), release provenance signing (P26, `sign_provenance.py`), and operator promotion signing (P40). The plan must pick one reuse strategy rather than inventing a fourth.
- **Evidence:** The verifier enforces `reproduction_claimed:false`, relative paths, no escapes, one entry per required class, class-specific shapes. The builder must produce exactly this shape or it will fail its own downstream verifier — strong argument for a shared internal model.
- **Risk:** If the builder recomputes schema constants independently, field-set drift between builder and verifier is likely. Mitigation: import `_BUNDLE_FIELDS`, `_ENTRY_FIELDS`, `_SOURCE_FIELDS`, schema version constants from `reproduction_bundle.py`.
- **Risk:** Determinism. `created_at`, `bundle_id`, `operator_label`, source metadata must be operator-supplied inputs — the builder must never inject `datetime.now()` or a random id.
- **Risk:** Signing key custody. Adding a fourth signing path (new PEM loader, new external signer protocol) would diverge from P26/P40 conventions. Reuse is strongly preferred.
- **Risk:** Shape validation timing. Builder should optionally validate class-specific shapes at build time (fail closed early) but must not relax the verifier's checks.
- **Scope discipline:** The slice must not add live artifact capture, Harbor/Docker contact, or any reproduction claim. It only packages operator-supplied paths.

## Required Changes
1. Pin the signing custody decision: reuse the P26 release-provenance signing helper / external-signer protocol (preferred, because P54 bundle signatures already mirror that sidecar schema) rather than corpus or operator-promotion signing.
2. Pin the CLI surface decision: implement as a `scripts/reproduction_bundle_build.py` plus optional `self-harness reproduction-bundle-build` thin wrapper, matching the existing `scripts/reproduction_bundle_verify.py` precedent — not a brand-new top-level CLI group.
3. Mandate shared schema constants and a shared `ReproductionBundleEntry`/`ReproductionBundle` dataclass round-trip between builder and verifier to prevent drift.
4. Forbid implicit timestamps and ids in the builder; require `--bundle-id`, `--operator-label`, `--created-at` as explicit operator inputs.
5. Add a round-trip test: build → verify → sign → verify-with-signature, all offline, deterministic hash fixture.
6. Add a Make target `reproduction-bundle-build` that consumes `ARTIFACT_DIR` with class-named files and emits `$(REPRODUCTION_BUNDLE)` plus optional `$(REPRODUCTION_BUNDLE_SIGNATURE)`.
7. Explicitly document the non-reproduction boundary in the new docs section and in the emitted manifest's `reproduction_claimed:false` field.

## Revised Plan
**P55 — Reproduction evidence bundle builder/signer (offline, operator-facing)**

Scope:
- Add first-party deterministic authoring of P54 reproduction bundle manifests from operator-supplied live artifact paths.
- Add optional detached Ed25519 signing reusing the P26 release-provenance signing custody path (local PEM, passphrase sources, or external signer).
- No live contact, no audit/corpus/manifest schema change, no readiness-hash rotation, no reproduction claim.

Deliverables:
1. `src/self_harness/reproduction_bundle_build.py`
   - `build_reproduction_bundle(entries, *, bundle_id, operator_label, created_at, requirements) -> ReproductionBundleDocument`
   - Reuses `ReproductionBundleEntry`, schema constants, and `artifact_shape_error` from `reproduction_bundle.py`.
   - Inputs: explicit mapping of `required_artifact_class -> Path`, plus required scalar metadata.
   - Computes `sha256`, `byte_size` deterministically; writes relative `path` rooted at the output bundle directory.
   - Optionally runs class-specific shape validation at build time (`--strict-shapes`, default on).
   - Always emits `reproduction_claimed:false` and rejects any operator input attempting to set it otherwise.
2. `scripts/reproduction_bundle_build.py`
   - Args: `--artifact-dir` (files named by class), repeated `--artifact CLASS=PATH`, `--bundle-id`, `--operator-label`, `--created-at`, `--requirements`, `--out`, `--strict-shapes/--no-strict-shapes`.
   - Writes deterministic JSON via `stable_json_dumps`.
3. Signing reuse
   - Extend or wrap `scripts/sign_provenance.py` semantics into a shared `scripts/sign_reproduction_bundle.py` (or a `--mode reproduction-bundle` flag on the existing signer) emitting the exact P54 sidecar schema.
   - Supports `--private-key`, `--passphrase*`, `--external-signer`, `--public-key`, `--key-id`, `--fingerprint`, `--provider`.
4. Make targets
   - `reproduction-bundle-build`: builds `$(REPRODUCTION_BUNDLE)` from `$(ARTIFACT_DIR)`.
   - `reproduction-bundle-sign`: signs into `$(REPRODUCTION_BUNDLE_SIGNATURE)`.
   - `reproduction-bundle-check`: build + verify + sign + verify-with-signature round-trip on fixture inputs; deterministic report hash fixture.
5. Tests
   - Determinism: two builds produce byte-identical manifests.
   - Round-trip: built manifest passes `verify_reproduction_bundle` with and without signature.
   - Fail-closed: missing artifact, absolute path, path escape, unknown class, duplicate class, empty file, malformed shape, `reproduction_claimed:true` injection, missing required scalars.
   - Signing custody parity: local PEM, passphrase-file, external-signer fixture subprocess (mirroring P21/P26 test pattern).
6. Docs
   - Update `docs/operations/benchmark_reproduction_readiness.md` with a "Building and Signing a Bundle" section.
   - State boundary: builder is offline, operator-owned, never claims reproduction, never contacts Harbor/Docker/registries/scanners/PyPI/Sigstore/model providers/cloud.

Non-goals (explicit):
- No live artifact capture.
- No new signing protocol or KMS/HSM implementation.
- No change to P54 verifier behavior, audit schema, corpus schema, manifest schema, canonical readiness hash, or reproduction-claim semantics.
- No CI execution of the builder against real live evidence; CI exercises fixture inputs only.

## Remaining Open Questions
1. Should the builder accept an operator-supplied `source` block per entry verbatim, or derive it from top-level `--provider/--captured-at/--operator-label` flags with per-entry overrides? *Non-blocking — recommend top-level defaults with per-entry override for parity with P54 source schema.*
2. Should `reproduction-bundle-sign` be a new script or a `--mode` flag on `sign_provenance.py`? *Non-blocking — recommend a sibling script to keep provenance and bundle custody auditable separately, but either is acceptable.*
3. Should `make reproduction-bundle-check` be added to the default `check` target or remain a standalone operator/CI gate? *Non-blocking — recommend standalone plus a dedicated CI job, matching P26/P40 precedent.*

[usage] {"completion_tokens": 2129, "completion_tokens_details": {"reasoning_tokens": 462}, "prompt_tokens": 22467, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 24596}

---

# Round 2

CONVERGED: YES

## Verdict
The revised P55 plan is execution-ready. The two previously blocking decisions (signing custody model and CLI surface) are now pinned with evidence-backed choices, scope is disciplined, the builder/verifier drift risk is mitigated through mandated schema/constant sharing, and remaining open questions are explicitly non-blocking. No further planning round is required.

## Critique
- **Evidence:** `src/self_harness/reproduction_bundle.py` is verifier-only (load + verify + signature check). The P54 sidecar signature schema (`manifest_sha256`, `manifest_filename`, `fingerprint`, `public_key_b64`, `signature_b64`, `provider`, `key_id`) is structurally identical to the P26 release-provenance sidecar intent, so reusing the P26 custody path is the correct, lowest-drift choice.
- **Evidence:** `scripts/reproduction_bundle_verify.py` already establishes the `scripts/...` + `--out` + exit-code-0/2/3 convention; the proposed `scripts/reproduction_bundle_build.py` is a clean sibling and avoids inventing a new CLI group.
- **Evidence:** `Makefile` already defines `ARTIFACT_DIR`, `REPRODUCTION_BUNDLE`, `REPRODUCTION_BUNDLE_SIGNATURE`, and downstream targets consume them, so adding `reproduction-bundle-build`/`reproduction-bundle-sign` is additive and non-disruptive.
- **Inference:** Builder-Verifier drift is the highest-value architectural risk; mandating shared imports of `_BUNDLE_FIELDS`/`_ENTRY_FIELDS`/`_SOURCE_FIELDS`/`ReproductionBundleEntry` and reusing `artifact_shape_error` resolves it.
- **Inference:** Determinism risk (implicit timestamps, random ids) is mitigated by explicit `--bundle-id`/`--created-at`/`--operator-label` inputs and `stable_json_dumps`.
- **Scope discipline:** Non-reproduction boundary, no live contact, no schema/readiness-hash rotation, and CI-fixture-only execution are all explicit.

## Architecture Risks
- Builder emits a manifest the verifier must accept byte-for-byte; any independent recomputation of schema constants or entry ordering would break the round-trip. Mitigated by shared schema module imports and a mandated build→verify→sign→verify-with-signature round-trip test with a deterministic hash fixture.
- A fourth signing custody path would fragment operator trust tooling. Resolved by reusing P26 release-provenance signing semantics (local PEM, passphrase sources, external signer) into a sibling script emitting the P54 sidecar schema.
- Operator could attempt to inject `reproduction_claimed:true` or per-entry `source` overrides. Resolved by hard-coded `false` emission and top-level source defaults with per-entry overrides (open question 1, non-blocking).
- Make target dependency ordering must not silently promote the bundle into default `check`/release path. Resolved by standalone targets plus explicit `release-candidate-evidence-reproduction` consumption (open question 3, non-blocking).

## Recommended Next Moves
1. Implement `src/self_harness/reproduction_bundle_build.py` importing shared constants/dataclasses and `artifact_shape_error` from `reproduction_bundle.py`.
2. Implement `scripts/reproduction_bundle_build.py` and a sibling `scripts/sign_reproduction_bundle.py` reusing P26 custody helpers, emitting the P54 sidecar schema.
3. Add `reproduction-bundle-build`, `reproduction-bundle-sign`, and `reproduction-bundle-check` Make targets; do not wire into default `check`.
4. Add determinism, round-trip, fail-closed, and signing-custody parity tests using fixtures only.
5. Extend `docs/operations/benchmark_reproduction_readiness.md` with a "Building and Signing a Bundle" section restating the offline, operator-owned, non-reproduction boundary.

## Blocking Decisions
None. Sign-off to proceed with implementation.

## Remaining Open Questions
1. Per-entry `source` verbatim vs top-level defaults with override — recommend top-level defaults with override (parity with P54 schema). Non-blocking.
2. Sibling signer script vs `--mode reproduction-bundle` on `sign_provenance.py` — recommend sibling script. Non-blocking.
3. Add `reproduction-bundle-check` to default `check` vs standalone CI gate — recommend standalone plus dedicated CI job, matching P26/P40. Non-blocking.

[usage] {"completion_tokens": 947, "completion_tokens_details": {"reasoning_tokens": 17}, "prompt_tokens": 25826, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 26773}
