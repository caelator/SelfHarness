#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.operator_promotion import (  # noqa: E402
    PROMOTION_BOUNDARY,
    promotion_verification_report_to_jsonable,
    verify_promotion_manifest,
)
from self_harness.types import stable_json_dumps  # noqa: E402

OPERATOR_PROMOTION_PREFLIGHT_SCHEMA_VERSION = "1.0"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify_promotion_manifest(
        args.promotion,
        signature_path=args.signature,
        trusted_public_key=args.trusted_public_key,
    )
    payload = {
        "schema_version": OPERATOR_PROMOTION_PREFLIGHT_SCHEMA_VERSION,
        "ok": report.ok,
        "promotion": promotion_verification_report_to_jsonable(report),
        "reproduction_claimed": False,
        "boundary": PROMOTION_BOUNDARY,
    }
    output = stable_json_dumps(payload) + "\n"
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.ok else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify operator policy promotion material without live services.")
    parser.add_argument("--promotion", type=Path, required=True)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--trusted-public-key", type=Path)
    parser.add_argument("--result-out", type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
