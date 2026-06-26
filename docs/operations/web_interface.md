# Web Interface

SelfHarness ships a self-contained operator console served by the Python stdlib
HTTP server (no build step; the single-page app loads Alpine.js from a CDN for
reactivity and degrades to a JSON-API notice without JavaScript):

```bash
self-harness ui --host 127.0.0.1 --port 8765 --root . --runs-dir runs
```

## Console features

The console is a single-page app with five views:

1. **Run launcher** — start a run with every engine knob exposed (rounds, seed,
   evaluation repeats, max proposals, max payload bytes) and a proposer selector
   (heuristic toy proposer or GLM 5.2).
2. **Overview** — final held-in/held-out pass rates, accept/reject counts, and
   GLM token usage (input/output/total) when a GLM run was executed.
3. **Trajectory** — per-round step view with held-in/held-out deltas and
   accept/merge/carry-forward badges; click a round to drill in.
4. **Round drill-down** — the mined evidence bundle `B_t` (failure patterns with
   their full `(c, q, m)` signature and supporting task ids), and every proposal
   with its rationale, expected effect, regression risks, split deltas, and
   accept/reject decision.
5. **Harness diff** — the initial (Figure 3) harness surfaces side-by-side with
   the final promoted harness, with changed surfaces highlighted.

A GLM status banner reflects live reachability (operational / needs funding /
unreachable). The console never claims Terminal-Bench reproduction.

### JSON API

The console is backed by a small JSON API you can also use directly:

```text
GET  /api/state                       overall state, run list, proposer mode
GET  /api/preflight                   GLM 5.2 reachability (dry-run or live)
GET  /api/runs/<id>                   run summary, trajectory, harness inspection, token usage
GET  /api/runs/<id>/rounds/<n>        round patterns, proposals, evaluations
GET  /api/runs/<id>/harness           initial vs final harness surfaces
POST /api/runs                        start a run (JSON body of engine knobs)
```

## GLM 5.2 proposer backend

To use Z.ai GLM 5.2 as the live proposer backend, set operator-held secrets
outside Git:

```bash
export ZAI_API_KEY="<operator-secret>"
export ZAI_BASE_URL="https://api.z.ai/api/paas/v4"
self-harness ui --proposer glm --host 127.0.0.1 --port 8765 --root . --runs-dir runs
```

### Verifying GLM connectivity

Check the GLM backend without launching the console using the `model-preflight`
command:

```bash
# Offline replay against a recorded fixture (no network):
self-harness model-preflight --backend glm --mode replay

# Live reachability against the real Z.ai endpoint:
self-harness model-preflight --backend glm --mode live
```

`--mode live` contacts the provider and reports the exact result. A successful
chat completion means GLM 5.2 is fully operational. A response carrying
`code 1113 "Insufficient balance or no resource package"` means the endpoint,
API key, and `glm-5.2` model id are all valid and accepted — the Z.ai **account
simply needs funding**. The console surfaces this as a distinct "needs funding"
status rather than "unreachable".

### Funding GLM 5.2

If `model-preflight --mode live` reports code `1113`, log into the Z.ai console
for the account behind `ZAI_API_KEY` and add a balance / resource package. No
code or configuration change is required — live completions begin working as
soon as the account balance is positive.

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
