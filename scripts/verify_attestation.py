#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.attestations import attestation_report_to_jsonable, verify_attestation  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify_attestation(
        args.bundle,
        material_path=args.material,
        trust_root_path=args.trust_root,
        backend=args.backend,
    )
    payload = attestation_report_to_jsonable(report)
    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.ok else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify local release attestation material.")
    parser.add_argument("--bundle", type=Path, required=True, help="PyPI attestation envelope JSON path.")
    parser.add_argument("--material", type=Path, required=True, help="Distribution artifact bound by the attestation.")
    parser.add_argument(
        "--trust-root",
        type=Path,
        required=True,
        help="Operator-owned attestation trust-root JSON path.",
    )
    parser.add_argument("--backend", choices=["structural", "sigstore"], default="structural")
    parser.add_argument("--out", type=Path, help="Optional path to write the structured verification report.")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
