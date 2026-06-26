# Audit Trajectory

`self-harness audit-trajectory` derives a paper-style evolution trace from an
existing audit directory. It does not re-run tasks and does not change the
accepted harness lineage.

```bash
self-harness audit-trajectory runs/demo
```

By default the command writes:

```text
runs/demo/trajectory.jsonl
```

Each line is one round:

```json
{
  "schema_version": "1.0",
  "round": 0,
  "harness_before_hash": "...",
  "harness_after_hash": "...",
  "baseline_held_in_passed": 4,
  "baseline_held_out_passed": 2,
  "after_held_in_passed": 5,
  "after_held_out_passed": 2,
  "proposals": [
    {
      "id": "r00__held_in__missing_artifact__bootstrap_targeted",
      "status": "accepted",
      "pattern_id": "held_in__missing_artifact",
      "changed_surfaces": ["bootstrap"],
      "primary_op": "AppendToSurface",
      "score_held_in_delta": 1,
      "score_held_out_delta": 0,
      "decision_reason": "candidate improved held_in without degrading held_out"
    }
  ],
  "merged": false
}
```

This derived schema maps to the paper's harness evolution trajectory figures:

- green accepted steps: `status` in `accepted` or `merged`;
- gray rejected candidates: `status` is `rejected`;
- invalid candidates: `status` is `invalid` with a concrete
  `decision_reason`;
- flat lineage segments: unchanged `after_*_passed` values across rounds;
- branch/merge behavior: `merged=true` and proposal rows with `status=merged`.

The file is stable JSONL with sorted keys and no timestamps. When it is written
inside a run directory, it is part of the audit tree hash.
