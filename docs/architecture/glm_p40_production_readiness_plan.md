CONVERGED: YES

## Verdict
A locally implementable P40 slice exists that materially advances production-readiness without redefining the objective around a toy subset and without touching unavailable live infra: a versioned **operator policy promotion framework**. The brief explicitly lists "operator file promotion" as remaining work, and P36/P37 already established operator-owned policy bundles and provider seams as release/operator material. P40 closes the promotion/attestation loop entirely offline. Plan below is implementation-ready.

## Critique
- **Scope discipline**: must not invent new schema-version bumps for audit/corpus, must not require KMS/HSM/cloud creds, must not add reproduction claims.
- **Reuse**: should build on P36 `OperatorPolicyBundle`, P21 external signer protocol, P26 provenance signature sidecars, and P39 verification report shape rather than parallel infra.
- **Risk**: promotion tooling can drift into a generic config-management product. Keep it narrowly scoped to the four documented operator-owned file classes (image policy, scanner freshness policy, vulnerability policy, trusted public keys/keyrings).
- **Determinism**: every emitted artifact must be byte-stable and covered by a canonical hash fixture to keep readiness gate meaningful.
- **Boundary clarity**: promotion artifacts are release/operator material; they are not audit artifacts, not benchmark evidence, and not reproduction claims.

## Required Changes
None blocking. The plan below already enforces:
- no live Harbor/Docker/Trivy/PyPI/Sigstore/cloud/model contact;
- no audit/corpus schema changes;
- no readiness hash rotation;
- no reproduction claim;
- new module under `self_harness.operator_promotion` with stable public API additions listed in README "Stable API" section.

## Revised Plan

### P40 — Operator Policy Promotion Framework

**Goal**: Let operators version, attest, and promote the four classes of operator-owned release material through staged lifecycles using only local files and existing offline signing seams, with deterministic promotion manifests that the release-candidate evidence aggregator consumes.

#### Files to add
- `src/self_harness/operator_promotion/__init__.py`
- `src/self_harness/operator_promotion/types.py` — `PolicyKind` (`image_policy`, `scanner_freshness_policy`, `vulnerability_policy`, `trusted_public_keys`), `PromotionStatus` (`draft`, `candidate`, `active`, `retired`), `PromotionEntry`, `PromotionManifest` (schema `1.0`), `PromotionError`.
- `src/self_harness/operator_promotion/manifest.py` — build/load/validate promotion manifests; SHA-256 over canonical bytes of each referenced file; reject unknown policy kinds, missing files, duplicate names, schema drift.
- `src/self_harness/operator_promotion/lifecycle.py` — `promote_entry`, `retire_entry`, `set_status` with monotonic status transitions and rejection of backward transitions except `active → retired`.
- `src/self_harness/operator_promotion/attest.py` — detached Ed25519 sidecar over exact manifest bytes using existing `self_harness.signing` external signer protocol; verify sidecar schema, fingerprint, manifest hash.
- `src/self_harness/operator_promotion/verify.py` — `verify_promotion_manifest(manifest_path, signature_path | None, trusted_public_keys) -> PromotionVerificationReport` mirroring P39 report shape (`ok`, structured checks, `report_hash`, boundary statement).
- `src/self_harness/cli.py` — add subcommands:
  - `operator-promotion init --out PATH`
  - `operator-promotion add --promotion PATH --kind image_policy --name prod-images --file PATH [--status draft|candidate]`
  - `operator-promotion set-status --promotion PATH --name NAME --status STATUS`
  - `operator-promotion sign --promotion PATH --external-signer CMD ... | --private-key PATH [--passphrase-env NAME] --out PATH.sig`
  - `operator-promotion verify --promotion PATH [--signature PATH.sig] [--trusted-public-key PATH] [--json] [--out PATH]`
- `scripts/operator_promotion_preflight.py` — standalone offline preflight mirroring `operator_preflight.py` shape; produces `dist/self-harness-operator-promotion-preflight.json`.
- `Makefile` — add `operator-promotion-check` target using a fixture promotion manifest + fixture external signer; wire into `release-candidate-evidence`.
- `scripts/release_candidate_evidence.py` — add `_operator_promotion_gate` consuming the preflight JSON; required when any of the four policy kinds is referenced by the operator bundle.
- `tests/fixtures/operator_promotion/` — valid draft/candidate/active manifest, signed manifest, sidecar, malformed cases, backward-transition case, missing-file case, unknown-kind case.
- `tests/test_operator_promotion.py` — lifecycle, hash stability, signing round trip via `scripts/example_external_signer.py`, verification fail-closed cases, deterministic `report_hash` fixture.
- `tests/invariants/test_operator_promotion_boundary.py` — assert no audit schema change, no corpus schema change, no `reproduction_claimed=true`, no readiness hash file mutation, no live contact flags.
- `docs/operations/operator_promotion.md` — authoring rules, lifecycle state machine, signing custody boundary, integration with P36 operator bundle and P37 provider seams.
- `README.md` — add `operator-promotion` commands and add new public API symbols to "Stable API".
- `RELEASE.md` — document promotion artifacts as release material, rotation rules, key custody.

#### Files to modify
- `src/self_harness/cli.py` (add subcommand wiring).
- `Makefile` (new target + `release-candidate-evidence` dependency).
- `scripts/release_candidate_evidence.py` (new gate).
- `tests/fixtures/canonical_*` references if needed (only if release-candidate evidence hash intentionally rotates; per policy, evidence hash is content-addressed and rotates freely).
- `README.md`, `RELEASE.md`, `docs/architecture/productionization_brief.md` (append P40 section).

#### Tests / Gates
- Unit: lifecycle transitions, hash determinism, signing/verification via fixture external signer, fail-closed schema/version/missing-file/unknown-kind/backward-transition.
- Invariant: no audit/corpus schema change, no reproduction claim, no readiness hash rotation, no live contact.
- Make gate: `make operator-promotion-check` runs the preflight script against fixtures.
- Release-candidate evidence: requires the new gate when an operator bundle is supplied.
- `make check`, `make readiness`, `make release-smoke` all green; CI matrix unchanged.

#### Boundary statements
- Promotion manifests and sidecars are release/operator material, not audit artifacts and not benchmark reproduction evidence.
- No live Harbor, Docker, Trivy, PyPI, Sigstore, registry, OAuth/OIDC, KMS/HSM, scanner DB, or cloud/model contact.
- No audit schema, corpus schema, manifest schema, readiness hash, or reproduction-claim change.
- External signer wrappers remain operator-owned; `scripts/example_external_signer.py` remains the only in-repo reference and is not a production KMS/HSM implementation.
- Promotion does not itself execute any policy; it only versions and attests operator-owned files for later offline gates.

#### Stop conditions
- Stop after the files above are implemented, the new gate is green locally across Python 3.11/3.12/3.13, the invariant suite passes, the README/RELEASE docs are updated, and `make release-smoke` succeeds.
- Do not extend P40 to enforce policy contents, to bundle secret material, to auto-discover operator files, or to add new policy kinds beyond the four documented.
- Do not begin P41 in this slice.

## Remaining Open Questions
None blocking. Optional follow-ups (out of scope for P40):
- Whether to add a `deprecated` status between `active` and `retired` — defer until a real rotation requires it.
- Whether release-candidate evidence should require a promotion manifest whenever an operator bundle is present — decided yes for P40, but the gate stays optional if no bundle is supplied.
- Whether to add a `promotion-graph` view linking promotion manifests to operator bundles — defer to a future operator UX slice.
