#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.adapters.container_preflight import run_container_preflight  # noqa: E402
from self_harness.adapters.terminal_bench.preflight import PreflightReport  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402

CONTAINER_PREFLIGHT_BOUNDARY = (
    "release/operator container preflight only; offline mode checks local Docker CLI discovery and skips "
    "daemon or image probes, live mode is operator-owned Docker evidence, and neither mode is benchmark "
    "reproduction evidence"
)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    live = args.mode == "live"
    report = run_container_preflight(
        args.image,
        docker_executable=args.docker_executable,
        require_daemon=live,
        require_image_present=live and args.require_image_present,
    )
    payload = _report_to_jsonable(report, mode=args.mode, image=args.image)
    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    if live and not report.passed:
        return 2
    return 0


def _report_to_jsonable(report: PreflightReport, *, mode: str, image: str) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "dataset": report.dataset,
        "mode": mode,
        "image": image,
        "passed": report.passed,
        "ok": report.passed,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "required": check.required_for_live,
                "required_for_live": check.required_for_live,
            }
            for check in report.checks
        ],
        "reproduction_claimed": False,
        "boundary": CONTAINER_PREFLIGHT_BOUNDARY,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a container verifier preflight surface report.")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--image", default="registry.example/trusted/verifier:1")
    parser.add_argument("--docker-executable", default="docker")
    parser.add_argument(
        "--require-image-present",
        action="store_true",
        help="In live mode, require the configured image to already be present locally.",
    )
    parser.add_argument("--out", type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
