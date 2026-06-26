CONVERGED: YES

## Verdict
The gap is well-bounded, the paper semantics are explicit (Section 3.4: validation rejects proposals that "do not modify any editable surface or fail execution before a valid evaluation result"), and the fix is a minimal additive schema slice on `proposal_validation_manifest` plus a relaxation rule on `changed_surfaces`. No new artifact class, no audit schema bump, no readiness hash rotation, no reproduction-claim change, and no live dependency is required. The plan below is execution-ready.

## Critique
- **Evidence (repo):** `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS` in `_artifact_shapes.py` currently has no machine-checkable failure category; only free-text `decision_reason` / `rejection_reason`. `_proposal_validation_candidate()` enforces `_non_empty_unique_string_list(..., "changed_surfaces", ...)`, so the schema literally cannot represent a no-surface invalid candidate.
- **Evidence:** `capture_extract._proposal_changed_surfaces()` raises `CaptureExtractError` whenever neither `changed_surfaces` nor `surface` is present, so even if the schema accepted empty lists the extractor would still reject the row. Both layers must change together.
- **Evidence:** P86 architect explicitly flagged "Should P87 introduce a structured `failure_category` enum on proposal-validation candidates" as the next open question, so this is the natural successor slice.
- **Inference:** Paper Section 3.4 names exactly two invalid-proposal causes: (a) does not modify an editable surface; (b) fails execution before valid evaluation. A closed two-value enum `validation_failure_category ∈ {"no_editable_surface", "execution_failure"}` is therefore paper-faithful and minimal.
- **Inference:** The relaxation must be scoped. An `accepted`/`rejected`/`superseded`/`merged` candidate with empty `changed_surfaces` would be nonsensical (the audit only ever evaluates candidates that produced a diff against `harness_before.json`). So the empty-list allowance applies *only* when `audit_decision == "invalid"` and `validation_failure_category == "no_editable_surface"`. Execution-failure invalid candidates may still record the surface they attempted to modify.
- **Inference:** `validation_failure_category` should be `null` (or absent) for every non-invalid candidate. Treating it as optional-and-nullable keeps the change back-compatible with existing fixtures and avoids forcing a `schema_version` bump on `proposal_validation_manifest`.
- **Architecture risk:** Low. Purely additive shape rule plus one new cross-field invariant inside the existing `_cross_artifact_proposal_validation_binding` check. Bundle report hashes for fixtures that exercise the new field will rotate; canonical paper-fidelity audit hash is untouched because no engine-side default audit output changes.

## Required Changes
1. Add optional nullable `validation_failure_category` field to `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS`.
2. In `_proposal_validation_candidate()`:
   - Allow `validation_failure_category` to be `null` or one of `{"no_editable_surface", "execution_failure"}`.
   - Require non-null `validation_failure_category` when `audit_decision == "invalid"`.
   - Require `null` when `audit_decision in {"accepted", "rejected", "superseded", "merged"}`.
   - Relax `changed_surfaces` to permit empty list **only** when `audit_decision == "invalid"` and `validation_failure_category == "no_editable_surface"`; otherwise keep the current non-empty rule.
   - When `changed_surfaces` is empty, also relax `edited_surface_sha256` by allowing a deterministic zero-information hash (e.g., sha256 of `{"changed_surfaces": []}`) so the existing required-hash shape invariant remains type-safe.
3. Extend `_proposal_changed_surfaces()` in `capture_extract.py`:
   - Accept empty `changed_surfaces`/missing `surface` when the audit proposal `status == "invalid"`.
   - When `status == "invalid"`, infer `validation_failure_category` deterministically from the audit row: empty proposed surfaces/no surface → `no_editable_surface`; otherwise → `execution_failure`. Do not parse free-text `rejection_reason`.
   - For non-invalid statuses, keep current behavior and emit `validation_failure_category: null`.
