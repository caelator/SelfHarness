# Harbor Artifact Layout

This document is provisional. The first real `harbor-inspect` output from a
provisioned Harbor run supersedes these inferred paths.

## Inferred Trial Directory

```text
<run-dir>/
  <task-id>/
    <attempt-index>/
      metadata.json
      reward.json
      reward.txt
      trajectory.jsonl
```

- `metadata.json` `[inferred]`: may contain `task_id`.
- `reward.json` `[from-harbor-docs/inferred-shape]`: reward value as a number,
  `{"reward": <float>}`, `{"score": <float>}`, or `{"passed": <bool>}`.
- `reward.txt` `[from-harbor-docs]`: reward value as plain text.
- `trajectory.jsonl` `[from-harbor-docs]`: ATIF-style interaction history. P11
  preserves each JSONL row as a generic `TraceEvent`; richer typing waits for a
  real sample.

## Validation Status

- `candidate`: all required inferred markers were found, but the layout has not
  been validated against a real Harbor run.
- `partial`: one or more required markers are missing.
- `validated`: reserved for a future round after a real Harbor artifact tree is
  inspected and accepted.

Only `validated` artifacts with complete model, Harbor, dataset, corpus, and
container provenance may support `reproduction_claimed=true`.
