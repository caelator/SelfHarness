CONVERGED: YES

## Verdict
The candidate gap is real, well-scoped, and material. Section 3.4 explicitly requires per-evaluated-candidate records (changed surfaces, split-wise outcomes, evaluation repeats, proposal summary, accept/reject decision), but the current reproduction bundle only has the boolean `audit_verify_report` and prior-round `previous_attempted_edits` summaries inside `proposer_context_manifest`. There is no strict per-candidate validation artifact that binds the live two-repeat evaluation and proposer context evidence to the actual Section 3.4 acceptance/rejection lineage. The next slice can be implemented fully offline, preserves `reproduction_ready:false`, and is ready to execute.

## Critique
Initial plan shape (strict `proposal_validation_manifest` artifact + cross-artifact bindings) is correct, but four scoping decisions must be made explicit:

1. **Artifact vs. writer.** The engine already writes `proposals.jsonl` and `evaluations.jsonl` per round with all Section 3.4 fields. The new artifact should be a *derived extraction* over an audit directory (like `proposer_context_manifest`), not a new engine writer surface. This keeps `tests/fixtures/canonical_audit_hash.txt` stable and the default audit writer unchanged. Inference, strongly supported by the existing `audit_trajectory_rows` precedent.

2. **Coverage.** Section 3.4 says "for each evaluated candidate." The artifact must cover accepted, rejected, superseded, merged, and invalid candidates per round, not just accepted ones. Evidence: paper text + existing proposal `status` enum in `_proposal_status`.

3. **Cross-artifact bindings.** At minimum: (a) round alignment with `proposer_llm_request_log`, `proposer_context_manifest`, and `fixed_protocol_config.self_harness_rounds`; (b) per-round attempted/committed counts vs. `fixed_protocol_config.proposal_width`; (c) accepted/merged candidates per round must equal subsequent-round `proposer_context_manifest.previous_attempted_edits` for matching `proposal_round_index` (binding on `audit_decision`, `targeted_mechanism_sha256`, `edited_surface_sha256`); (d) split-wise held-in/held-out pass counts for the committed baseline and committed candidate arms must reconcile with `live_two_repeat_evaluation_report.per_task_attempts` partitioned by `live_terminal_bench_split_manifest`.

4. **Closed-set shape.** Top-level and per-proposal field sets must be closed (matching `_LIVE_TWO_REPEAT_EVALUATION_REPORT_FIELDS` style) so unreviewed derived metrics cannot enter evidence. `reproduction_claimed` must be false.

## Required Changes
None blocking. The plan below is executable as written.

## Revised Plan

**P84 — proposal_validation_manifest artifact and Section 3.4 cross-artifact binding**

