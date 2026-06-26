# Round 1

CONVERGED: YES

## Verdict
P44 is bounded as: **land the offline cryptographic Sigstore bundle verification backend** behind the existing `sigstore` extra, replacing the current fail-closed stub with a real `sigstore-python` backed verifier that runs offline against the bundle's embedded certificate chain, inclusion proof, and operator-owned trust root. No live Fulcio/Rekor/PyPI/Sigstore/Harbor/Docker/registry/scanner/model/cloud contact; no audit/corpus schema change; `reproduction_claimed=false` preserved. This materially advances P43's structural pre-validation into a real cryptographic gate while staying inside the stated constraints.

## Critique
- **Evidence (from repo):** `src/self_harness/attestations.py` already defines `SigstorePythonVerifier` as the integration seam and fails closed today ("requires an explicit verifier callable in this release"). `release_candidate_evidence.py` already accepts an attestation report and blocks on `cryptographic_valid` only via metadata, not as a hard gate. The trust-root schema already carries `fulcio_certificate_paths` and `rekor_public_key_path`. The CLI/scripts/Makefile already expose `verify-attestation --backend sigstore`.
- **Evidence (from docs):** `docs/operations/attestations.md` explicitly states the structural backend "does not validate a Fulcio chain, Rekor inclusion proof, or signature over artifact bytes," and frames `SigstorePythonVerifier` as "the integration seam for a future cryptographic verifier."
- **Inference:** The natural next material step is to implement that seam. Real cryptographic verification of a Sigstore bundle is offline-verifiable: the bundle carries the certificate chain, signature, and transparency-log inclusion proof; verification reduces to checking the inclusion proof and cert chain against the supplied Fulcio roots and Rekor public key plus the signature over the material digest. No network is required when the bundle and trust root are local.
- **Risk 1 — test fixtures:** Generating a real Sigstore bundle in CI requires either a live Fulcio/Rekor round-trip (forbidden) or checked-in fixture material signed by a real CA the package cannot impersonate. We cannot honestly produce a passing real-crypto fixture without external signing. Mitigation: ship the backend with (a) injected-verifier contract tests that already exist, (b) new unit tests that mock `sigstore.verify`/`sigstore.models` interfaces to assert correct wiring, trust-root plumbing, and fail-closed behavior on missing/malformed bundles, and (c) an explicit operator-validation section in docs. Do not check in a fake "passing" real-crypto fixture.
- **Risk 2 — trust-root mismatch:** Sigstore-python's verifier expects a specific trust-root configuration shape (Fulcio certificates, Rekor public key, possibly CT logs). Our current `AttestationTrustRoot` stores paths but not the full sigstore policy. Mitigation: map our trust-root file into sigstore-python's `policy` type inside the backend; if the mapping is lossy, fail closed with a structured `AttestationError` rather than silently relaxing checks.
- **Risk 3 — optional dependency surface:** Importing `sigstore` at module top would break core import. Mitigation: keep imports inside `SigstorePythonVerifier.verify`, consistent with the current lazy `__import__("sigstore")` pattern; CI's `core-import` job already proves `import self_harness` works without extras.
- **Risk 4 — release-candidate evidence over-claiming:** Once crypto verification is real, the evidence aggregator must still not flip `reproduction_claimed`. Mitigation: keep the evidence gate semantics identical; `cryptographic_valid=true` is recorded as metadata, not as a reproduction claim.
- **Non-blocking concern:** The structural backend remains the default; `sigstore` is opt-in. Operators who want the crypto gate pass `--backend sigstore` and install the extra. This matches the existing documented boundary.

## Required Changes
None blocking. The plan below is executable as-is. The only deferred (non-blocking) item is producing an end-to-end real-crypto fixture; that is explicitly out of scope and documented as operator-validated.

## Revised Plan
**P44: Offline cryptographic Sigstore bundle verification backend**

