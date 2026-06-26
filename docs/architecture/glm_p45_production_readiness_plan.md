CONVERGED: YES

## Verdict

P44 closed offline Sigstore cryptographic verification behind an optional extra with fail-closed semantics and no live contact. The strongest next slice (P45) is an **offline operator readiness matrix + live-dependency blocker catalog**: a versioned, generated artifact that enumerates every remaining live-infrastructure dependency, the gate that currently fails closed without it, the offline fixture proving the seam, and the operator remediation step. This materially advances production readiness without requiring Harbor/Trivy/Docker/Sigstore/PyPI access, does not claim benchmark reproduction, and directly increases audibility of the exact blockers operators must remove before live runs.

Evidence: P31–P44 each define fail-closed preflight/dry-run/replay paths; `make release-candidate-evidence` already aggregates offline gates; `docs/architecture/productionization_brief.md` explicitly lists remaining live work. Inference: no existing artifact consolidates blockers in a machine-readable schema, so this slice is additive and non-breaking.

## Critique

- The repository already has many narrow preflight scripts (`operator_preflight.py`, `scanner_run.py`, `scanner_db_update.py`, `harbor_discovery.py`, `terminal-bench-preflight`). Risk: another generator could duplicate state and drift.
- Mitigation: the readiness matrix should be **declarative and content-addressed**, loaded from a checked-in `docs/operations/readiness_matrix.json` catalog, not computed by probing the system. The generator only validates the catalog against the codebase (e.g., command names referenced exist, fixture paths exist) and emits a stable report.
- Scope must forbid any live probing; all status fields are static operator assertions, not runtime detections. Runtime detection already lives behind preflight scripts and must not be duplicated.
- Must remain release/operator material: no audit-schema, corpus-schema, manifest-schema, readiness-hash, or reproduction-claim change.

## Required Changes

The candidate slice is ready to execute provided the plan enforces:
1. Catalog is the source of truth; generator validates only.
2. No live network, subprocess, or filesystem probing beyond reading checked-in files.
3. Output is deterministic JSON (+ optional markdown), with stable `report_hash`.
4. Wiring into `make operator-check` and `release-candidate-evidence` is optional/additive and must not break the existing hash fixtures unless we rotate intentionally.
5. Tests cover: missing/malformed catalog, unknown gate reference, missing fixture path, deterministic hash, and reproduction-claim rejection.
6. Documentation explicitly states this is a blocker catalog, not a reproduction or live-capability claim.

## Revised Plan

**P45 — Offline operator readiness matrix and live-dependency blocker catalog**

1. Add `src/self_harness/readiness_matrix.py`:
   - `ReadinessMatrixCatalog` schema `1.0`: entries with `dependency`, `domain` (harbor/docker/trivy/sigstore/pypi/model/registry/scanner-db/secret/kms), `status` (provisioned/blocked/optional), `affects` (list of CLI/gate names), `offline_fixture` (optional checked-in path), `operator_remediation`, `reproduction_relevant` (bool).
   - `load_readiness_matrix_catalog(path)` with strict validation, unknown-field rejection, and non-empty required strings.
   - `evaluate_readiness_matrix(catalog)` returning a deterministic `ReadinessMatrixReport` with per-entry evaluated rows, overall `live_execution_blocked` boolean (true if any `blocked` entry is `reproduction_relevant`), `report_hash`, and `reproduction_claimed=false`.
   - Public helpers: `readiness_matrix_report_to_jsonable`, `ReadinessMatrixError`.

2. Add `scripts/readiness_matrix_report.py`:
   - `--catalog docs/operations/readiness_matrix.json`
   - `--out dist/self-harness-readiness-matrix.json`
   - Exit 0 for valid catalog (regardless of blocked state), exit 2 for malformed/invalid catalog.
   - No network, no subprocess, no environment probing.

3. Add `docs/operations/readiness_matrix.json` (checked-in catalog):
   - Harbor live host → blocked, affects `terminal-bench --mode live`, `harbor-discovery --url ...` live path.
   - Docker daemon → blocked, affects `container-demo --mode live`, `terminal-bench --mode live`.
   - Trivy binary + DB → blocked, affects `scanner_run.py` live, `scanner_db_update.py` live.
   - Sigstore Fulcio/Rekor → blocked, affects `verify-attestation --backend sigstore` with real bundles.
   - PyPI trusted publishing → blocked, affects release publishing workflow.
   - Model API key → blocked, affects `LLMProposer` with `AnthropicClaudeClient`.
   - Scanner DB mirror credentials → optional, affects `--db-registry-config` live behavior.
   - Each row names its offline fixture or preflight script proving the seam today.

4. Add `make readiness-matrix`:
   - Runs `scripts/readiness_matrix_report.py` against the checked-in catalog.
   - Standalone offline gate; included in `operator-check` dependencies as additive.

5. Add optional release-candidate evidence hook:
   - `scripts/release_candidate_evidence.py` accepts `--readiness-matrix-result` (optional). If supplied and malformed or reproduction-claiming, block. Do not make it required in this slice to avoid rotating existing fixture hashes.

6. Tests:
   - `tests/test_readiness_matrix.py`: catalog load/validation, unknown field rejection, missing fixture path rejection, unknown gate reference rejection, deterministic `report_hash`, `reproduction_claimed=false`, live-blocked summary correctness.
   - `tests/invariants/test_readiness_matrix_boundary.py`: assert catalog entries do not claim reproduction and any `reproduction_relevant=true` entry in `blocked` state sets `live_execution_blocked=true`.

7. Docs:
   - `docs/operations/readiness_matrix.md`: purpose, operator workflow, how to update the catalog, and explicit non-reproduction boundary.
   - README/RELEASE: mention `make readiness-matrix` and the catalog path; do not change release gate requirements.

8. CI:
   - Add `readiness-matrix` job (Python 3.11) running `make PYTHON=python readiness-matrix` and uploading `dist/self-harness-readiness-matrix.json`.
   - Do not modify existing matrix hashes.

Out of scope (explicit): live probing, KMS/HSM SDK adapters, real Sigstore bundles, real Harbor/Docker execution, scanner DB downloads, audit/corpus/manifest schema changes, readiness-hash rotation, and any benchmark reproduction claim.

## Remaining Open Questions

- Should the readiness matrix catalog eventually become required input to `release-candidate-evidence` (forcing a fixture hash rotation)? Deferred to P46; non-blocking for P45.
- Should operator preflight scripts (`operator_preflight.py`, `scanner_run.py`) emit per-check rows that the matrix can reference for cross-validation? Desirable but deferred; P45 keeps the catalog declarative to avoid coupling.
- Is a markdown rendering target wanted for release notes? Recommended as optional in P45; exact templating can be decided during implementation without blocking convergence.