### Files
- `src/self_harness/_artifact_shapes.py`: add `_PROPOSAL_VALIDATION_MANIFEST_FIELDS`, `_PROPOSAL_VALIDATION_ROUND_FIELDS`, `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS`, `_proposal_validation_manifest` validator; register in `REPRODUCTION_ARTIFACT_CLASS_VALIDATORS`; add to `EXTRACTABLE_ARTIFACT_CLASSES` semantics by reusing the validator.
- `src/self_harness/capture_extract.py`: add `extract_proposal_validation_manifest(audit_run_dir, *, capture_envelope, fixed_protocol_sha256, split_manifest_result)` and dispatch entry in `extract_artifact_from_paths`; extend `EXTRACTABLE_ARTIFACT_CLASSES`.
- `src/self_harness/reproduction_bundle.py`: add `_cross_artifact_proposal_validation_round_alignment`, `_cross_artifact_proposal_validation_outcomes`, `_cross_artifact_proposal_validation_previous_edits`; invoke from `_cross_artifact_invariants`.
- `src/self_harness/capture_manifest_diff.py`: add `proposal-validation-derivation` finding (planned validation manifest's per-round committed decisions vs. realized proposer_context previous edits).
- `src/self_harness/capture_manifest_build.py`: extend `_planned_artifact_stub` for `proposal_validation_manifest`.
- `docs/operations/benchmark_reproduction_requirements.json`: add requirement row `proposal_validation_records` citing Section 3.4, `required_artifact_class: proposal_validation_manifest`, `required_state: provisioned`, with notes pointing to the four cross-artifact bindings.
- `docs/architecture/schema_changelog.md`: P84 entry.
- `docs/architecture/productionization_brief.md`: P84 status entry.
- `scripts/capture_extract.py` and CLI: wire `--audit-run-dir`, `--proposal-validation-result` flag.
- `Makefile`: `capture-extract-check` extension; no default `check`-path dependency.

### Artifact shape (closed field set)
Top-level: `schema_version`, `ok`, `mode`, `capture_run_id`, `round_count`, `rounds`, `fixed_protocol_sha256`, `reproduction_claimed`, `boundary`.

Per round: `round_index`, `baseline_split_outcomes` (`held_in_passed`, `held_in_total`, `held_out_passed`, `held_out_total`, `evaluation_repeats`), `candidates` (list), `committed_proposal_ids`, `merge_decision` (`accepted|rejected|none`).

Per candidate: `proposal_id`, `proposal_round_index`, `pattern_id`, `changed_surfaces`, `edited_surface_sha256`, `targeted_mechanism_sha256`, `summary_sha256`, `split_outcomes` (held_in/held_out passed/total + evaluation_repeats), `audit_decision` (`accepted|rejected|superseded|merged|invalid`), `decision_reason`, `rejection_reason` (required non-empty when decision ∈ {rejected, invalid, superseded}).

### Invariants
- `mode == "live"`, `reproduction_claimed is False`.
- `round_count` and `rounds[].round_index` contiguous from 0; equal to `proposer_llm_request_log.round_count` and `fixed_protocol_config.self_harness_rounds` when those are co-bundled.
- `len(candidates)` per round equal `fixed_protocol_config.proposal_width` when proposer log is present.
- Accepted+merged `proposal_id` set per round equal to `committed_proposal_ids`.
- For every non-initial round, `proposer_context_manifest.rounds[round_index].previous_attempted_edits` must reference the prior round's `candidates` with matching `proposal_round_index`, `targeted_mechanism_sha256`, `edited_surface_sha256`, and `audit_decision`.
- `baseline_split_outcomes.held_in_passed + held_out_passed` must equal `live_two_repeat_evaluation_report.pass_count` when split manifest is present (partition by `held_in_task_ids`).
- Each candidate's `split_outcomes.evaluation_repeats` must equal `live_two_repeat_evaluation_report.attempts_per_task`.
- `fixed_protocol_sha256` matches `fixed_protocol_config` byte hash.

### Tests
- `tests/test_reproduction_readiness.py`: extend `_class_shaped_payloads` with `proposal_validation_manifest`; rotate expected release-candidate, reproduction-readiness, capture-manifest, capture-rehearsal fixture hashes.
- New `tests/test_proposal_validation_manifest.py`:
  - happy path: full bundle verifies clean.
  - rejected candidate missing `rejection_reason` → fail closed.
  - candidate `audit_decision` outside enum → fail closed.
  - `committed_proposal_ids` disagree with accepted set → fail.
  - round count drift vs. proposer log → fail.
  - attempted proposals drift vs. `proposal_width` → fail.
  - previous-edit binding: missing prior candidate, mismatched `targeted_mechanism_sha256`, mismatched `audit_decision` → fail.
  - split outcomes disagree with `live_two_repeat_evaluation_report` partitioned by split manifest → fail.
  - `fixed_protocol_sha256` drift → fail.
- `tests/test_capture_manifest.py`: extend `_class_shaped_payloads`; add coverage-violation diff test (`proposal-validation-derivation` finding) and skip-when-absent test.
- Engine integration: existing `tests/test_engine_*` keep passing; canonical audit hash unchanged because no writer change.
- `make capture-extract-check` covers the new extractor with synthetic audit directory fixtures.

### Fixture rotations
- Rotate: `tests/fixtures/release_candidate/expected_hash.txt`, capture-manifest fixture hash, capture-rehearsal fixture hash, reproduction-readiness fixture hash (because new artifact is a strict paper class).
- Do NOT rotate: `tests/fixtures/canonical_audit_hash.txt`, `tests/fixtures/canonical_llm_audit_hash.txt` (engine writes unchanged; recorder opt-in unchanged).

### Docs
- Update `docs/architecture/schema_changelog.md` with P84 entry following the existing template.
- Update `docs/architecture/productionization_brief.md` P84 block with explicit "no live Harbor/Docker/Trivy/PyPI/Sigstore/registry/scanner-db/model contact, no audit schema change, no corpus schema change, no canonical hash rotation, no benchmark reproduction claim."

### Stop conditions
- Stop before adding any live execution path.
- Stop before changing the default audit writer or default release path.
- Stop before rotating the canonical paper-fidelity audit hash.
- Stop before extending `fixed_protocol_config` schema (already carries `self_harness_rounds` and `proposal_width`).

### reproduction_ready:false preservation
- Default release path: `reproduction_ready:false` unchanged.
- New artifact is `required_state: provisioned` in the requirements catalog; readiness matrix rows stay `blocked` by default.
- Capture rehearsal reports `reproduction_ready:false` even when synthetic bundle verifies.
- Hard reproduction release-candidate path (`release-candidate-evidence-reproduction`) still blocked until operator supplies signed live bundle including `proposal_validation_manifest`.

## Remaining Open Questions
1. Should the per-candidate `summary_sha256` be computed from a canonical JSON of `{rationale, expected_effect, regression_risks}` (mirroring `_proposal_row`), or only from `rationale`? — Non-blocking; recommendation is the full triple for paper-faithful attestation, with the choice documented in the schema changelog.
2. Should `superseded` candidates be allowed to omit `rejection_reason`? — Non-blocking; recommendation is to require it (matches the existing audit-writer `_decision_reason("superseded")` non-empty behavior).
3. Should `proposal_validation_manifest` also bind `live_harbor_audit` per-task verifier outcomes for the committed candidate arm? — Non-blocking; deferred to a future slice because the two-repeat evaluation already cross-binds audit outcomes and adding it here would duplicate P70 semantics.
