#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.model_backend_preflight import (  # noqa: E402
    MODEL_BACKEND_PREFLIGHT_BOUNDARY,
    ModelBackendPreflightError,
    evaluate_model_backend_preflight,
    model_backend_preflight_report_to_jsonable,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate_model_backend_preflight(
            mode=args.mode,
            backend_ids=args.backend,
            env=os.environ,
            replay_path=args.replay,
            today=args.today,
        )
        payload = model_backend_preflight_report_to_jsonable(report)
    except (OSError, ModelBackendPreflightError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "mode": args.mode,
            "error": str(exc),
            "reproduction_claimed": False,
            "boundary": MODEL_BACKEND_PREFLIGHT_BOUNDARY,
        }
        output = stable_json_dumps(payload) + "\n"
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        print(output, end="", file=sys.stderr)
        return 3

    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.ok else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run paper model backend preflight checks.")
    parser.add_argument("--mode", choices=("dry-run", "replay", "live"), default="dry-run")
    parser.add_argument("--backend", choices=("all", "minimax", "qwen", "glm"), action="append", default=[])
    parser.add_argument("--replay", type=Path, help="Replay fixture file or directory.")
    parser.add_argument("--today", help="Optional YYYY-MM-DD stamp for deterministic operator reports.")
    parser.add_argument("--out", type=Path, default=Path("dist/self-harness-model-backend-preflight.json"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
