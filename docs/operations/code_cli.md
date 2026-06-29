# SelfHarness Code CLI

`self-harness code` is the operator-facing terminal UI for day-to-day coding. It has two planes:

- The chat plane sends normal text to the configured coding backend.
- The control plane handles slash commands locally before anything reaches the backend.

Type `/` at the prompt to open the slash-command menu. Use Up/Down to move through commands, then
Enter to accept the highlighted command. Continue typing, such as `/mo`, to filter the list. `/menu`
also opens the command palette with numbered selection controls for the major functions.

## Provider And Model

The active provider can be changed without restarting the CLI.

```text
/model
/model codex gpt-5.6 xhigh
/provider claude
/effort high
```

Supported main providers:

- `glm`: GLM 5.2 through Z.ai.
- `codex`: local `codex exec` in headless mode.
- `agy`: local `agy --print` in headless mode.
- `claude`: local `claude --print` in headless mode.

After a provider is selected, the model picker queries the provider's live catalog and displays the
returned models. It uses `agy models` for Agy, Z.ai's coding-plan `/models` endpoint for GLM, and the
server-provided Codex model cache for Codex. Codex can also fall back to OpenAI `/v1/models`; Claude
uses Anthropic `/v1/models` when `ANTHROPIC_API_KEY` is available. If discovery is not available,
choose `custom` to enter a model id without leaving the TUI.

Reasoning effort is provider-scoped and model-aware. Codex supports `none`, `minimal`, `low`,
`medium`, `high`, and `xhigh`; Claude supports `low`, `medium`, `high`, `xhigh`, and `max`.
GLM/Z.ai exposes effort for models that advertise it through Z.ai model metadata. Agy exposes effort
as part of model choice text rather than a separate flag.

Persistent defaults:

```bash
self-harness settings set code_provider codex
self-harness settings set code_model gpt-5.6
self-harness settings set code_effort xhigh
self-harness settings set glm_retry_max_attempts 4
self-harness settings set glm_request_min_interval_seconds 1.5
```

The older `settings set model codex|agy|claude|glm-5.2` compatibility path still works, but
`code_provider`, `code_model`, and `code_effort` are the preferred settings.

## GLM Rate Limits And Helper Delegation

When GLM/Z.ai returns explicit rate-limit or overload markers such as `[1302]`, HTTP `429`, `rate
limit`, `too many requests`, or `overloaded`, SelfHarness keeps GLM as the active provider and retries
with bounded exponential backoff. The retry policy is configurable without editing code:

```bash
self-harness settings set glm_retry_max_attempts 4
self-harness settings set glm_retry_base_backoff_seconds 5
self-harness settings set glm_retry_max_backoff_seconds 60
self-harness settings set glm_request_min_interval_seconds 1.5
```

There is no silent provider fallback. If Z.ai is still rate-limiting after retries, wait and send
`continue`, lower request pressure, or explicitly delegate one helper subtask:

```text
/helpers
/helpers on
/delegate codex inspect the failing test output and summarize the likely fix
```

Helpers are opt-in and disabled by default. `/delegate` can use `codex`, `agy`, or `claude` when the
corresponding CLI is installed or configured with `SELF_HARNESS_CODEX_BINARY`,
`SELF_HARNESS_AGY_BINARY`, or `SELF_HARNESS_CLAUDE_BINARY`. Helper output is appended to the current
conversation as context for the main provider; it does not change `/whoami`, `/status`, or the
configured provider/model.

## Threads

Threads are saved conversations under `runs/sessions/`.

```text
/threads
/thread new
/thread switch <id-or-number>
/thread list
```

Switching threads saves the current thread first, then loads the selected thread's history into the
active backend object. It does not restart the terminal.

## Runtime Controls

```text
/config      edit max steps, tool timeout, harvesting, or model/provider
/whoami      show active provider, configured model, effort, and transport
/status      show cwd, session store, thread id, harness hash, provider/model, and budgets
/history     show recent turns
/report      report a semantic/control-plane UX issue for secondary judging
/feedback    alias for /report
/harvested   list command bundles and admitted UX reports
/rejected    list rejected UX captures and admission reasons
/helpers     list/toggle optional helper CLI delegation
/delegate    pass one subtask to an enabled helper CLI
/save        write the current thread now
/clear       clear the terminal
/reset       clear current thread history
```

The exact identity questions `what model are you`, `what model are you using`, and related
provider/backend variants are answered locally by the control plane. This keeps the reported
provider/model tied to the active SelfHarness configuration rather than a model's self-description.

Ctrl-C behavior:

- At the prompt: exits cleanly after saving the current thread.
- During a running turn: interrupts the turn and returns to the prompt. The interrupted turn is not
  recorded as a completed model turn.

Esc behavior:

- In nested menus: returns one step up, such as model list -> provider list or effort picker -> model list.
- At the top of a menu: returns to the chat prompt.

Exit commands:

```text
/exit
/quit
/q
:q
```

## Safety

The coding backend can execute commands on the host. Run `self-harness code` only in workspaces you
trust. Harvesting is enabled by default; failing test/build/check commands are written to the shared
inbox so the continuous improvement loop can learn from real failures.

## Semantic UX Harvesting

The command harvester remains unchanged: a failing check/build/test command becomes a legacy inbox
bundle with the command as the success criterion.

Semantic/control-plane failures use a separate `ux_complaint` bundle:

```json
{
  "id": "report-20260629T120000Z-ux-01",
  "kind": "ux_complaint",
  "trigger": "provider-identity-contradiction",
  "observation": "GLM via Z.ai answered that it was Claude.",
  "expected_behavior": "Identity answers should come from SelfHarness runtime state.",
  "observed": "I'm Claude, made by Anthropic.",
  "checkable_criterion": "Asking what model is active reports provider glm from runtime state.",
  "metadata": {
    "operating_provider": "glm",
    "admitting_judge": "codex",
    "trigger_kind": "provider-identity-contradiction",
    "admission_reason": "checkable identity contradiction"
  }
}
```

Manual reports:

```text
/report model identity contradicted the provider picker
/report
```

The short form uses the supplied text as the observation. Bare `/report` opens a short structured
flow for the problem, expected behavior, observed behavior, and optional checkable criterion.

Automatic UX candidates are raised quietly for explicit user corrections, provider/model identity
contradictions, invalid provider/model/effort state, repeated identical failing tool calls,
apology/hedge final responses after failures, and max-step exhaustion.

UX candidates are double gated:

1. A randomly selected secondary judge provider, excluding the active coding provider, must return an
   admitted verdict with a concrete `checkable_criterion`.
2. When the continuous loop drains the inbox, the UX task must pass the existing fresh solve+verify
   guard before it is added to learned held-in tasks.

If no non-active judge is available, the judge call fails twice, the judge rejects the candidate, or
the solve+verify guard fails, the bundle is moved to `runs/inbox/processed/*.rejected` for audit. Use
`/rejected` or `/harvested --rejected` to inspect rejected semantic captures from the current session.
