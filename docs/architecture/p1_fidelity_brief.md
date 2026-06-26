# P1 Fidelity Brief

## Status

GLM convergence completed in `docs/architecture/glm_p1_fidelity_plan.md`.
The P1 implementation slice is complete:

- Added `tools`, `skills`, `memory_sources`, and `subagents` to `HarnessSpec`.
- Added `AppendToListSurface` with snapshot reverse semantics.
- Bumped default audit schema to `1.1`.
- Added manifest `surface_kinds`.
- Added `ProposalPolicy`, addressability filtering, and diversity filtering
  keyed by pattern, surface, op, and payload hash.
- Added tests for list surfaces, schema `1.1`, policy filtering, deterministic
  subagent serialization, and unchanged toy demo outcomes.

## Current Verified State

The project has a production package foundation and a paper-faithful toy
implementation of the Self-Harness algorithmic protocol. P0 production and P0
paper-fidelity work are complete:

- `src/` layout, package metadata, CI, Makefile, ruff, mypy, pytest, build.
- Immutable `EngineConfig` and project exceptions.
- Repeated evaluation with aggregate pass-count validation.
- Held-in-only proposer context with failure patterns, passing summaries, and
  attempted edits.
- Schema-versioned manifest, proposal rows, evaluation rows, and lineage.
- Bounded reversible patch DSL and deterministic audit artifacts.

## Remaining Paper-Fidelity Gaps

The paper's initial DeepAgent-style harness exposes more declared surfaces than
the current toy `HarnessSpec`. It includes prompts/instructions plus tools,
skills, memory sources, subagents, and runtime control policy. The current code
only models text instructions and `runtime_policy`.

The paper also says the proposer should select target failure patterns only when
they are supported by evidence and plausibly addressable by editable surfaces,
and candidates should be materially distinct rather than restatements of the
same cluster/surface/mechanism.

## Proposed P1 Implementation Slice

1. Expand `HarnessSpec` with inert-but-declared production surfaces:
   - `tools: list[str]`
   - `skills: list[str]`
   - `memory_sources: list[str]`
   - `subagents: list[dict[str, Any]]`
2. Expand patch DSL:
   - keep `AppendToSurface`, `ReplaceSurface`, `SetPolicy`;
   - add `AppendToListSurface` for list-valued surfaces;
   - keep append-to-text restricted to text surfaces.
3. Update `EDITABLE_SURFACES`, manifests, replacement logic, reverse patches,
   merge compatibility, and tests.
4. Add proposal policy:
   - `ProposalPolicy` with `min_pattern_support` and `require_distinct_surfaces`;
   - `is_addressable(pattern, editable_surfaces)` helper;
   - `select_actionable_patterns(context, policy)` helper;
   - `ensure_diverse(proposals, policy)` helper.
5. Update `HeuristicProposer` to use proposal policy before mapping patterns to
   proposals. Default support threshold should remain `1` for toy/demo behavior,
   but production callers can raise it.
6. Add tests proving:
   - new surfaces appear in default harness and manifest;
   - list append/reversal works;
   - text/list op mismatch is rejected;
   - policy can filter unsupported/unaddressable patterns;
   - diversity can suppress same-surface duplicate proposals.

## Constraints

- Do not add a real Terminal-Bench runner in this slice.
- Do not change the toy demo outcomes.
- Keep artifacts deterministic.
- Preserve backward constructor compatibility where reasonable.

## GLM Question

Converge whether this P1 slice is the right next step toward production readiness
with fidelity to the original Self-Harness paper. If yes, confirm the exact
surface/DSL/policy changes. If no, return the smallest corrected implementation
slice.
