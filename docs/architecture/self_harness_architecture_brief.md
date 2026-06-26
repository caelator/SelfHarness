# Self-Harness Architecture Brief

## Source Grounding

The project is based on the June 2026 arXiv paper "Self-Harness: Harnesses That Improve Themselves" (arXiv:2606.09498).

Core mechanism from the paper:

- A fixed model runs under a current harness on held-in tasks and held-out tasks.
- Execution traces and verifier outcomes are recorded.
- Weakness mining clusters failed traces by verifier-grounded signatures:
  terminal verifier cause, causal status of agent behavior, and reusable agent-side mechanism.
- Harness proposal uses the same fixed model, in proposer role, to generate bounded candidate edits.
- Candidates must target declared editable harness surfaces and include audit metadata:
  targeted failure pattern, edited surface, expected behavioral effect, and regression risks.
- Proposal validation evaluates each candidate under the same protocol.
- Acceptance rule: accept only if the candidate improves at least one split and does not degrade the other.
- Multiple compatible accepted candidates can be merged. Rejected candidates remain logged.

Paper constraints to preserve:

- Model, evaluator, benchmark protocol, decoding budget, and tool set should stay fixed during validation.
- Harness changes must be bounded and auditable, not broad uncontrolled rewrites.
- Held-out traces must not be exposed to the proposer.
- The artifact should make every transition reversible and evidence-backed.

## MVP Goal

Create a Python implementation in an empty workspace that demonstrates the Self-Harness loop end-to-end without requiring API keys:

1. Core dataclasses for tasks, traces, run records, failure patterns, proposals, harness specs, evaluation results, and lineage records.
2. A declared editable harness spec with safe surfaces such as:
   system prompt, bootstrap instruction, execution instruction, verification instruction, failure recovery instruction, and runtime control policy.
3. Weakness miner that deterministically clusters failed run records by failure signature.
4. Proposer interface with a deterministic local heuristic proposer for the demo, plus clean extension points for LLM-backed proposers later.
5. Validator implementing the paper's non-regression acceptance rule.
6. A runnable toy benchmark that simulates common terminal-agent failure modes:
   missing required artifact, repeated failed command, late verification, and environment persistence.
7. CLI command `self-harness demo` that runs several rounds, writes JSONL/JSON audit artifacts, and prints a compact summary.
8. Tests for mining, proposal application, acceptance rule, and demo behavior.

## Draft Architecture

- `self_harness/types.py`: immutable-ish dataclasses and JSON helpers.
- `self_harness/harness.py`: editable harness spec, safe patch application, merge compatibility checks.
- `self_harness/mining.py`: failure signature clustering and pattern ranking.
- `self_harness/proposer.py`: proposer protocol and deterministic heuristic proposer.
- `self_harness/evaluation.py`: evaluator and acceptance logic.
- `self_harness/engine.py`: orchestration loop.
- `self_harness/demo.py`: deterministic toy tasks, simulated runner, demo config.
- `self_harness/cli.py`: command line entrypoint.
- `tests/`: focused pytest suite.

Risks and open questions for GLM critique:

- Is a toy deterministic runner sufficient for an MVP, or should we add a real subprocess/task runner immediately?
- Should patches be JSON-patch style, or domain-specific operations against harness surfaces?
- How should accepted edits merge safely when multiple proposals pass in the same round?
- What audit artifacts are essential to avoid creating an uninspectable self-modifying loop?
