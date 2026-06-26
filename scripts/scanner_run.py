from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from vuln_check import run_vulnerability_check  # noqa: E402

from self_harness.freshness_policy import FreshnessPolicyError  # noqa: E402
from self_harness.image_policy import ImagePolicyError  # noqa: E402
from self_harness.scanner_db_freshness import (  # noqa: E402
    ScannerDbFreshnessError,
    load_scanner_db_freshness_policy,
)
from self_harness.scanner_execution import (  # noqa: E402
    ScannerCommand,
    ScannerExecutionError,
    run_scanner,
    scanner_run_result_to_jsonable,
)
from self_harness.types import stable_json_dumps  # noqa: E402
from self_harness.vulnerability_policy import VulnerabilityPolicyError  # noqa: E402

RESULT_SCHEMA_VERSION = "1.0"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        command = ScannerCommand(
            image=args.image,
            digest=args.digest,
            output_path=args.out,
            db_dir=args.db_dir,
            db_registry_config_path=args.db_registry_config,
            db_freshness_policy=(
                load_scanner_db_freshness_policy(args.db_freshness_policy)
                if args.db_freshness_policy is not None
                else None
            ),
            db_freshness_evaluated_at=_parse_today(args.today),
            additional_args=tuple(args.trivy_arg),
        )
        scanner_result = run_scanner(
            command,
            dry_run=args.dry_run,
            replay_report=args.replay,
            trivy_binary=args.trivy_binary,
            timeout_seconds=args.timeout_seconds,
        )
        vulnerability_report = None
        if scanner_result.ok and not args.dry_run:
            vulnerability_report = run_vulnerability_check(
                audit_json=args.out,
                report_format="trivy",
                policy_path=args.vuln_policy,
                image_policy_path=args.image_policy,
                freshness_policy_path=args.freshness_policy,
                today=args.today,
            )
        result = _combined_report(scanner_result, vulnerability_report)
    except (
        OSError,
        ScannerExecutionError,
        ScannerDbFreshnessError,
        VulnerabilityPolicyError,
        ImagePolicyError,
        FreshnessPolicyError,
    ) as exc:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "ok": False,
            "reason": "scanner-run-error",
            "message": str(exc),
        }
    output = stable_json_dumps(result) + "\n"
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result.get("ok") is True else 2


def _combined_report(
    scanner_result,
    vulnerability_report: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "ok": scanner_result.ok and (vulnerability_report is None or vulnerability_report.get("ok") is True),
        "scanner": scanner_run_result_to_jsonable(scanner_result),
        "vulnerability_report": vulnerability_report,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or replay a Trivy scan and evaluate Self-Harness policies.")
    parser.add_argument("--format", choices=["trivy"], default="trivy", help="Scanner format to run.")
    parser.add_argument("--image", required=True, help="Container image name or tag to scan.")
    parser.add_argument("--digest", help="Optional sha256 image digest; target becomes image@digest.")
    parser.add_argument("--out", type=Path, required=True, help="Path where the Trivy JSON report is written.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print the scanner command without executing Trivy.")
    mode.add_argument("--replay", type=Path, help="Copy an existing Trivy JSON report instead of executing Trivy.")
    parser.add_argument("--trivy-binary", default="trivy", help="Trivy executable used for live scanner runs.")
    parser.add_argument(
        "--db-dir",
        type=Path,
        help="Optional Trivy cache directory; live preflight checks DB metadata.",
    )
    parser.add_argument(
        "--db-registry-config",
        type=Path,
        help="Operator-owned Trivy registry config used for scanner DB mirrors or private images.",
    )
    parser.add_argument(
        "--trivy-arg",
        action="append",
        default=[],
        help="Additional argument passed to Trivy before the image reference. Use --trivy-arg=--flag for flags.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Live scanner subprocess timeout.")
    parser.add_argument("--image-policy", type=Path, help="Image policy used to validate report RepoDigests.")
    parser.add_argument("--freshness-policy", type=Path, help="Freshness policy used to validate report CreatedAt.")
    parser.add_argument("--db-freshness-policy", type=Path, help="Scanner DB freshness policy used during preflight.")
    parser.add_argument("--vuln-policy", type=Path, help="Vulnerability policy used to allow known findings.")
    parser.add_argument("--today", help="Override evaluation date for deterministic tests.")
    parser.add_argument("--result-out", type=Path, help="Optional path for the combined scanner/policy JSON result.")
    return parser


def _parse_today(value: str | None):
    if value is None:
        return None
    from datetime import date

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ScannerExecutionError("--today must use YYYY-MM-DD") from exc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
