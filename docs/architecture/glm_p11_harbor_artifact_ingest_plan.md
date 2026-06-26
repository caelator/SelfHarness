CONVERGED: YES

## Verdict

The initial plan proposes a Harbor artifact ingestion slice but inherits the same risk as the existing `parse_harbor_output`: it would invent yet another "Self-Harness-shaped" schema without validating against real Harbor output. The revised plan corrects this by making schema discovery itself a first-class deliverable, and bounds the parser as explicitly candidate until a real run is captured. With that change the slice is implementation-ready, materially improves readiness (real Harbor outputs become ingestible the moment Harbor is provisioned), and respects the no-reproduction / no-toy-demo constraints.

## Critique

Evidence (from repo):
- P10 already implemented `HarborCommandSpec`, `parse_harbor_output` with a `HarborOutputFormat.SELF_HARNESS_V1` enum value that is explicitly a Self-Harness-invented shape (`src/self_harness/adapters/terminal_bench/harbor_output.py`).
- `harbor_protocol_assumptions.md` already labels the structured output as inferred and notes "the first real run can refine them."
- `productionization_brief.md` lists the remaining boundary as: "exact Harbor structured output schema and custom DeepAgent config channel must still be validated with a real provisioned Harbor run."
- P7 preflight already fails gracefully and writes a report; missing Harbor/Docker is by design.

Inference:
- Inventing a second, richer ingestion schema (trial directories, reward.json, ATIF trajectory logs) without a real Harbor capture doubles the validation debt rather than reducing it.
- The valuable, executable-now move is to ship an ingestion boundary that (a) captures whatever Harbor actually produces, and (b) labels every parsed field by source so future validation is mechanical.

Material risks in the initial plan:
1. **Schema invention risk**: a new `HarborTrialOutput` dataclass with fixed fields assumes a layout we have never observed. Mitigation: ship a redacted tree-dump inspection command and a parser that records `field_source` per parsed value.
2. **Provenance drift risk**: richer ingestion makes it tempting to start filling reproduction-provenance fields from inferred data. Mitigation: explicit `candidate` vs `validated` provenance status; readiness gate already rejects `reproduction_claimed=true` with incomplete provenance.
3. **Engine coupling risk**: wiring new ingestion directly into the engine evaluation loop risks breaking the deterministic audit hash. Mitigation: ingestion is additive metadata on evaluation rows; schema bump to 1.4 with a migration test that 1.3 audits still load.

## Required Changes

1. Add a **schema discovery tool** (`self-harness harbor-inspect <run_dir>`) that dumps a redacted file tree with sha256 + size per file. This is the single artifact that the next convergence round needs to lock the schema.
2. Make the new ingestion parser **source-attributed**: every parsed field carries `field_source` ∈ {`reward.json`, `reward.txt`, `trajectory.jsonl`, `exit-code`, `missing`, `inferred`}. No field is silently inferred.
3. Add an explicit **`harbor_artifact_validation_status`** field on audit rows: `candidate` until a real Harbor run is captured and the layout matches `docs/architecture/harbor_artifact_layout.md`. Readiness gate forbids `validated` status co-occurring with `reproduction_claimed=false` mismatch.
4. Keep `parse_harbor_output` (stdout parser) and the new directory parser **separate modules**; do not unify them in this slice. Both stay labeled candidate.
5. Do not introduce engine-level `n_attempts` orchestration. Capture per-trial attempt metadata only.

## Revised Plan

### Slice: P11 — Harbor Run Artifact Ingestion Boundary

**Modules**
- `src/self_harness/adapters/terminal_bench/harbor_artifacts.py` (new)
  - `HarborArtifactProvenance` (frozen dataclass): `run_dir`, `discovered_files: tuple[str,...]`, `validation_status: Literal["candidate","validated","partial"]`, `missing_required: tuple[str,...]`.
  - `HarborTrialRecord` (frozen dataclass): `task_id`, `attempt_index`, `passed`, `reward_value: float | None`, `reward_source: str`, `terminal_cause: str`, `mechanism: str`, `trajectory_events: tuple[TraceEvent,...]`, `field_sources: dict[str,str]`.
  - `inspect_run_dir(run_dir: Path) -> dict[str, Any]` — redacted tree with hashes; stable JSON.
  - `discover_trials(run_dir: Path) -> list[HarborTrialRecord]` — best-effort; returns empty list with `validation_status="partial"` if no markers found.
  - `parse_reward(path: Path) -> tuple[float | None, str]` — handles `.json` (float or `{"reward": ...}`) and `.txt` (parseable float); returns `(value, source)`; on failure returns `(None, "missing")`.
  - `parse_trajectory_log(path: Path) -> tuple[list[TraceEvent], str]` — generic JSONL; preserves `kind/message/metadata`; returns `([], "missing")` if absent.