4. Extend `_proposal_validation_candidate()` in `capture_extract.py` to set `validation_failure_category` on the emitted row.
5. Extend `_cross_artifact_proposal_validation_binding` in `reproduction_bundle.py` with one cross-field invariant block:
   - For every candidate: `audit_decision == "invalid"` ⇔ `validation_failure_category in {"no_editable_surface", "execution_failure"}`.
   - For every candidate with `validation_failure_category == "no_editable_surface"`: `changed_surfaces` must be empty.
   - For every candidate with `validation_failure_category == "execution_failure"`: `changed_surfaces` may be non-empty (attempted surface) and no pass-count comparison is required.
   - Record violations in a new metadata bucket `validation_failure_category_violations` (per-round, per-candidate).
   - Do **not** apply the P86 acceptance-rule check to invalid candidates (already exempt today; reaffirm in metadata boundary text).
6. Update `capture_manifest_build._planned_artifact_stub()` for `proposal_validation_manifest` so at least one fixture round includes an `invalid` candidate with `validation_failure_category: "no_editable_surface"` and empty `changed_surfaces`. This keeps the rehearsal bundle honest about the new schema surface.
7. Update `tests/test_reproduction_readiness.py` fixtures (`_proposal_validation_candidate()` helper) to add at least one `invalid` candidate per round in the round-1 / round-2 case using the new category, and to leave `validation_failure_category: null` on accepted/rejected rows.
8. Add tests in `tests/test_reproduction_readiness.py`:
   - Happy path: bundle with an invalid no-surface candidate verifies.
   - Rejected non-invalid candidate carrying a non-null category fails.
   - Invalid candidate missing category fails.
   - Invalid `no_editable_surface` candidate with non-empty `changed_surfaces` fails.
   - Invalid `execution_failure` candidate still records a non-regression pass-count comparison exemption.
9. Add tests in `tests/test_capture_extract.py`:
   - Audit row with `status == "invalid"` and no `changed_surfaces`/`surface` extracts as `no_editable_surface` with empty `changed_surfaces`.
   - Audit row with `status == "invalid"` and a `surface` extracts as `execution_failure` with that surface preserved.
   - Audit row with `status == "accepted"` and no `changed_surfaces` still raises (no relaxation leak).
10. Documentation:
    - Append P87 entry to `docs/architecture/productionization_brief.md` using the P84–P86 template, stating the two paper-derived categories, the scoped `changed_surfaces` relaxation, the capture-extract inference rule, the back-compat behavior for old audit rows, and the explicit out-of-scope items.
    - Update `docs/operations/benchmark_reproduction_requirements.json` `proposal_validation_records.notes` to mention the new `validation_failure_category` field.
11. Stop conditions / explicit non-goals:
    - No semantic parsing of free-text `rejection_reason` beyond the deterministic surface-presence inference.
    - No new artifact class.
    - No audit directory schema bump (audit `schema_version` stays at `1.4`).
    - No `proposal_validation_manifest.schema_version` bump (stays `1.0`; field is additive nullable).
    - No readiness hash rotation, no live Harbor/Docker/model/PyPI/Sigstore contact, no reproduction-claim change.

## Revised Plan
**P87 — proposal_validation_manifest no-surface invalid-candidate representation**

Files:
- `src/self_harness/_artifact_shapes.py`
  - Add `"validation_failure_category"` to `_PROPOSAL_VALIDATION_CANDIDATE_FIELDS`.
  - Define `_PROPOSAL_VALIDATION_FAILURE_CATEGORIES = frozenset({"no_editable_surface", "execution_failure"})`.
  - In `_proposal_validation_candidate()`:
    - Read `category = value.get("validation_failure_category")`.
    - Validate `category in (None, *_PROPOSAL_VALIDATION_FAILURE_CATEGORIES)`.
    - If `audit_decision == "invalid"`: require `category in _PROPOSAL_VALIDATION_FAILURE_CATEGORIES`.
    - Else: require `category is None`.
    - Replace `_non_empty_unique_string_list(value, "changed_surfaces", label)` with a conditional check:
      - If `audit_decision == "invalid"` and `category == "no_editable_surface"`: allow empty list; require list type and uniqueness when non-empty.
      - Else: keep current non-empty unique check.
    - When `changed_surfaces` is empty: allow `edited_surface_sha256` to equal `_EMPTY_EDITED_SURFACE_SHA256` (constant = sha256 of `{"changed_surfaces": []}\n`). Otherwise keep existing strict sha256 check.
