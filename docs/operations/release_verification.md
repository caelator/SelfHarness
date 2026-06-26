# Release Verification

Use the normal release evidence path for package readiness:

```bash
make release-candidate-evidence
```

This path is non-reproduction release/operator evidence. It does not claim
Terminal-Bench reproduction.

`make release-candidate-evidence` includes `make reproducible-build-check`,
which rebuilds the wheel from the source distribution and blocks the package
release if the rebuilt wheel differs from the wheel in `dist/`.

For benchmark reproduction evidence, first lint the supplied live artifact
directory:

```bash
make reproduction-readiness-artifact-shape-lint ARTIFACT_DIR=dist/reproduction-artifacts
```

Then run the readiness report:

```bash
make reproduction-readiness-check
```

The standalone reproduction-readiness script exits `0` when ready, `2` when the
report is valid but not ready, and `3` for corrupt inputs. The Make target keeps
the not-ready report for inspection.

The hard release gate is:

```bash
make release-candidate-evidence-reproduction
```

It blocks unless the reproduction-readiness report says
`reproduction_ready:true`. None of these targets contacts live infrastructure
unless the operator supplies live artifacts or runs separate live preflight
commands.
