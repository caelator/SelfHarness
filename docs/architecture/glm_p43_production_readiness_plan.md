CONVERGED: YES

## Verdict

The environment cannot exercise Harbor/Trivy/Sigstore/Docker-daemon/PyPI live paths, so the only honest P43 slices are those that advance the listed "remaining production work" via offline-structural or contract-seam work. The strongest such slice is an **offline Sigstore/PyPI attestation verification contract with a structural pre-validation gate**, because: (a) it is locally implementable (the `cryptography` dependency already exists for provenance), (b) it materially advances the explicitly-listed "Sigstore/PyPI attestations" gap, (c) it mirrors the already-accepted optional-extra + mock-transport pattern used by the Anthropic LLM adapter, and (d) it preserves every paper-fidelity/reproduction/audit invariant because it never claims cryptographic validity on the offline path and never contacts live trust roots.

## Critique

- Evidence (from repo): `cryptography` is available via the `provenance` extra; the Anthropic adapter already establishes the optional-extra + mock-contract-test pattern (`.github/workflows/ci.yml` `anthropic-contract`, `tests/adapters/llm`); `RELEASE.md` explicitly states "Sigstore and PyPI attestations remain separate future trust boundaries"; `release_candidate_evidence.py` is the additive gate-aggregation point.
- Inference: structural parsing of a Sigstore bundle (cert chain presence, SAN/issuer extraction, signature/tlog field presence) and a PyPI attestation envelope (materials + claim + embedded bundle) is implementable today with `cryptography.x509` without the `sigstore` package. Real cryptographic verification must remain behind an optional `sigstore` extra and be contract-tested without the library present, exactly as the Anthropic adapter is.
- Risk if misframed: an "offline Sigstore verifier" could be mistaken for live/cryptographic trust. The plan therefore introduces a `StructuralAttestationVerifier` whose report sets `cryptographic_valid=None`, and a separate `SigstorePythonVerifier` (optional) that is the only path permitted to set `cryptographic_valid=true`.
- The slice is additive: no audit/corpus/manifest schema change, no readiness hash rotation, no reproduction-claim change, no new required dependency, no live network.

## Required Changes

(none blocking — this is the initial converged plan; the changes below are the agreed scope to implement)

1. Add `src/self_harness/attestations.py` with: `AttestationTrustRoot`, `SigstoreBundle`, `PyPIAttestation`, `AttestationVerificationReport`, `AttestationVerifierBackend` protocol, `StructuralAttestationVerifier` (offline, `cryptographic_valid=None`), and optional `SigstorePythonVerifier` behind a `sigstore` extra.
2. Add operator CLI `self-harness verify-attestation` and `scripts/verify_attestation.py` (operator preflight helper) supporting `--backend structural|sigstore`.
3. Add `make attestation-check` deterministic offline gate over checked-in synthetic bundle/trust-root fixtures and the built wheel/provenance manifest as the verified *material*.
4. Wire an **optional** `attestation` gate into `scripts/release_candidate_evidence.py` (non-blocking when absent; blocking only when a supplied report is malformed, failed, or claims reproduction); schema remains `1.0` via the additive `gates[]` extension point.
5. CI: add `attestation-structural` job (always runs) and `sigstore-contract` job (mock-transport, no library required), mirroring `anthropic-contract`.
6. Docs: `docs/operations/attestations.md`, README "Attestations" section, RELEASE.md note that structural pre-validation is release/operator material and that cryptographic validity requires the optional `sigstore` extra plus operator-supplied trust root.

## Revised Plan

**P43 — Offline attestation verification contract and structural pre-validation gate**

