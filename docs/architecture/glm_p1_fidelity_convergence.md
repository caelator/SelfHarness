# Round 1

CONVERGED: NO

## Verdict
The P1 slice is correctly scoped and aligned with the paper-fidelity gaps. It is safe in direction but not yet execution-ready: three material risks are under-specified (artifact/hash stability after adding surfaces, the diversity rule, and subagent-list merge/reverse semantics). Round 1 critique produces a revised plan that resolves these without needing new repository evidence.

## Critique
Evidence (from repo):
- `HarnessSpec` is a frozen dataclass with six fields; `harness_hash` is `sha256(stable_json_dumps(spec))`, so any new field changes every harness hash, lineage hash, and byte-stable demo artifact.
- `EDITABLE_SURFACES` and `OP_WHITELIST` are exported into `manifest.json`, so adding surfaces/ops is observable and version-relevant.
- `apply_op` returns a single reverse `HarnessOp`; `AppendToListSurface` needs a well-defined reverse (previous list snapshot) or the reverse-patch contract breaks.
- `structurally_mergeable` only permits same-surface merges when both ops are `AppendToSurface`; list-surface appends need an analogous rule or merge-group selection silently drops valid proposals.
- `HeuristicProposer._proposals_for_pattern` already encodes an implicit mechanism→surface map; the proposed `is_addressable` helper must formalize this or the policy and proposer will disagree.

Inferences / concerns:
- Adding `tools/skills/memory_sources/subagents` with defaults keeps constructor compatibility but will break any byte-stable artifact tests. The brief says "keep artifacts deterministic" and "do not change toy demo outcomes" — these conflict with surface expansion unless tests assert on structural invariants rather than literal hashes.
- `require_distinct_surfaces` is ambiguous: distinct by `primary_op.surface`, by `changed_surfaces`, by pattern mechanism, or by patch payload equivalence? The current proposer already emits two proposals targeting `bootstrap` for `missing_artifact`; a naïve "distinct surfaces" rule would drop one intentionally.
- `subagents: list[dict[str, Any]]` plus `AppendToListSurface` raises deep-merge / identity questions (do subagents have IDs? can two appends of the same subagent collide?).

## Required Changes
1. Make artifact-stability an explicit, testable decision: bump `schema_version` to `1.1`, document that harness hashes change in this slice, and rewrite any byte-stable demo test to assert structural determinism (same inputs → same outputs within a version) rather than frozen hash literals.
2. Specify the diversity rule precisely: default `require_distinct_surfaces` should key on `(pattern_id, primary_op.surface, primary_op.op)` and suppress only proposals that are restatements of the same surface+op+pattern, not proposals that legitimately share a surface for the same pattern. Surface-only distinctness must be opt-in, not default.
3. Specify `AppendToListSurface` reverse semantics: reverse op captures the full previous list value via `ReplaceSurface`-style snapshot on the same surface, and validation rejects the op when the surface's current value is not a list.
4. Extend `structurally_mergeable` so two `AppendToListSurface` ops on the same list surface are mergeable, and so `AppendToListSurface` is never mergeable with `SetPolicy`/text ops on the same surface.
5. Formalize `is_addressable(pattern, editable_surfaces)` as a pure function derived from the existing mechanism→surface table used by `HeuristicProposer`, so the proposer and policy cannot drift.
6. Decide subagent identity: in this slice, treat `subagents` as an opaque list of dicts with no identity-based dedup; `AppendToListSurface` simply appends. Note deep-merge as future work.
7. Default `ProposalPolicy(min_pattern_support=1, require_distinct_surfaces=False)` to preserve current toy outcomes; production callers raise both.

## Revised Plan
P1 slice (round 2 candidate):

1. **Surfaces (declared, inert)**
   - Add to `HarnessSpec` with defaults: `tools: list[str] = []`, `skills: list[str] = []`, `memory_sources: list[str] = []`, `subagents: list[dict[str, Any]] = []`.
   - Add `LIST_SURFACES = {"tools", "skills", "memory_sources", "subagents"}`; extend `EDITABLE_SURFACES` accordingly.
   - `initial_harness()` returns empty lists for the new surfaces (no behavior change on `ToyRunner`).

