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

Supported providers:

- `glm`: GLM 5.2 through Z.ai.
- `codex`: local `codex exec` in headless mode.
- `agy`: local `agy --print` in headless mode.
- `claude`: local `claude --print` in headless mode.

Persistent defaults:

```bash
self-harness settings set code_provider codex
self-harness settings set code_model gpt-5.6
self-harness settings set code_effort xhigh
```

The older `settings set model codex|agy|claude|glm-5.2` compatibility path still works, but
`code_provider`, `code_model`, and `code_effort` are the preferred settings.

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
/status      show cwd, session store, thread id, harness hash, provider/model, and budgets
/history     show recent turns
/save        write the current thread now
/clear       clear the terminal
/reset       clear current thread history
```

Ctrl-C behavior:

- At the prompt: exits cleanly after saving the current thread.
- During a running turn: interrupts the turn and returns to the prompt. The interrupted turn is not
  recorded as a completed model turn.

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
