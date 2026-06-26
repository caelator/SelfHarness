#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.readiness_matrix import (  # noqa: E402
    ReadinessMatrixError,
    evaluate_readiness_matrix,
    load_readiness_matrix_catalog,
    readiness_matrix_report_to_jsonable,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = load_readiness_matrix_catalog(args.catalog)
        report = evaluate_readiness_matrix(catalog)
        payload = readiness_matrix_report_to_jsonable(report)
    except (OSError, ReadinessMatrixError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "error": str(exc),
            "reproduction_claimed": False,
            "boundary": (
                "readiness matrix report generation failed before live probing; no benchmark reproduction claimed"
            ),
        }
        output = _dumps(payload, pretty=args.pretty) + "\n"
        print(output, end="", file=sys.stderr)
        return 2

    output = _dumps(payload, pretty=args.pretty) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


def _dumps(payload: dict[str, object], *, pretty: bool) -> str:
    if pretty:
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
    return stable_json_dumps(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the offline live-dependency readiness matrix and write a release report."
    )
    parser.add_argument("--catalog", type=Path, default=Path("docs/operations/readiness_matrix.json"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
