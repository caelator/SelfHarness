from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.harbor_discovery import (  # noqa: E402
    HarborDiscoveryCommand,
    HarborDiscoveryError,
    harbor_discovery_result_to_jsonable,
    run_harbor_discovery,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_harbor_discovery(
            HarborDiscoveryCommand(
                url=args.url,
                project=args.project,
                repository=args.repository,
                reference=args.reference,
                authorization_header=_authorization_header(args.authorization_env),
            ),
            dry_run=args.dry_run,
            replay_response=args.replay,
            timeout_seconds=args.timeout_seconds,
        )
        report = harbor_discovery_result_to_jsonable(result)
    except (OSError, HarborDiscoveryError) as exc:
        report = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "harbor-discovery-error",
            "message": str(exc),
        }
    output = stable_json_dumps(report) + "\n"
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.get("ok") is True else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover Harbor artifact digests before scanner or Harbor runs.")
    parser.add_argument("--url", required=True, help="Harbor base URL.")
    parser.add_argument("--project", required=True, help="Harbor project name.")
    parser.add_argument("--repository", required=True, help="Harbor repository path inside the project.")
    parser.add_argument("--reference", required=True, help="Artifact reference: tag or digest.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print the Harbor API request without network access.")
    mode.add_argument(
        "--replay",
        type=Path,
        help="Parse a captured Harbor artifact JSON response instead of live HTTP.",
    )
    parser.add_argument(
        "--authorization-env",
        help="Environment variable containing the full Authorization header for live Harbor discovery.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=15, help="Live Harbor HTTP timeout.")
    parser.add_argument("--result-out", type=Path, help="Optional path for the Harbor discovery JSON result.")
    return parser


def _authorization_header(env_name: str | None) -> str | None:
    if env_name is None:
        return None
    value = os.environ.get(env_name)
    if value is None:
        raise HarborDiscoveryError(f"authorization environment variable is not set: {env_name}")
    if not value:
        raise HarborDiscoveryError(f"authorization environment variable is empty: {env_name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
