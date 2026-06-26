#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.audit_verify_live import (  # noqa: E402
    LIVE_AUDIT_VERIFY_BOUNDARY,
    live_audit_verification_report_to_jsonable,
    verify_live_audit_run,
)
from self_harness.exceptions import AuditCorruptError  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_live_audit_run(
            args.audit_dir,
            live_harbor_audit=args.live_harbor_audit,
            provenance=args.provenance,
            provenance_signature=args.provenance_signature,
            public_key=args.public_key,
            require_signature=args.require_signature,
            strict_migration=not args.lenient_migration,
        )
    except AuditCorruptError as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "audit-corrupt",
            "message": str(exc),
            "reproduction_claimed": False,
            "boundary": LIVE_AUDIT_VERIFY_BOUNDARY,
        }
        print(stable_json_dumps(payload))
        return 3
    payload = live_audit_verification_report_to_jsonable(report)
    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    if args.json:
        print(output, end="")
    else:
        status = "passed" if report.ok else "blocked"
        print(f"Live audit verification {status}: {args.audit_dir}")
        print(f"Mode: {report.mode}")
        print(f"Report hash: {report.report_hash}")
        if args.out is not None:
            print(f"Report: {args.out}")
    return 0 if report.ok else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify an audit directory against signed live Harbor provenance.")
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--live-harbor-audit", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--provenance-signature", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--lenient-migration",
        action="store_true",
        help="allow unknown migration_provenance fields when present",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