- `src/self_harness/capture_extract.py`
  - In `_proposal_validation_candidate()`:
    - Compute `category` via new helper `_infer_validation_failure_category(row, status)`: returns `None` for non-invalid statuses; for invalid statuses returns `"no_editable_surface"` iff `_proposal_changed_surfaces(...)` would be empty, else `"execution_failure"`.
    - Refactor `_proposal_changed_surfaces()` to accept `allow_empty` flag; raise only when empty is disallowed.
    - Add `"validation_failure_category": category` to the returned dict.
  - `edited_surface_sha256` continues to use `_stable_payload_sha256({"changed_surfaces": changed_surfaces})`, which is deterministic for the empty case.
- `src/self_harness/reproduction_bundle.py`
  - Extend `_cross_artifact_proposal_validation_binding()`:
    - For each candidate, read `audit_decision` and `validation_failure_category`.
    - Append to `validation_failure_category_violations` when:
      - `audit_decision == "invalid"` and category missing/unknown.
      - `audit_decision != "invalid"` and category not null.
      - `category == "no_editable_surface"` and `changed_surfaces` non-empty.
    - Add `validation_failure_category_violations` to metadata.
    - Extend boundary note: "invalid candidates are exempt from the acceptance-rule check; their failure category records the paper Section 3.4 no-surface vs execution-failure distinction."
- `src/self_harness/capture_manifest_build.py`
  - In `_planned_artifact_stub()` proposal_validation_manifest branch, add a second candidate per round with `audit_decision: "invalid"`, `validation_failure_category: "no_editable_surface"`, `changed_surfaces: []`, `edited_surface_sha256: <empty-hash>`, `rejection_reason: "planned invalid no-surface candidate"`, and ensure `committed_proposal_ids` does not include it.
- `tests/test_reproduction_readiness.py`
  - Update `_proposal_validation_candidate()` fixture helper to include `validation_failure_category` (null for accepted/rejected).
  - Update `_proposal_validation_rounds()` to include one invalid no-surface candidate in at least one round.
  - Add tests listed in Required Changes #8.
- `tests/test_capture_extract.py`
  - Add tests listed in Required Changes #9.
  - Update `_audit_proposal()` to optionally emit `status == "invalid"` without `changed_surfaces`/`surface` for the no-surface case.
- `docs/architecture/productionization_brief.md`
  - Append P87 entry using the P84–P86 template; cite Section 3.4 of the paper.
- `docs/operations/benchmark_reproduction_requirements.json`
  - Extend `proposal_validation_records.notes` to mention the new field and the two paper-derived categories.

Invariants enforced after P87:
- `audit_decision == "invalid"` ⇔ `validation_failure_category ∈ {"no_editable_surface", "execution_failure"}`.
- `validation_failure_category == "no_editable_surface"` ⇒ `changed_surfaces == []`.
- `validation_failure_category == "execution_failure"` ⇒ no acceptance-rule comparison; `changed_surfaces` may be non-empty.
- All non-invalid candidates: `validation_failure_category is null` and `changed_surfaces` non-empty.
- Invalid candidates remain exempt from the P86 acceptance-rule check.

## Remaining Open Questions
- Should `proposal_validation_manifest.schema_version` eventually bump to `1.1` to make the additive field formally part of the versioned contract? **Non-blocking**: the additive-nullable pattern matches existing P72/P76 precedent and the bundled-shape validator gates the field either way. Revisit if a future breaking change to proposal-validation evidence forces a bump.
- Should `live_harbor_audit` or `live_two_repeat_evaluation_report` ever surface invalid-candidate trial artifacts so execution-failure invalid candidates can be bound to a raw trace? **Non-blocking** and explicitly out-of-scope: P84/P86 already declared per-candidate raw trace binding as future work, and P87 does not require it.
- If the release-candidate evidence fixture hash rotates because the rehearsal bundle now carries an invalid candidate, is a changelog note required beyond the P87 brief entry? **Non-blocking**: follow the existing P46/P57/P85 precedent of noting rotations inline.
