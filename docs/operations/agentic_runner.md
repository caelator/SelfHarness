# Agentic Runner (production-grade GLM 5.2 evaluation)

The `glm-agentic-demo` command runs Self-Harness with a **real agentic runner**: GLM 5.2 actually
solves each task as a tool-using agent under the candidate harness, and a real verifier (the Codex
CLI) judges success. This is the difference between the deterministic demo and a production loop —
harness edits change genuine task-success rates, so the acceptance gate promotes edits that truly help.

```bash
export ZAI_API_KEY="<z.ai coding-plan key>"
self-harness glm-agentic-demo examples/agentic_corpus.json \
  --proposer glm --rounds 2 --evaluation-repeats 1 --max-steps 12 \
  --out runs/glm-agentic
```

## How it works

For each task attempt:

1. **Fresh workspace.** An isolated temp directory is created and seeded from the task's
   `workspace_files` (inline path → content) or `workspace_template` (a directory).
2. **Harness → system prompt.** `render_system_prompt` assembles the candidate harness's five
   instruction surfaces plus tools/skills/memory/runtime-policy into the agent's system prompt. This
   is the load-bearing link: every promoted edit changes the prompt the solver receives.
3. **Agentic loop.** GLM 5.2 acts with real tools — `bash`, `read_file`, `write_file` — executing in
   the workspace until it stops or hits the step/timeout budget. The full trajectory is recorded.
4. **Codex judge.** `codex exec --json -s read-only --cd <workdir> --output-schema <schema>` inspects
   the final workspace and returns a structured `{passed, reason}` verdict. The judge runs read-only
   and cannot modify the workspace.
5. **RunRecord.** A pass/fail record is emitted with deterministic `(terminal_cause, causal_status,
   mechanism)` signatures (so clustering produces real failure patterns) plus reward and token-usage
   metadata.

With `--proposer glm`, GLM 5.2 is also the harness-edit proposer, matching the paper's within-model
setup: the same fixed model both solves tasks and proposes edits to its own harness.

## Task corpus format

Each task declares a `success_criteria` string for the judge and optional inline `workspace_files`:

```json
{
  "id": "word-count",
  "split": "held_in",
  "failure_mode": "agentic_coding",
  "description": "Count words and write the count to answer.txt.",
  "metadata": {
    "instructions": "Count whitespace-separated words in input.txt and write the integer to answer.txt.",
    "success_criteria": "answer.txt exists and contains exactly the integer word count of input.txt.",
    "workspace_files": { "input.txt": "the quick brown fox\n" }
  }
}
```

The held-in / held-out partition is enforced (disjoint, non-empty held-out). A bundled example lives
at `examples/agentic_corpus.json`.

## Security boundary

> **The agent executes model-generated shell commands directly on the host** (no container). Run
> `glm-agentic-demo` only with **trusted corpora**, ideally signed (`--require-corpus-signature` /
> `--require-corpus-keyring`). Tool file operations are confined to the per-attempt workspace, but
> `bash` is not sandboxed beyond the working directory and a per-command timeout.

Task metadata cannot smuggle solver/judge configuration: keys like `api_key`, `base_url`, `model`,
and `codex_binary` are rejected at load time.

## Honesty and determinism

This is real agentic evaluation but **not** a Terminal-Bench reproduction — it uses a different task
set and the Codex judge rather than Harbor's deterministic verifiers. The `reproduction_claimed:
false` guard remains in force. Because the solver and judge are stochastic, agentic-runner audits are
**not byte-reproducible** — unlike the deterministic `demo`/`python-demo` runners, whose
canonical-hash determinism is unchanged. Use the deterministic runners when you need reproducible
audits; use the agentic runner when you need real task outcomes.

## Requirements

- **GLM 5.2** via the Z.ai coding plan (`ZAI_API_KEY`); see `docs/operations/web_interface.md`.
- **Codex CLI** (`codex`) on `PATH`, authenticated (`codex login`). Override the binary with
  `--codex-binary`. If the judge is unavailable, tasks fail closed with a distinct
  `codex-judge-unavailable` mechanism rather than silently passing.
