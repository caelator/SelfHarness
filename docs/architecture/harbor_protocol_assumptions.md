# Harbor Protocol Assumptions

## Documented Surface

Current Harbor examples use this command shape:

```bash
harbor run \
  --dataset terminal-bench@2.0 \
  --agent claude-code \
  --model anthropic/claude-opus-4-1 \
  --n-concurrent 4
```

Cloud execution examples add:

```bash
--env daytona
```

`HarborCommandSpec` treats these fields as the documented protocol core:

- `dataset`
- `agent_name`
- `model`
- `n_concurrent`
- optional `cloud_env`

## Inferred Surface

Self-Harness still needs two extension points before a real benchmark run can
fully mirror the paper's DeepAgent harness evolution:

- `task_ids`, currently rendered as repeated `--task <id>` flags;
- `agent_config_path`, currently rendered as `--agent-config <path>`.

These fields are intentionally isolated in `HarborCommandSpec`. If first live
Harbor execution shows a different spelling or mechanism, only the command
builder and adapter need to change.

## Structured Output

`HarborRunResult` currently parses a Self-Harness V1 JSON shape:

```json
{
  "task_id": "held-out-smoke",
  "passed": true,
  "terminal_cause": "verifier-pass",
  "mechanism": "verifier",
  "verifier_output": "ok",
  "container_digest": "sha256:..."
}
```

Plain text output falls back to exit-code interpretation and is not sufficient
for paper-style reproduction claims. Any future reproduction claim must be
backed by complete structured verifier and provenance data.

## Image Policy

`terminal-bench` can enforce operator-owned image policy material with
`--image-policy`, `--trust-container-image`,
`--trust-container-image-digest`, and `--require-image-digest`. Policy schema
and operator-pinned digest checks run before the engine. Live Harbor output is
then checked after each task invocation using the parsed `container_digest`.

Harbor output does not currently provide a stable container image name in the
supported structured shape, so image-name binding is supplied by the operator.
This keeps the trust boundary explicit and avoids treating benchmark manifests
or Harbor output as policy authorities.

## Artifact Inspection

P11 adds `harbor-inspect` and `harbor-ingest` for preserved Harbor run
directories. The artifact layout is still candidate until a real run is
captured and reviewed. See `docs/architecture/harbor_artifact_layout.md`.