Source scope
- `src/self_harness/attestations.py`
  - `AttestationTrustRoot` (operator file paths only): expected issuer URL, expected Fulcio root/intermediate cert paths, expected Rekor public-key path, allowed SAN/identity patterns.
  - `SigstoreBundle`: parse provided Sigstore bundle JSON (cert chain, signature bytes, Rekor tlog inclusion entry) using `cryptography.x509`.
  - `PyPIAttestation`: parse PyPI attestation envelope (`_type`, `materials`, `claim`, embedded Sigstore bundle).
  - `AttestationVerificationReport`: deterministic, stable JSON via `stable_json_dumps`; per-check statuses; explicit `cryptographic_valid: true | false | null`; `reproduction_claimed=false`; `report_sha256`; explicit boundary string.
  - `AttestationVerifierBackend` protocol (`verify(bundle, material_digest, trust_root) -> bool`).
  - `StructuralAttestationVerifier`: checks structural validity, materials digest match, signature presence, cert-chain presence, SAN/issuer/identity vs trust-root expectations, tlog entry presence; **sets `cryptographic_valid=None`**.
  - `SigstorePythonVerifier` (optional, behind `sigstore` extra): wraps `sigstore.verify` against the operator-supplied trust root; only path allowed to set `cryptographic_valid=True`.

- `scripts/verify_attestation.py`: operator preflight helper writing structured report.
- CLI: `self-harness verify-attestation --bundle <path> --material <path> --trust-root <path> --backend {structural|sigstore} [--out <path>]`.

Tests scope
- `tests/test_attestations_structural.py`: parse synthetic fixture Sigstore bundle and PyPI attestation; assert pass path and fail-closed cases: tampered material digest, missing signature, missing cert chain, wrong SAN, wrong issuer, missing tlog entry, malformed JSON, trust-root missing-file. Deterministic expected `report_sha256`.
- `tests/adapters/attestations/test_sigstore_contract.py`: mock-transport contract test for `SigstorePythonVerifier` (no `sigstore` package required), mirroring `tests/adapters/llm` Anthropic pattern.
- Extend `tests/invariants` with a no-reproduction-claim invariant for attestation reports.

Docs scope
- `docs/operations/attestations.md`: boundary language (offline structural pre-validation vs optional cryptographic verification; operator-supplied trust roots; no live Fulcio/Rekor/PyPI contact by this tool; not benchmark reproduction evidence).
- README: "Attestations" section with structural-only example and sigstore-extra example.
- RELEASE.md: attestation evidence is optional release/operator material; structural check is non-authoritative for cryptographic trust.
- Stable API additions enumerated in README "Stable API".

Make/CI scope
- Makefile: `attestation-check` runs `build` + `provenance`, then `scripts/verify_attestation.py --backend structural` over a fixture bundle/trust-root against the built wheel and provenance manifest.
- `release-candidate-evidence`: accept optional `--attestation-result`; include as `attestation` gate only when supplied.
- `.github/workflows/ci.yml`: add `attestation-structural` job (always, Python 3.11/3.12/3.13) and `sigstore-contract` job (mock, no library).

Boundaries / invariants preserved
- No live Fulcio, Rekor, PyPI, Sigstore CLI, registry, Harbor, Docker, or model contact.
- No audit schema, corpus schema, manifest schema, readiness hash, canonical-hash fixture, or reproduction-claim change.
- `StructuralAttestationVerifier` must never set `cryptographic_valid=True`.
- No trust-root material embedded in artifacts; only operator file paths.

Stop conditions
- Structural verifier, contracts, CLI, `scripts/verify_attestation.py`, `make attestation-check`, CI jobs, docs, and tests merged and green on 3.11/3.12/3.13.
- `SigstorePythonVerifier` contract test passes without the `sigstore` package installed.
- `make attestation-check` produces a deterministic report hash.
- Release-candidate evidence accepts (optional) attestation evidence without blocking on its absence.

Verification commands
- `make attestation-check`
- `python scripts/verify_attestation.py --bundle tests/fixtures/attestations/sigstore_bundle.json --material "$(ls dist/*.whl)" --trust-root tests/fixtures/attestations/trust_root.json --backend structural --out dist/self-harness-attestation.json`
- `python -m pytest -q tests/test_attestations_structural.py tests/adapters/attestations tests/invariants`
- `make check`

## Remaining Open Questions

(non-blocking)
- Whether operators want a bundled default Fulcio/Rekor trust-root file in `examples/` (decision deferred; plan uses operator-supplied paths only, which is the safer default).
- Whether the optional `sigstore` extra should pin `sigstore-python` to a specific major version (deferred to implementation; contract test will document the supported API surface).
- Whether future P44 should add an offline *Rekor inclusion-proof* structural validator independent of `sigstore-python` (out of P43 scope; flagged for follow-up).
