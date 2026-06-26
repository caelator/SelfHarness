#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.operator_policy_binding import (  # noqa: E402
    policy_binding_report_to_jsonable,
    verify_policy_binding,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify_policy_binding(
        args.bundle,
        args.promotion,
        signature_path=args.signature,
        trusted_public_key=args.trusted_public_key,
        today=date.fromisoformat(args.today) if args.today is not None else None,
    )
    payload = policy_binding_report_to_jsonable(report)
    output = stable_json_dumps(payload) + "\n"
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.ok else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify operator policy bundle paths against promotion manifest digests."
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--promotion", type=Path, required=True)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--trusted-public-key", type=Path)
    parser.add_argument("--today", help="Evaluation date for bundle expiry checks, as YYYY-MM-DD.")
    parser.add_argument("--result-out", type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
