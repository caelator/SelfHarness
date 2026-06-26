# Benchmark Report Schema

`self-harness benchmark-report` writes schema `1.0`.

```json
{
  "schema_version": "1.0",
  "reproduction_claimed": false,
  "provenance_per_model": {
    "glm": {
      "model_id": "harbor-live-runner",
      "model_version": "anthropic/claude-opus-4-1",
      "decoding_config": {},
      "harbor_version": "captured-live",
      "dataset_version": "terminal-bench@2.0",
      "corpus_hash": "terminal-bench@2.0",
      "container_image_digest": "sha256:...",
      "task_split_assignment": {}
    }
  },
  "per_model_summary": {},
  "per_task_breakdown": {},
  "split_gains": {}
}
```

The report is derived from existing audit directories. It does not re-run tasks
and defaults to `reproduction_claimed=false`.

Reproduction claims require complete provenance. Incomplete values such as
`unknown-live`, `dry-run`, or missing container digests are rejected by
`validate_provenance_completeness()`.
