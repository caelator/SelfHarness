# Web Interface

SelfHarness ships a self-contained operator console served by the Python stdlib
HTTP server (no build step). Alpine.js is **vendored and served locally** from
`/static/alpine-3.14.1.min.js`, so the console works fully offline; if the script
ever fails to load, a visible banner explains it and points at the JSON API rather
than rendering a blank page.

```bash
self-harness ui --host 127.0.0.1 --port 8765 --root . --runs-dir runs
```

Agentic and dev-task features additionally accept `--max-steps`,
`--tool-timeout-seconds`, `--codex-binary`, and `--harness-state` (path to the
evolving harness lineage; default `<runs-dir>/harness_state.json`). Pass
`--no-auto-promote` to stop reviewer-approved edits from being written back into
`harness.py` automatically (see *Promote evolved harness to source* below).

## Console features

The console has three top-level views — **Runs**, **Dev task**, and **Chat**:

### Runs

1. **Run launcher** — start a run with every engine knob (rounds, seed, evaluation
   repeats, max proposals, max payload bytes) and harness-lineage controls. The
   console launches **agentic** runs: GLM 5.2 solves a real task corpus with
   `bash`/`read_file`/`write_file` tools and the Codex CLI judges each result, so
   promoted edits change genuine pass rates. Commands run on the host (no
   container); only run trusted corpora.
   - **Harness lineage** — *evolve from persisted* (default) starts each run from
     the last promoted harness so the harness improves across runs/sessions;
     **Reset** discards the lineage back to `initial_harness()` (Figure 3).
2. **Overview** — final held-in/held-out pass rates, accept/reject counts, GLM
   token usage.
3. **Trajectory** — per-round step view with deltas and accept/merge/carry badges.
4. **Round drill-down** — the mined evidence bundle `B_t` (failure patterns with
   full `(c, q, m)` signatures) and every proposal with rationale, expected effect,
   regression risks, split deltas, and decision.
5. **Harness diff** — initial (Figure 3) vs final promoted surfaces, with the
   **Promote → source** integration described below.

### Dev task

Hand GLM 5.2 a free-form development task: instructions + Codex success criteria,
optional inline workspace files, or "use the SelfHarness repo as the workspace"
(GLM edits a *copy* of this repo, never the live tree). GLM solves it with real
tools under the current evolving harness, Codex judges, and the console shows the
verdict, step/tool counts, final message, and full trajectory. No harness mutation.

### Chat

A direct GLM 5.2 chat panel for talking to and directing the model, independent of
the harness loop. Single-shot calls carry conversation context; token usage is
reported per turn.

A GLM status banner reflects live reachability (operational / needs funding /
unreachable). The console never claims Terminal-Bench reproduction.

### Promote evolved harness to source

When a run's acceptance gate (the "reviewer": `Δin≥0 ∧ Δho≥0 ∧ max(Δin,Δho)>0`)
promotes at least one edit, that evolved harness is **integrated into source
automatically** — written back into `initial_harness()` in
`src/self_harness/harness.py`, closing the self-improvement loop into real code
with no separate manual approval. There is no approval gate on *whether* to
integrate an approved edit; there is only a **correctness** gate on *how*: the
write backs up the original to `harness.py.bak`, rewrites the marker-delimited
block, then runs ruff + mypy + an import round-trip that confirms the rewritten
`initial_harness()` reconstructs the promoted spec. If that gate fails, the source
is restored automatically, so a bad rewrite is never left in the tree. The console
flashes the outcome and the Harness-diff tab notes that integration is automatic.

Auto-integration is on by default. Launch with `--no-auto-promote` to disable it
(the run still persists its evolving lineage, but source is left untouched); the
Harness-diff tab then offers **Preview diff** and a manual **Integrate into
harness.py** button driven by the same correctness-gated path.

### JSON API

The console is backed by a small JSON API you can also use directly:

```text
GET  /                                the console (loads /static/alpine-3.14.1.min.js)
GET  /static/<asset>                  vendored front-end assets (allowlisted)
GET  /api/state                       overall state, run list, proposer mode, harness lineage
GET  /api/preflight                   GLM 5.2 reachability (dry-run or live)
GET  /api/runs/<id>                   run summary, trajectory, harness inspection, token usage
GET  /api/runs/<id>/rounds/<n>        round patterns, proposals, evaluations
GET  /api/runs/<id>/harness           initial vs final harness surfaces
POST /api/runs                        start a run (engine knobs + run_mode + evolve)
POST /api/dev-task                    GLM solves one described task; Codex judges
POST /api/chat                        single-shot GLM 5.2 chat with history
POST /api/harness/reset               discard the evolving harness lineage
POST /api/runs/<id>/promote-to-source render/diff (and apply by default; pass {"apply": false} for a preview)
```

## GLM 5.2 proposer backend

To use Z.ai GLM 5.2 as the live proposer backend, set operator-held secrets
outside Git. The default endpoint is the **GLM Coding Plan** (Anthropic-compatible
Messages API):

```bash
export ZAI_API_KEY="<operator-secret>"
self-harness ui --proposer glm --host 127.0.0.1 --port 8765 --root . --runs-dir runs
```

The harness auto-selects the wire format from the endpoint:

- **Coding Plan (subscription)** — `https://api.z.ai/api/anthropic` (the default).
  Served on Z.ai's Anthropic-compatible Messages API; authenticated with
  `ZAI_API_KEY` via `x-api-key`. This is what a GLM Coding Plan subscription uses.
- **Pay-as-you-go PaaS** — set `ZAI_BASE_URL=https://api.z.ai/api/paas/v4` to use
  the OpenAI-compatible `/chat/completions` endpoint instead (requires prepaid
  account balance).

If a live check returns `code 1113 "Insufficient balance"`, you are pointed at the
PaaS endpoint without prepaid balance — switch to the coding-plan endpoint (unset
`ZAI_BASE_URL` or set it to `https://api.z.ai/api/anthropic`).

### Verifying GLM connectivity

Check the GLM backend without launching the console using the `model-preflight`
command:

```bash
# Offline replay against a recorded fixture (no network):
self-harness model-preflight --backend glm --mode replay

# Live reachability against the real Z.ai endpoint (coding plan by default):
ZAI_API_KEY=<secret> self-harness model-preflight --backend glm --mode live
```

`--mode live` contacts the provider and reports the exact result. A successful
chat completion (`ok: true`) means GLM 5.2 is fully operational. The console
surfaces the same status as an "operational" banner.

## Remote access

For a remote host such as `minerva`, keep the UI bound to localhost and open it
through an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 minerva
```

Then browse to:

```text
http://127.0.0.1:8765
```
