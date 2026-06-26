# Readiness Matrix

`docs/operations/readiness_matrix.json` is the checked-in operator catalog for
live dependencies that cannot be proven by the offline release gates. It is
release/operator material, not an audit artifact and not benchmark reproduction
evidence.

Generate the current report with:

```bash
make readiness-matrix
```

This writes `dist/self-harness-readiness-matrix.json`. The report validates the
catalog shape, offline fixture references, affected commands, and reproduction
boundary. It does not probe the environment, run Harbor, Docker, registries,
scanners, PyPI, Sigstore, models, or cloud providers.

## Catalog Fields

Each entry declares:

- `dependency`: human-readable live dependency name.
- `domain`: controlled readiness domain such as `harbor`, `docker`, `trivy`,
  `sigstore`, `pypi`, `model`, `scanner-db`, or `kms`.
- `status`: `blocked`, `optional`, or `provisioned`.
- `affects`: known gate, command, or adapter surfaces affected by the
  dependency.
- `offline_fixture`: repo-relative fixture, workflow, or contract file that
  covers the offline path.
- `operator_remediation`: concrete operator action required to unblock or
  harden the dependency.
- `reproduction_relevant`: whether the dependency matters for live benchmark or
  Terminal-Bench execution.
- `preflight_surface`: coarse offline surface that can support a future
  provisioned claim: `operator_preflight`, `scanner_check`,
  `harbor_discovery_check`, `container_preflight`, `attestation_check`,
  `model_backend_preflight`, `release_smoke`, or `none`.
- `operator_action`: machine-readable remediation category: `provision`,
  `configure`, `sign`, `publish`, `scan`, or `discover`.

Unknown fields, unknown domains, unknown affected surfaces, absolute fixture
paths, missing fixture files, malformed booleans, and unknown enum values fail
closed.

Schema `1.1` adds `preflight_surface` and `operator_action`. Schema `1.0`
catalogs still load with defaults of `none` and `provision`.

## Drift Check

`make readiness-drift-check` cross-checks the readiness catalog against the
existing offline preflight artifacts:

```bash
make readiness-drift-check
```

The drift report is written to `dist/self-harness-readiness-drift.json`.
Blocked and optional entries are advisory, even when their preflight surface is
absent or failed. A `provisioned` entry that is also
`reproduction_relevant: true` fails closed unless its declared preflight
surface is present, well-formed, has `"ok": true`, and has no failed required
checks. Entries with `preflight_surface: none` cannot be promoted to
`provisioned` while `reproduction_relevant` remains true.

Docker and Sigstore have explicit promotion guards. The Docker row uses
`container_preflight`; its default report is offline and advisory while the row
is `blocked`, but a future `provisioned` Docker row requires a live
container-preflight report. The Sigstore row uses `attestation_check`; the
default structural attestation report is advisory while the row is `blocked`,
but a future `provisioned` Sigstore row requires `verify-attestation --backend
sigstore` with `cryptographic_valid: true`.

The drift check reads existing JSON reports only. It does not run live tools,
contact external services, mutate audits, rotate readiness hashes, or claim
benchmark reproduction.

## Promotion Admission

Before changing a readiness row from `blocked` or `optional` to `provisioned`,
operators can compare the checked-in baseline catalog with a candidate catalog:

```bash
python scripts/readiness_promotion_report.py \
  --baseline-catalog docs/operations/readiness_matrix.json \
  --candidate-catalog dist/operator-readiness-candidate.json \
  --container-preflight-result dist/self-harness-container-preflight.json \
  --attestation-result dist/self-harness-attestation.json \
  --out dist/self-harness-readiness-promotion.json
```

The promotion verifier is read-only. It never rewrites the catalog; it only
writes a deterministic admission report. It rejects removed baseline entries,
demotions unless `--allow-demotion` is explicit, and `preflight_surface` changes
on provisioned reproduction-relevant rows. Promotions to provisioned
reproduction-relevant status must satisfy the same surface contract enforced by
the drift check, so Docker still requires a live container preflight report,
Sigstore requires the Sigstore backend with `cryptographic_valid: true`, and
model backend rows require live model-backend preflight evidence.

The Make target is:

```bash
make readiness-promotion-check \
  READINESS_BASELINE_CATALOG=docs/operations/readiness_matrix.json \
  READINESS_CANDIDATE_CATALOG=dist/operator-readiness-candidate.json
```

This target consumes existing surface artifacts when they are present under
`dist/`; it does not run live tools or regenerate evidence. The report is
advisory to the default release-candidate evidence path, but it gives operators
a machine-readable admission record before they apply a candidate catalog.

The PyPI trusted-publishing entry declares `preflight_surface: release_smoke`
because the installed-wheel smoke gate now writes
`dist/self-harness-release-smoke.json`. That status proves offline
installability and artifact parity only; it does not validate PyPI or TestPyPI
trusted publishing. The PyPI entry remains `blocked` until an operator verifies
trusted publishing in an operator-owned environment and explicitly reclassifies
it.

The paper model-backend entries are split by backend: MiniMax M2.5, Qwen3.5-
35B-A3B, and GLM-5.2. Each remains `blocked` until an operator supplies the
provider-specific live credentials or deployment and a matching preflight
artifact through the `model_backend_preflight` surface. The Anthropic adapter
entry is `optional` and
`reproduction_relevant: false`; it documents the package's reference provider
seam but is not one of the paper's evaluated model backends.

Run model-backend preflight explicitly with `make model-backend-preflight`.
Dry-run mode writes an inspection report without contacting providers; live mode
is operator-owned and enabled with `MODEL_BACKEND_PREFLIGHT_MODE=live`. See
`docs/operations/model_backend_preflight.md`.

Run the offline Docker surface explicitly with `make container-preflight`. It
writes `dist/self-harness-container-preflight.json` without contacting the
Docker daemon or inspecting images. Operator-owned live Docker validation should
use `scripts/container_preflight_report.py --mode live` and supply that artifact
before reclassifying the Docker row as `provisioned`.

## Release Evidence

`make release-candidate-evidence` requires the generated readiness matrix
report and the generated readiness drift report. The release-candidate decision
fails when either report is missing, malformed, failed, or claims reproduction.
A valid matrix may still set `live_execution_blocked: true`; this is expected
until the blocked live dependencies are provisioned and reclassified by an
operator.

Changing this catalog does not rotate canonical paper-fidelity readiness hashes.
It may rotate release/operator evidence hashes that include readiness report
metadata. It does not change audit schemas, task corpus schemas, benchmark
reports, or reproduction-claim semantics.
