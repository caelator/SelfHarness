# Web Interface

SelfHarness ships a small stdlib web operator console:

```bash
self-harness ui --host 127.0.0.1 --port 8765 --root . --runs-dir runs
```

The console can start deterministic harness runs, list completed audit runs,
show aggregate held-in and held-out scores, inspect trajectory rows, and inspect
the final retained harness surfaces. It does not claim benchmark reproduction.

To use Z.ai GLM-5.2 as the live proposer backend, set operator-held secrets
outside Git:

```bash
export ZAI_API_KEY="<operator-secret>"
export ZAI_BASE_URL="https://api.z.ai/api/paas/v4"
self-harness ui --proposer glm --host 127.0.0.1 --port 8765 --root . --runs-dir runs
```

For a remote host such as `minerva`, keep the UI bound to localhost and open it
through an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 minerva
```

Then browse to:

```text
http://127.0.0.1:8765
```
