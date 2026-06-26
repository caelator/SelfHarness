from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.scanner_db_update import (  # noqa: E402
    ScannerDbUpdateCommand,
    ScannerDbUpdateError,
    run_scanner_db_update,
    scanner_db_update_result_to_jsonable,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_scanner_db_update(
            ScannerDbUpdateCommand(
                cache_dir=args.cache_dir,
                db_registry_config_path=args.db_registry_config,
                additional_args=tuple(args.trivy_arg),
            ),
            dry_run=args.dry_run,
            trivy_binary=args.trivy_binary,
            timeout_seconds=args.timeout_seconds,
        )
        report = scanner_db_update_result_to_jsonable(result)
    except (OSError, ScannerDbUpdateError) as exc:
        report = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "scanner-db-update-error",
            "message": str(exc),
        }
    output = stable_json_dumps(report) + "\n"
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.get("ok") is True else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or dry-run a Trivy DB update command.")
    parser.add_argument("--cache-dir", type=Path, required=True, help="Trivy cache directory to update.")
    parser.add_argument(
        "--db-registry-config",
        type=Path,
        help="Operator-owned Trivy registry config used for scanner DB mirrors.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the DB update command without executing Trivy.")
    parser.add_argument("--trivy-binary", default="trivy", help="Trivy executable used for live DB updates.")
    parser.add_argument(
        "--trivy-arg",
        action="append",
        default=[],
        help="Additional argument passed to Trivy after --download-db-only. Use --trivy-arg=--flag for flags.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Live DB update subprocess timeout.")
    parser.add_argument("--result-out", type=Path, help="Optional path for the scanner DB update JSON result.")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