- `src/self_harness/adapters/terminal_bench/runner.py` (modify)
  - `HarborRunner._live_run` after subprocess: call `discover_trials(preserved_run_dir)` only when `keep_run_dir` path is set; otherwise proceed with existing stdout parser. Do not change dry-run path.
  - Add `keep_run_dir: Path | None = None` to `HarborRunner`.
- `src/self_harness/reporting/provenance.py` (modify)
  - Extend `BenchmarkProvenance` with `harbor_artifact_validation_status: str = "candidate"`.
  - `validate_provenance_completeness` raises if `reproduction_claimed=True` and `harbor_artifact_validation_status != "validated"`.

**CLI**
- `self-harness harbor-inspect <run_dir> [--out <path>] [--json]` — writes/dumps redacted tree.
- `self-harness harbor-ingest <run_dir> --manifest <manifest.json> --out <audit_dir>` — offline ingestion of a completed Harbor run; writes a single-round audit directory with schema 1.4; never claims reproduction; requires `reproduction_claimed=false`.
- Existing `terminal-bench --mode live` gains `--keep-run-dir <path>` flag.

**Schemas**
- Audit schema `1.4`:
  - Evaluation rows add `harbor_artifact_provenance` (`run_dir`, `validation_status`, `missing_required`).
  - Evaluation rows add per-attempt `reward_value`, `reward_source`, `trajectory_event_count`.
  - Manifest adds `harbor_artifact_validation_status` (default `candidate`).
- Migration: `audit.py` `SUPPORTED_SCHEMA_VERSIONS` adds `"1.4"`; loader accepts 1.3 without change (new fields absent ⇒ defaults).

**Tests**
- `tests/test_harbor_artifacts.py`:
  - Synthetic Harbor-like directory fixture (reward.json, reward.txt, trajectories/attempts/0/trajectory.jsonl) — fixture layout documented as inferred.
  - `inspect_run_dir` produces stable JSON with sha256 of file contents.
  - `discover_trials` returns one `HarborTrialRecord` per attempt directory with correct `field_sources`.
  - `parse_reward` handles float, `{"reward": 0.0}`, and `.txt` formats; returns `(None, "missing")` for absent file.
  - Partial directory (no reward file) yields `validation_status="partial"` and `missing_required=["reward"]`.
- `tests/test_harbor_ingest_cli.py`:
  - `harbor-ingest` writes schema 1.4 audit dir; `reproduction_claimed=false`; `harbor_artifact_validation_status="candidate"`.
- `tests/invariants/test_harbor_artifact_provenance.py`:
  - Reproduction claim with `validation_status="candidate"` raises `PaperFidelityError`.
  - Reproduction claim with `validation_status="validated"` still requires complete model/harbor/dataset provenance.
- `tests/test_audit_schema_migration.py`:
  - Loading a 1.3 audit dir still works; accessing new fields returns defaults.

**Docs**
- New `docs/architecture/harbor_artifact_layout.md`: each path labeled `[inferred]` or `[from-harbor-docs]`; explicit note that the first real `harbor-inspect` output supersedes this document.
- Update `harbor_protocol_assumptions.md` with artifact layout section.
- Update `README.md` experimental section with `harbor-inspect` and `harbor-ingest` usage and the `--keep-run-dir` flag.

**Acceptance gates**
- `make check` and `make readiness` pass.
- New readiness invariant: `harbor-ingest` output with `validation_status != "validated"` cannot be used in `benchmark-report --reproduction-claimed` (already enforced via provenance validator; add explicit test).
- Deterministic audit hash fixture in `make readiness` is regenerated for the 1.4 schema bump with a changelog entry.

**Stop conditions**
- Do not validate against a real Harbor run in this slice.
- Do not implement engine-level multi-attempt orchestration.
- Do not implement ATIF-specific event typing beyond generic `TraceEvent`.
- Do not unify stdout parser and directory parser.
- Do not auto-promote `validation_status` to `validated`; that transition is a manual decision in a future round once a real capture exists.

## Remaining Open Questions

Non-blocking for execution of this slice; blocking for closing the reproduction boundary in a future round:

1. **Real Harbor run directory layout** — must be answered by capturing the first real run via `harbor-inspect`. Cannot be replaced by more planning.
2. **Exact reward schema** (plain float vs. `{"reward": ...}` vs. nested) — handled defensively in `parse_reward`; to be locked down post-capture.
3. **ATIF trajectory log semantics** — current slice treats as opaque JSONL; richer typing deferred until real sample exists.
4. **Whether Harbor supports `--n-attempts` as a flag or via repeated invocation** — orthogonal; engine-level orchestration deferred per stop conditions.