2. **Patch DSL**
   - Add `AppendToListSurface` to `OP_WHITELIST`.
   - `validate_op`: `AppendToListSurface` requires `surface in LIST_SURFACES` and payload type matching the surface element type (`str` for tools/skills/memory_sources; `dict` for subagents).
   - `apply_op` for `AppendToListSurface`: `next_value = list(current) + [payload]`; reverse op is `ReplaceSurface(surface, list(current))`.
   - `structurally_mergeable`: two `AppendToListSurface` on same list surface → mergeable; `AppendToListSurface` vs any other op on same surface → not mergeable.

3. **Schema versioning and artifacts**
   - Bump `schema_version` to `"1.1"`.
   - Update manifest to emit `surface_whitelist`, `op_whitelist`, and a new `surface_kinds` map (`{surface: "text"|"list"|"policy"}`).
   - Replace byte-stable hash assertions in demo tests with structural-determinism assertions plus a recorded `schema_version` check.

4. **Proposal policy module** (`proposer_policy.py` or extend `proposer.py`)
   - `ProposalPolicy(min_pattern_support: int = 1, require_distinct_surfaces: bool = False)`.
   - `ADDRESSABLE_SURFACE_BY_MECHANISM`: canonical map extracted from `HeuristicProposer._proposals_for_pattern`.
   - `is_addressable(pattern, editable_surfaces) -> bool`: True iff the pattern's mechanism maps to at least one editable surface present in `editable_surfaces`.
   - `select_actionable_patterns(patterns, policy) -> list[FailurePattern]`: filter `pattern.support >= min_pattern_support` and `is_addressable`.
   - `ensure_diverse(proposals, policy) -> list[Proposal]`: when `require_distinct_surfaces` is True, drop proposals duplicating an already-kept proposal's `(pattern_id, primary_op.surface, primary_op.op)`.

