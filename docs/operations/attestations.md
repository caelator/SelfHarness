# Release Attestations

Self-Harness supports an offline release-attestation pre-validation gate for
operator-owned PyPI attestation envelopes and Sigstore bundle material.

This gate checks local structure only:

- the attestation envelope uses a supported PyPI attestation `_type`;
- the attestation `materials` bind the checked distribution artifact by
  SHA-256;
- the embedded bundle contains a base64 signature field, certificate chain, and
  transparency-log entries;
- the leaf certificate issuer and subject alternative name match an
  operator-owned trust-root JSON file.

The structural backend never contacts Fulcio, Rekor, PyPI, Sigstore, Harbor,
Docker, registries, scanners, models, or cloud providers. Its
`cryptographic_valid` value is always `null`, and its report is release/operator
material, not benchmark reproduction evidence.

## Local Structural Gate

Build the distribution artifacts and run the structural fixture gate:

```bash
make attestation-check
```

The target writes:

- `dist/self-harness-pypi-attestation.json`
- `dist/self-harness-attestation.json`

For an operator-supplied attestation and trust root, run:

```bash
self-harness verify-attestation \
  --bundle path/to/pypi-attestation.json \
  --material dist/self_harness-0.1.0-py3-none-any.whl \
  --trust-root ops/attestation-trust-root.json \
  --backend structural \
  --out dist/self-harness-attestation.json
```

The script form prints the report and writes the same schema:

```bash
python scripts/verify_attestation.py \
  --bundle path/to/pypi-attestation.json \
  --material dist/self_harness-0.1.0-py3-none-any.whl \
  --trust-root ops/attestation-trust-root.json \
  --backend structural \
  --out dist/self-harness-attestation.json
```

## Trust Root Shape

Trust roots are operator-owned release policy:

```json
{
  "schema_version": "1.0",
  "expected_certificate_issuer": "CN=Example Fulcio",
  "allowed_subject_alternative_names": [
    "https://github.com/example/self-harness/.github/workflows/release.yml@refs/tags/v0.1.0"
  ],
  "fulcio_certificate_paths": ["fulcio-root.pem"],
  "rekor_public_key_path": "rekor.pub",
  "sigstore_client_trust_config_path": "client-trust-config.json"
}
```

Paths are resolved relative to the trust-root file when they are not absolute.
The current structural backend confirms that the files exist and uses the
issuer/SAN allowlist for certificate identity checks. It does not validate a
Fulcio chain, Rekor inclusion proof, or signature over artifact bytes.

`sigstore_client_trust_config_path` is optional for structural checks and
required for cryptographic checks unless `sigstore_trusted_root_path` is
supplied instead. Those files must use Sigstore's native client trust config or
trusted-root JSON shapes. The minimal PEM/Rekor fields above are not enough for
cryptographic verification because Sigstore also needs CT log and Rekor trust
metadata.

## Release-Candidate Evidence

`scripts/release_candidate_evidence.py --attestation-result` is optional. When a
report is supplied, missing, malformed, failed, or reproduction-claiming
attestation reports block the release-candidate decision. When absent, the
release-candidate schema remains valid and no attestation gate is emitted.

The Makefile production stack supplies `dist/self-harness-attestation.json` to
release-candidate evidence after `make attestation-check`.

## Cryptographic Backend Contract

`self_harness.attestations.SigstorePythonVerifier` verifies canonical Sigstore
bundle material offline through `sigstore-python` when the optional extra is
installed and the trust root points to a full Sigstore client trust config or
trusted-root file. It verifies the artifact bytes, bundle signature, signing
certificate, transparency-log evidence, and identity policy through
`sigstore-python` without contacting Fulcio, Rekor, PyPI, Sigstore, Harbor,
Docker, registries, scanners, models, or cloud providers.

Install the optional extra only in environments that intentionally integrate a
real Sigstore verification policy:

```bash
python -m pip install 'self-harness[sigstore]'
```

Then run:

```bash
self-harness verify-attestation \
  --bundle path/to/pypi-attestation.json \
  --material dist/self_harness-0.1.0-py3-none-any.whl \
  --trust-root ops/attestation-trust-root.json \
  --backend sigstore \
  --out dist/self-harness-attestation.json
```

This repository does not include a fake passing real-crypto fixture. Maintainers
must validate real signing material with an operator-owned bundle and trust root
before promoting a release. Do not treat structural success as proof that an
artifact is trusted for publication; it is a pre-validation check that catches
local shape, digest, and identity mistakes before a real signing or publishing
workflow consumes the material.
