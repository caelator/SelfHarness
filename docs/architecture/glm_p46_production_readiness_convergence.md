# Round 1

CONVERGED: YES

## Verdict

P45 (offline readiness matrix catalog) is fully implemented in the repo: `src/self_harness/readiness_matrix.py`, `scripts/readiness_matrix_report.py`, `docs/operations/readiness_matrix.json`, the `make readiness-matrix` target, CI coverage, and optional wiring into `release_candidate_evidence.py`. The highest-value locally implementable P46 slice is closing P45's first deferred open question: **promote the readiness matrix from optional to required release-candidate evidence**. This closes the operator-visibility loop without live access, without reproduction claims, and without core schema changes. The only cost is an intentional rotation of the `release-candidate-evidence` expected-hash fixture, which is already an accepted operation under the release policy.

Evidence: `scripts/release_candidate_evidence.py` currently treats `--readiness-matrix-result` as optional; `docs/operations/release_candidate_evidence.md` lists it as optional; CI's `release-candidate-evidence` job does not currently pass it; `Makefile`'s `release-candidate-evidence` target already supplies it. Inference: making it required is a small, mechanical, additive change that materially hardens the release gate.

## Critique

- The slice is intentionally narrow. That is a feature, not a gap: every remaining production item in the brief requires live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud access, which is explicitly out of scope.
- The only material side effect is rotating `tests/fixtures/release_candidate/expected_hash.txt`. This is permitted by `RELEASE.md` ("Canonical Audit Hash Rotation" rules apply to audit hashes; release-candidate evidence hashes are release/operator material and rotate when inputs change). It must be called out in the schema changelog and release notes.
- Risk of fixture drift: the CI `release-candidate-evidence` job currently omits `--readiness-matrix-result`. Forgetting to add both the fixture file and the flag would make CI red. Mitigation: add the fixture, update the flag, regenerate the expected hash from the actual run, and verify locally before merge.
- Non-goal risk to avoid: do **not** also rotate the canonical paper-fidelity readiness hash (`tests/fixtures/canonical_audit_hash.txt`). Those are independent artifacts.
- Do not couple this slice to P45 Q2 (per-check preflight row emission). That is desirable future work but adds coupling between declarative catalog and runtime preflight scripts; it should be its own slice.

## Required Changes

None blocking. Execution must enforce:
1. `--readiness-matrix-result` becomes a required argument in `scripts/release_candidate_evidence.py`.
2. A checked-in fixture `tests/fixtures/release_candidate/readiness_matrix_result.json` is added, generated deterministically from `docs/operations/readiness_matrix.json` via `scripts/readiness_matrix_report.py`. Its `live_execution_blocked` may legitimately be `true`; that does not block the release decision.
3. CI's `release-candidate-evidence` job is updated to pass `--readiness-matrix-result tests/fixtures/release_candidate/readiness_matrix_result.json`.
4. `tests/fixtures/release_candidate/expected_hash.txt` is regenerated from the new gate set.
5. Docs (`docs/operations/release_candidate_evidence.md`, `RELEASE.md`) move readiness matrix from "optional" to "required" and note the fixture rotation.
6. No change to audit schema, corpus schema, manifest schema, canonical readiness hash, or reproduction-claim semantics.

## Revised Plan

**P46 — Promote readiness matrix to required release-candidate evidence**

1. `scripts/release_candidate_evidence.py`:
   - Change `--readiness-matrix-result` from `type=Path` (optional) to `type=Path, required=True`.
   - Keep existing validation (schema `1.0`, `ok=true`, boolean `live_execution_blocked`, lowercase-hex `report_hash`, no reproduction claim).
2. Fixtures:
   - Add `tests/fixtures/release_candidate/readiness_matrix_result.json` produced by running `scripts/readiness_matrix_report.py --catalog docs/operations/readiness_matrix.json` and copying the output (or committing a stable equivalent).
   - Regenerate `tests/fixtures/release_candidate/expected_hash.txt` from a local run of the aggregator with the full required gate set.
3. CI (`.github/workflows/ci.yml`):
   - In the `release-candidate-evidence` job, append `--readiness-matrix-result tests/fixtures/release_candidate/readiness_matrix_result.json`.
4. Makefile:
   - No change required; `release-candidate-evidence` already passes `dist/self-harness-readiness-matrix.json`.
5. Tests:
   - Add/extend a unit test in `tests/test_release_candidate_evidence.py` (or equivalent) covering: missing `--readiness-matrix-result` fails closed; supplied-but-malformed matrix fails closed; supplied matrix claiming reproduction fails closed; valid matrix with `live_execution_blocked=true` passes; expected hash matches the committed fixture.
6. Docs:
   - `docs/operations/release_candidate_evidence.md`: move readiness matrix from optional to required inputs; note that a valid matrix may still report `live_execution_blocked=true`.
   - `RELEASE.md`: list `make readiness-matrix` output as required release-candidate evidence and note the P46 fixture rotation in the release-notes template language.
   - `docs/architecture/schema_changelog.md` (if it tracks release-evidence inputs): add a one-line entry noting readiness matrix became required release-candidate evidence with no schema-version bump (evidence schema stays `1.0`).
7. Explicit non-goals:
   - No live Harbor/Docker/Trivy/PyPI/Sigstore/model/cloud contact.
   - No audit/corpus/manifest schema change.
   - No rotation of `tests/fixtures/canonical_audit_hash.txt`.
   - No coupling to operator preflight per-check row emission (P45 Q2 — deferred).
   - No benchmark reproduction claim.
   - No markdown rendering of the matrix (P45 Q3 — deferred).

## Remaining Open Questions

- Should operator preflight scripts (`operator_preflight.py`, `scanner_run.py`, `scanner_db_update.py`) emit per-check rows that the readiness matrix can cross-reference for drift detection? **Deferred to a future slice**; non-blocking for P46. (P45 Q2)
- Is a markdown rendering of the readiness matrix wanted for release notes? **Deferred**; the JSON report is sufficient for the release gate. (P45 Q3)
- Should `release-candidate-evidence` eventually fail when `live_execution_blocked=true`? **No, by design** — the catalog is operator information; the gate only fails on missing/malformed/reproduction-claiming material. Documented and preserved.

[usage] {"completion_tokens": 2462, "completion_tokens_details": {"reasoning_tokens": 1041}, "prompt_tokens": 31836, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 34298}
