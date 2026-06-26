#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.capture_manifest_diff import (  # noqa: E402
    CAPTURE_MANIFEST_DIFF_BOUNDARY,
    capture_manifest_diff_report_to_jsonable,
    diff_capture_manifest_to_bundle,
)
from self_harness.reproduction_readiness import ReproductionReadinessError, load_reproduction_requirements  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = load_reproduction_requirements(args.requirements)
        report = diff_capture_manifest_to_bundle(
            args.manifest,
            args.bundle,
            requirements,
            manifest_signature_path=args.manifest_signature,
            bundle_signature_path=args.bundle_signature,
            require_manifest_signature=args.require_manifest_signature,
            require_bundle_signature=args.require_bundle_signature,
        )
        payload = capture_manifest_diff_report_to_jsonable(report)
    except (OSError, ReproductionReadinessError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "error": str(exc),
            "reproduction_claimed": False,
            "boundary": CAPTURE_MANIFEST_DIFF_BOUNDARY,
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
    parser = argparse.ArgumentParser(description="Diff a capture manifest against a reproduction evidence bundle.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    parser.add_argument("--manifest-signature", type=Path)
    parser.add_argument("--bundle-signature", type=Path)
    parser.add_argument("--require-manifest-signature", action="store_true")
    parser.add_argument("--require-bundle-signature", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("dist/self-harness-capture-manifest-diff.json"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
