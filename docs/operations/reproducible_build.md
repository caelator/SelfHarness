# Reproducible Build Verification

`scripts/verify_reproducible_build.py` verifies that the release wheel can be
rebuilt byte-for-byte from the release source distribution. It writes
`dist/self-harness-reproducible-build.json`, a deterministic
`reproducible_build/1.0` report consumed by release-candidate evidence.

Run it through Make:

```sh
make reproducible-build-check
```

The Make target first runs `make build` with a fixed `SOURCE_DATE_EPOCH` and
`PYTHONHASHSEED=0`. The verifier then runs:

```sh
python -m pip wheel --no-index --no-deps --no-build-isolation
```

against the built source distribution and compares the rebuilt wheel filename
and SHA-256 hash with the published wheel in `dist/`.

## Contract

The report contains:

- `schema_version: "1.0"`;
- `artifact_class: "reproducible_build"`;
- `ok`;
- pass/fail checks;
- SHA-256 and byte counts for the sdist, published wheel, and rebuilt wheel;
- build metadata including `source_date_epoch` and `network_contact:false`;
- `report_hash`;
- `reproduction_claimed:false`.

Exit codes:

- `0`: rebuilt wheel is byte-identical to the published wheel;
- `2`: inputs were valid but the rebuilt wheel differed;
- `3`: input or rebuild execution failed.

## Boundary

This gate is package supply-chain evidence. It does not contact PyPI or
TestPyPI, validate trusted publishing, verify provenance signatures, run
Sigstore, run scanners, contact Harbor or Docker, run model backends, or claim
Terminal-Bench benchmark reproduction.

If the gate fails, common causes are missing no-isolation build dependencies,
generated files that depend on the current clock, non-deterministic file
ordering, or building the published wheel without the same `SOURCE_DATE_EPOCH`.
