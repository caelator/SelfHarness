# Round 1

CONVERGED: YES

## Verdict
The proposed P0 productionization slice is the correct scope. It hardens the package boundaries, introduces explicit configuration, and adds standard CI/linting without expanding the functional scope prematurely (e.g., not adding the LLM/subprocess runners yet). The architecture questions regarding `src/` layout, tooling, and API boundaries can be resolved definitively here, allowing the plan to proceed to execution.

## Critique
- **Layout**: The project currently uses a flat layout (`self_harness/` at root). For a production package, a `src/` layout is strongly recommended to prevent local imports from shadowing installed packages during testing.
- **Configuration**: The `SelfHarnessEngine` currently accepts loose arguments (`evaluation_repeats`, `proposal_budget`). Wrapping these in an immutable `EngineConfig` is the right move, but must be done carefully to avoid breaking the existing `demo.py` and `cli.py` entry points.
- **Tooling**: The codebase is already typed (`from __future__ import annotations`, dataclasses). Adding `mypy` and `ruff` poses no architectural risk and provides immediate safety nets.
- **Audit Schema**: The brief mentions a "clear protocol version" but the current `manifest.json` only has a `protocol_hash`. A backward-compatible `schema_version` field should be introduced in the manifest and the JSONL row schemas.

## Required Changes
1. **Adopt `src/` Layout**: Move the package to `src/self_harness/`. Update `pyproject.toml` packaging targets and test configuration accordingly.
2. **Schema Versioning**: Add a `schema_version: "1.0"` field to the manifest and all audit row dictionaries (proposals, evaluations, lineage) to future-proof the artifacts.
3. **Strict API Definition**: Explicitly declare the following as the stable public API: `EngineConfig`, custom exceptions, the `Runner` and `Proposer` protocols, and the core `types.py` dataclasses.

## Revised Plan
### Phase 1: Refactoring & Layout
1. Move code to `src/self_harness/`.
2. Update `pyproject.toml` to support `src/` layout and add dev dependencies (`pytest`, `ruff`, `mypy`, `build`).

### Phase 2: Core API Hardening
1. Create `self_harness/config.py` with a frozen `EngineConfig` dataclass (fields: `rounds`, `evaluation_repeats`, `proposal_budget`, `protocol_version`, `model_id`).
2. Create `self_harness/exceptions.py` (`InvalidConfigError`, `InvalidPatchError`, `InvalidProposalError`, `EvaluationError`).
3. Update `SelfHarnessEngine` to accept `EngineConfig`. Maintain backwards compatibility by allowing keyword arguments to fall back to defaults if `config` is not provided.

### Phase 3: Audit & Schema Updates
1. Add `schema_version` to `manifest.json`.
2. Add `schema_version` to all output rows in `engine.py` (`_proposal_row`, `_evaluation_rows`).

### Phase 4: Tooling & CI
1. Add `ruff` configuration to `pyproject.toml` (target Python 3.11).
2. Add `mypy` configuration to `pyproject.toml`.
3. Add `.github/workflows/ci.yml` running `ruff check`, `mypy src/`, and `pytest`.
4. Create a `Makefile` with `make lint`, `make typecheck`, `make test`, `make build`.

### Phase 5: CLI & Docs
1. Expand `cli.py` to wire `--rounds`, `--seed`, `--evaluation-repeats`, etc., into `EngineConfig`.
2. Add tests for `EngineConfig` validation and invalid proposal handling.
3. Update `README.md` with dev setup instructions and clear delineation of stable APIs vs internal engine logic.

## Remaining Open Questions
None. The scope is clear, the layout decision is resolved (move to `src/`), and the API boundaries are sufficiently defined by the existing dataclasses and protocols. The plan is actionable.

[usage] {"completion_tokens": 1727, "completion_tokens_details": {"reasoning_tokens": 853}, "prompt_tokens": 10542, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 12269}
