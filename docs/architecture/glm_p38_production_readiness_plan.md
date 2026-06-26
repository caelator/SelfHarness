CONVERGED: YES

## Verdict

Round 2 locks the four open questions from round 1 against repository evidence. All blocking decisions are resolved; remaining items are implementation detail, not architecture. The P38 breaking-schema audit migration framework is approved for implementation.

## Critique

Evidence (from repository):
- `RELEASE.md` already commits Self-Harness to migration-shim-before-major-default-writer policy and to `audit-migrate` as release/operator-only tooling that does not mutate source, rotate readiness hashes, or claim reproduction.
- `docs/architecture/productionization_brief.md` enumerates audit schema majors `1.0` through `1.4` and derived schema `1.0` for trajectory, benchmark report, harness inspection. The brief explicitly lists "future migration shims for breaking schemas not covered by the current additive metadata path" as remaining work.
- P35 notes restrict current `audit-migrate` to additive metadata copy with fail-closed rejection of downgrade/malformed/existing-destination.
- Existing offline/fixture/release-operator patterns (`operator-check`, `scanner-check`, `harbor-discovery-check`, `release-candidate-evidence`) are standalone CI jobs and `make` targets, not release-smoke prerequisites.
- Provider seams (`self_harness.providers`) are in-repo contracts plus static test providers; no plugin/entry-point surface.

Inferences (locked decisions):
1. `make migration-check` should be a **standalone gate**, mirroring `operator-check`. Coupling it to `release-smoke` would tie release cadence to historical fixture maintenance and conflict with the existing standalone-gate pattern.
2. Transform registry is **strictly in-repo**; operator-owned `--transforms-json` is the escape hatch. No entry-point/plugin surface is warranted given the in-repo provider-seam precedent.
3. Lossy transforms are **drop-only-with-explicit-flag** (`--allow-lossy`), recorded in migrated provenance. Silent rename/remap violates audit transparency and is rejected.
4. Required fixture source majors are exactly the audit schema majors documented in the brief: **`{1.0, 1.1, 1.2, 1.3, 1.4}`**, each migrated to the current writer schema. Derived schemas (trajectory, benchmark, inspection) are not audit majors and are out of scope.

Self-critique:
- Considered folding derived trajectory schema migration into this slice. Rejected: trajectory is a derived artifact, not an audit tree; P38 scope stays on audit migration to preserve the release/operator boundary.
- Considered adding a plugin entry point for transforms. Rejected: no plugin precedent exists; `--transforms-json` covers operator experimentation without expanding the trust surface.

## Required Changes

None blocking. The following are non-blocking implementation requirements carried from round 1 and now locked:
- Migration registry keyed by `(source_major, target_major)`; pure transform functions; classification enum `lossless|lossy|unsupported`.
- Copy-only output; source byte-hash immutability assertion before and after.
- Migrated provenance block: `migration_applied=true`, source hash, transform ids, classification; never `reproduction_claimed=true`.
- Fail-closed matrix: downgrade, same-version, unsupported transform, lossy without `--allow-lossy`, existing destination, malformed source, missing `schema_version`.
- Fixtures under `tests/fixtures/audit_migration/schema_1_{0,1,2,3,4}/` plus one lossy-with-flag fixture; deterministic expected-hash file for lossless cases.
- `make migration-check` standalone target; new CI job `audit-migration` mirroring `operator-preflight`; NOT added to `release-smoke` prerequisites.
- Docs: extend `docs/operations/audit_migration.md` and add `docs/architecture/schema_changelog.md` framework stub (no schema bump this slice).
- Boundary statements in code, docs, and CLI help: release/operator-only, no source mutation, no readiness-hash rotation, no default writer change, no network, no reproduction claim.

## Revised Plan

**Slice P38 — Breaking-schema audit migration framework** (approved, ready to execute)

1. `src/self_harness/audit_migration.py`
   - `MigrationRegistry` keyed by `(source_major: str, target_major: str)`.
   - `MigrationTransform` protocol: `(meta) -> (meta', classification, notes)`.
   - `Classification = Literal["lossless", "lossy", "unsupported"]`.
   - `migrate_audit_tree(source, dest, *, target_major, allow_lossy=False, transforms_json=None) -> MigrationReport`.
   - Source bytes hashed before and after copy phase to prove immutability.
   - Destination gains `migration_applied` provenance block.
   - Operator-owned `transforms_json` overrides registry; strictly optional.

2. `self-harness audit-migrate` extension
   - New flags: `--target-major`, `--allow-lossy`, `--transforms-json`.
   - Existing additive behavior remains the default when no `--target-major` is supplied.
   - Exit code 2 on any fail-closed condition.
   - Help text restates the release/operator boundary.

3. Fixtures under `tests/fixtures/audit_migration/`
   - `schema_1_0/` … `schema_1_4/` legacy source trees.
   - `expected_hashes.txt` for lossless migrations.
   - One lossy-transform fixture requiring `--allow-lossy`.

4. Tests
   - Lossless migration per source schema with hash assertion.
   - Lossy rejection without flag; success with flag; provenance records lossy classification.
   - Rejection matrix: downgrade, same-version, existing destination, malformed source, missing `schema_version`, unsupported transform.
   - Source immutability hash before/after.
   - `reproduction_claimed` absence assertion on migrated tree.
   - Current writer schema readiness hash unchanged (regression guard).
   - `transforms_json` override path exercised.

5. Make / CI
   - `make migration-check`: offline fixture matrix, verifies expected hashes, asserts no reproduction claim.
   - New CI job `audit-migration` modeled on `operator-preflight`.
   - Not added to `release-smoke` prerequisites.

6. Docs
   - Extend `docs/operations/audit_migration.md` with breaking-schema section, transform authoring rules, lossy policy, release/operator boundary.
   - Add `docs/architecture/schema_changelog.md` framework stub; no schema bump.

Boundary statements (code comments, docs, CLI help):
- Migration is release/operator tooling.
- It does not mutate source audits, rotate canonical readiness hashes, change the default audit writer version, contact any network resource, or claim benchmark reproduction.
- Lossy transforms require explicit `--allow-lossy` and are recorded in migrated provenance as drop-only.

## Remaining Open Questions

None blocking. Decisions locked:
1. `make migration-check` is a standalone gate, not a `release-smoke` prerequisite.
2. Transform registry is strictly in-repo; `--transforms-json` is the operator override path; no plugin surface.
3. Lossy migrations are drop-only-with-explicit-flag; no silent rename/remap.
4. Required fixture source majors are exactly `{1.0, 1.1, 1.2, 1.3, 1.4}`.
