# LLM Proposer Integration

`LLMProposer` is the provider-neutral implementation of the paper's Harness
Proposal stage. It calls an `LLMClient` with the current harness, declared
editable surfaces, held-in failure evidence, held-in passing summaries, and
previous attempted edits.

The reference provider adapter is optional:

```bash
python -m pip install -e '.[anthropic]'
```

```python
from pathlib import Path

from self_harness.adapters.llm import AnthropicClaudeClient
from self_harness.config import EngineConfig
from self_harness.engine import SelfHarnessEngine
from self_harness.llm_proposer import LLMProposer

client = AnthropicClaudeClient("claude-sonnet-4-5")
proposer = LLMProposer(client)

engine = SelfHarnessEngine(
    tasks=tasks,
    runner=runner,
    proposer=proposer,
    out_dir=Path("runs/llm-proposer"),
    config=EngineConfig(rounds=3, model_id="claude-sonnet-4-5"),
)
engine.run()
```

Set `ANTHROPIC_API_KEY` before constructing `AnthropicClaudeClient`. The
adapter retries 429 and 5xx responses, reports 4xx responses as typed
`LLMRequestError` exceptions, and can report token counts through the
`on_usage` callback.

Paper-fidelity guardrails:

- The proposer prompt contains only held-in failure patterns and held-in passing
  summaries.
- Each valid proposal must reference a held-in pattern from the proposer
  context.
- Duplicate primary targets in one LLM response are marked invalid with
  `diversity_collision`.
- Fabricated pattern IDs are marked invalid with `ungrounded_proposal`.
- Invalid LLM suggestions are written to the normal proposal audit stream; they
  are not evaluated or promoted.

The reference adapter is not a reproduction claim. Terminal-Bench reproduction
still requires a provisioned Harbor/Docker environment and live benchmark
execution.