5. **Proposer integration**
   - `HeuristicProposer` accepts an optional `ProposalPolicy` (default preserves today's behavior).
   - Call `select_actionable_patterns` before `_proposals_for_pattern`; call `ensure_diverse` after budget filtering.

6. **Engine wiring**
   - `SelfHarnessEngine` accepts optional `proposal_policy` and forwards to proposer only when the proposer supports it (keep `Proposer` protocol unchanged; add a `policy` setter or construct `HeuristicProposer(policy=...)` at engine init for the built-in path).
   - No change to acceptance rule, evaluation, audit shape beyond new schema version and new surface kinds.

7. **Tests**
   - New surfaces appear in default harness, manifest, and `surface_kinds`.
   - `AppendToListSurface` append + reverse round-trip restores prior spec byte-for-byte.
   - Type-mismatched payload (str on `subagents`, dict on `tools`) is rejected.
   - Text op on list surface and list op on text surface are both rejected.
   - Two list appends on same surface merge; list-append vs `SetPolicy` on same surface does not.
   - Policy with `min_pattern_support=2` filters toy support-1 patterns.
   - Policy with `require_distinct_surfaces=True` suppresses only exact `(pattern, surface, op)` duplicates, not the two intentional `bootstrap` proposals for `missing_artifact`.
   - Toy demo still yields ≥1 accepted and ≥1 rejected proposal; artifact hashes may change but are deterministic within schema `1.1`.

## Remaining Open Questions
- Should `AppendToListSurface` support batch append (`payload: list`) in this slice, or defer? Recommendation: defer; keep single-element append.
- Should `runtime_policy` eventually move to a structured `Policy` surface kind, or remain a dict? Non-blocking for P1.
- Is there a future need for `RemoveFromListSurface` / `ReplaceListItem`? Non-blocking; note as P2.
- Should `ProposalPolicy` live on `EngineConfig` or on the proposer? Recommendation: proposer-owned, engine-passed; revisit if a second proposer implementation arrives.

[usage] {"completion_tokens": 2492, "completion_tokens_details": {"reasoning_tokens": 646}, "prompt_tokens": 9876, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 12368}

---

# Round 2

CONVERGED: YES

## Verdict
The round 2 candidate plan resolves all three material risks (artifact/hash stability, diversity rule semantics, list-surface reverse/merge semantics) without requiring new repository evidence. The plan is safe, actionably scoped, preserves toy demo outcomes via conservative defaults, and routes production-only behavior behind opt-in policy flags. It is ready to execute.

## Critique
Round 1 closed the architecture-level gaps. Round 2 verification against repository facts confirms the plan is execution-ready:

- (Evidence) `harness_hash` is `sha256(stable_json_dumps(spec))`; the plan's schema bump to `1.1` plus structural-determinism tests correctly resolves the conflict between "keep artifacts deterministic" and "expand surfaces."
- (Evidence) `_replace_surface` currently only handles `TEXT_SURFACES ∪ {"runtime_policy"}`; the revised plan implicitly requires extending it to the new list surfaces. This is an implementation task, not a blocking architecture decision — the reverse-op contract (`ReplaceSurface(surface, list(current))`) is well-defined.
- (Evidence) `_ops_mergeable` currently returns `left.op == right.op == "AppendToSurface"`; the plan's extension to two same-surface `AppendToListSurface` ops is a direct, low-risk generalization.
- (Evidence) `HeuristicProposer._proposals_for_pattern` hardcodes mechanism→surface mapping; extracting it into `ADDRESSABLE_SURFACE_BY_MECHANISM` and `is_addressable` is the right factoring to prevent drift.
- (Evidence) `Proposer` is a `Protocol` with a single `propose(context)` method; passing policy via constructor on `HeuristicProposer` preserves the protocol and avoids forcing all proposers to accept policy — correct.

Inference: no further external review or experiment is required. The remaining open questions (batch append, structured `Policy` surface kind, list-item ops, `ProposalPolicy` ownership) are all marked non-blocking with recommendations.

## Architecture Risks
- **List-surface reverse payloads.** `validate_op` currently restricts `ReplaceSurface` payloads to `str | dict`. Implementation must widen this for list surfaces; otherwise the round-trip test will fail. Non-blocking at architecture level but a concrete implementation hazard.
- **Determinism of `subagents: list[dict[str, Any]]` in `stable_json_dumps`.** Already handled by existing `to_jsonable` recursion over dicts; no new risk, but worth a smoke test that a spec with a populated `subagents` list serializes deterministically.
- **Diversity rule interaction with intentional same-surface proposals.** The `missing_artifact → bootstrap` pair is preserved because the rule keys on `(pattern_id, surface, op)` — both proposals share that tuple? No: they share `surface` and `op` but the plan explicitly says suppress only exact duplicates; the two intentional proposals have distinct `rationale`/`payload` but identical `(pattern_id, surface, op)`. **Correction needed at implementation:** the diversity key must include a payload signature (e.g. hash of `str(payload)`), not just `(pattern_id, surface, op)`, or the second `bootstrap` proposal will be dropped when `require_distinct_surfaces=True`. This is a refinement of the policy, not an architecture blocker — tests will catch it.

## Recommended Next Moves
1. Proceed to implementation. Treat the round 2 revised plan as the authoritative P1 slice.
2. During implementation, refine `ensure_diverse`'s de-duplication key to `(pattern_id, surface, op, payload_hash)` so production-mode diversity cannot drop intentionally distinct same-surface proposals.
3. Add one smoke test asserting that `stable_json_dumps(HarnessSpec(... subagents=[{"id": "x"}] ...))` is byte-stable across runs (guards against future dict-ordering regressions).
4. Defer all "Remaining Open Questions" to P2 unless a downstream consumer forces one of them.

[usage] {"completion_tokens": 1155, "completion_tokens_details": {"reasoning_tokens": 314}, "prompt_tokens": 13594, "prompt_tokens_details": {"cached_tokens": 64}, "total_tokens": 14749}