1. **Implement the backend**
   - In `src/self_harness/attestations.py`, replace `SigstorePythonVerifier.verify`'s fail-closed body with a real implementation that:
     - lazily imports `sigstore` and raises a structured `AttestationError` if missing;
     - loads the bundle via `sigstore.models.Bundle` from the attestation file;
     - builds a `sigstore.verify.Verifier` (or `policy`) from the trust-root's Fulcio certificate paths and Rekor public key path;
     - verifies the bundle against `material_path` using the configured identity policy derived from `expected_certificate_issuer` and `allowed_subject_alternative_names`;
     - returns `True`/`False` (never `None`) and raises `AttestationError` only for configuration/IO problems, mapping verification failures to `False`.
   - Keep `StructuralAttestationVerifier` and the structural path unchanged; `--backend structural` stays default and continues to set `cryptographic_valid=null`.

2. **Trust-root mapping**
   - Add an internal `_sigstore_policy_from_trust_root(trust_root)` helper that converts `AttestationTrustRoot` into sigstore-python's policy objects.
   - Fail closed with `AttestationError` if any required trust-root file is unreadable or if sigstore-python rejects the policy shape.

3. **CLI / script / Makefile**
   - No CLI flag changes needed; `--backend sigstore` already routes to `SigstorePythonVerifier`.
   - Add a Makefile note (docs only) that `make attestation-check` continues to use the structural backend; operators with the `sigstore` extra and a real bundle run `self-harness verify-attestation --backend sigstore` separately.
   - Do not add `sigstore` to the default release-smoke dependency set; keep it opt-in.

4. **Tests (all offline)**
   - Unit tests with mocked `sigstore.verify`/`sigstore.models` interfaces:
     - successful verification returns `True` and the report's `cryptographic_valid` becomes `True`;
     - verification failure returns `False` and the check records `cryptographic_valid=False` with `ok=False`;
     - missing `sigstore` extra raises `AttestationError` and the check records a structured failure;
     - malformed bundle, missing trust-root file, and unsupported policy mapping each produce structured `AttestationError` failures;
     - `reproduction_claimed` remains `False` in all crypto-backend reports.
   - Contract test with an injected verifier callable (already supported) continues to pass and is reused to assert the report schema is unchanged.
   - Add an invariant test asserting the core package imports without the `sigstore` extra and that `SigstorePythonVerifier` raises `AttestationError` on use without the extra.

5. **Docs**
   - Update `docs/operations/attestations.md` with:
     - the cryptographic backend now works offline when the `sigstore` extra is installed and an operator-supplied bundle plus trust root are provided;
     - explicit operator-validation requirement for end-to-end real-crypto validation (no in-repo fixture claims real crypto);
     - unchanged `reproduction_claimed=false` boundary;
     - version boundary note in `docs/architecture/schema_changelog.md` (no audit/corpus schema change; only attestation backend behavior).

6. **CI**
   - Add a dedicated `sigstore-crypto-backend` job (Python 3.11/3.12/3.13) that installs `.[dev,provenance,sigstore]` and runs the new mocked-crypto unit tests plus the injected-verifier contract tests.
   - Do not add a real-crypto end-to-end job.

7. **Release-candidate evidence**
   - No aggregator change. `cryptographic_valid` remains metadata; the decision boundary is unchanged. Document that operators may replace the structural report in `--attestation-result` with a `--backend sigstore` report when promoting.

8. **Out of scope (explicit)**
   - No live Fulcio/Rekor/PyPI contact.
   - No in-repo real-crypto passing fixture.
   - No change to audit schema, corpus schema, manifest schema, readiness hash, or reproduction-claim semantics.
   - No KMS/HSM/OAuth/registry provider implementations (deferred).

## Remaining Open Questions
- **Trust-root policy shape compatibility:** whether sigstore-python's current stable API accepts our minimal trust-root mapping directly or requires additional fields (e.g., CT log public keys). Resolved at implementation time by failing closed; non-blocking because the structural backend remains the default and the seam is opt-in.
- **Sigstore-python version pinning:** whether to constrain the `sigstore` extra to a minimum version that supports offline bundle verification with our trust-root shape. Resolved by adding a lower-bound pin in `pyproject.toml` and noting it in docs; non-blocking.
- **Future real-crypto fixture:** deferred to an operator-validation run; not required for this slice to ship.

[usage] {"completion_tokens": 3764, "completion_tokens_details": {"reasoning_tokens": 1797}, "prompt_tokens": 29345, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 33109}
