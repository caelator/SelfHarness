# P7 Terminal-Bench Live Preflight Brief

## Status

GLM convergence completed in `docs/architecture/glm_p7_live_preflight_plan.md`.
P7 is implemented.

Implemented:

- `terminal_bench.preflight` typed preflight reports;
- `terminal-bench-preflight` CLI;
- live `terminal-bench --mode live` preflight gate before engine execution;
- `terminal-bench-capture` CLI and single-task capture scaffold;
- synthetic Harbor capture test path;
- capture-artifact no-reproduction invariant;
- documentation for missing-runtime behavior.

## Runtime Evidence

On the current machine, Docker CLI exists but the daemon is not reachable, and
`harbor` / `uv` are not installed. A live benchmark run is therefore not
available here. P7 converts that into a structured `preflight.json` artifact
instead of producing misleading benchmark evaluation rows.

## Production Meaning

Live execution now has an explicit gate. If required runtime dependencies are
missing, the command exits before creating audit rounds. On a provisioned host,
the capture command can record one live Harbor task as a replayable dry-run
fixture for future parser hardening.

## Remaining Limits

P7 still does not include a captured live Harbor run, full task-set execution,
corpus signing, cross-Python readiness matrix, or major-version migration shim.
