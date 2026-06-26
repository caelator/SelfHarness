# Round 1

CONVERGED: NO

## Verdict

Round 1 plan proposed and self-critiqued. A coherent, locally testable, high-value slice exists: a **breaking-schema audit migration framework (P38)** that closes the explicit remaining gap left by P35's additive-only migration path. The plan is sound but needs one revision round to lock boundary semantics and test matrix before execution.

## Critique

Evidence (from repository):
- `RELEASE.md` states: "Breaking schema changes require a migration shim before the new major version becomes the default writer version," and documents `audit-migrate` as "upgrade-only … does not mutate the source audit … does not … claim benchmark reproduction."
- `docs/architecture/productionization_brief.md` lists remaining work as including "future migration shims for breaking schemas not covered by the current additive metadata path."
- P35 implementation notes restrict migration to additive metadata copying from schema `1.0` to latest readable schema, rejecting downgrade/malformed/existing-destination.
- The codebase already establishes the offline/fixture-tested pattern (scanner dry-run/replay, Harbor dry-run/replay, operator preflight) and the release/operator vs audit boundary language.

Inferences:
- A breaking-schema migration framework is the cleanest remaining slice because it (a) is explicitly enumerated as remaining, (b) requires no Harbor/Docker/cloud/Trivy/PyPI/Sigstore, (c) extends an existing operator-only CLI without touching audit writers or readiness hashes, and (d) has a natural deterministic fixture test surface.
- Lower-priority alternatives (provider-specific KMS/registry/OAuth helpers, live Harbor mock server, Sigstore/PyPI attestation scaffolding) are either narrower extensions of P37 seams or risk implying live trust boundaries; they are better sequenced after migration hardening.

Self-critique of the initial proposal:
- The initial idea of also adding localhost mock-server validation for Harbor "live" path was tempting, but it would expand scope and blur the "no live Harbor in CI" boundary; defer.
- The initial idea of bundling provider conformance reference adapters is valuable but is essentially documentation+tests around already-shipped protocols; less architecturally load-bearing than closing the migration-shim gap. Defer to a follow-up slice.
- The migration slice must explicitly forbid any change to canonical readiness hashes, current audit writer defaults, or reproduction-claim semantics. This needs to be wired into tests, not just docs.

## Required Changes

Before convergence, the next round must lock:
1. **Migration registry contract**: declare a versioned, in-repo registry of breaking transforms keyed by `(source_major, target_major)`; each transform is a pure function over deserialized audit metadata plus a lossless/lossy classification.
2. **Boundary guarantees**: migration output is always a *copy* under a new path; source audit bytes must remain byte-identical (asserted by hash); migrated artifacts must set `migration_applied: true` provenance and never set `reproduction_claimed: true`.
3. **Fail-closed matrix**: explicit rejection cases — downgrade, same-version, unsupported transform, lossy transform without explicit `--allow-lossy`, existing destination, malformed source, missing schema_version.
4. **Test fixtures**: one synthetic legacy fixture per prior audit schema major referenced in the brief (1.0, 1.1, 1.2, 1.3, 1.4), each migrated to the current writer schema; deterministic expected-hash coverage for the migrated tree where lossless.
5. **Make/CI surface**: `make migration-check` running the full fixture matrix offline; new CI job mirroring `operator-preflight` style; no live dependency.
6. **Docs**: `docs/operations/audit_migration.md` extended with breaking-schema section, transform authoring rules, lossy-transform policy, and release/operator boundary language.

## Revised Plan

**Slice P38 — Breaking-schema audit migration framework**

Scope (all local, no live dependencies):

1. `src/self_harness/audit_migration.py`
   - Versioned `MigrationRegistry` keyed by `(source_major, target_major)`.
   - `MigrationTransform` protocol: `(meta) -> (meta', classification, notes)`.
   - Classification enum: `lossless`, `lossy`, `unsupported`.
   - `migrate_audit_tree(source, dest, *, target_major, allow_lossy=False) -> MigrationReport`.
   - Source bytes hashed before and after to prove immutability.
   - Destination gains `migration_applied` provenance block with source hash, transform ids, classification, and timestamp-free deterministic fields.

2. `self-harness audit-migrate` extension
   - New flags: `--target-major`, `--allow-lossy`, `--transforms-json` (operator-owned override path, optional).
   - Existing additive behavior remains the default for additive-only upgrades.
   - Exit code 2 on any fail-closed condition listed above.

3. Fixtures under `tests/fixtures/audit_migration/`
   - `schema_1_0/`, `schema_1_1/`, `schema_1_2/`, `schema_1_3/`, `schema_1_4/` legacy source trees.
   - `expected_hashes.txt` for lossless migrations.
   - One lossy-transform fixture with explicit `--allow-lossy` test.

4. Tests
   - Lossless round-trip per source schema.
   - Lossy rejection without flag; success with flag.
   - Downgrade/same-version/existing-destination/malformed-source rejection.
   - Source immutability hash assertion.
   - `reproduction_claimed` absence assertion on migrated tree.
   - Readiness hash *unchanged* for current writer schema (regression guard).

5. Make / CI
   - `make migration-check`: runs fixture matrix, verifies expected hashes, asserts no reproduction claim.
   - New CI job `audit-migration` modeled on `operator-preflight`.
   - Add `make migration-check` to `release-smoke` prerequisites? — decision item (see Open Questions).

6. Docs
   - Extend `docs/operations/audit_migration.md`.
   - Add `docs/architecture/schema_changelog.md` entry stub for the framework itself (no schema bump this slice).

Boundary statements (must appear in code comments, docs, and CLI help):
- Migration is release/operator tooling.
- It does not mutate source audits, rotate canonical readiness hashes, change the default audit writer version, contact any network resource, or claim benchmark reproduction.
- Lossy transforms require explicit operator opt-in and are recorded in the migrated tree's provenance.

## Remaining Open Questions

1. Should `make migration-check` be a hard prerequisite of `release-smoke`, or a standalone gate like `operator-check`? (Inference: standalone is safer to avoid coupling release cadence to historical fixture maintenance; decision needed.)
2. Should the transform registry be extensible via entry points / plugins, or strictly in-repo? (Inference: strictly in-repo for now; operator-owned `--transforms-json` covers out-of-tree experiments without a plugin surface.)
3. Should lossy migrations be allowed to *drop* unknown fields or only *rename/remap* them? (Inference: drop-only-with-explicit-flag, never silent remap; confirm in round 2.)
4. Is the set of prior schema majors exactly `{1.0, 1.1, 1.2, 1.3, 1.4}`, or does the brief imply additional intermediate versions requiring fixtures? (Needs a quick check of `schema_changelog.md` in round 2.)

[usage] {"completion_tokens": 2402, "completion_tokens_details": {"reasoning_tokens": 804}, "prompt_tokens": 33848, "prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 36250}

---

# Round 2

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

[usage] {"completion_tokens": 1820, "completion_tokens_details": {"reasoning_tokens": 274}, "prompt_tokens": 37070, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 38890}
