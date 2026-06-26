#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.types import stable_json_dumps  # noqa: E402

PYPI_ATTESTATION_TYPE = "https://docs.pypi.org/attestations/publish/v1"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    bundle = _json_object(args.bundle)
    material_digest = sha256(args.material.read_bytes()).hexdigest()
    payload = {
        "_type": PYPI_ATTESTATION_TYPE,
        "materials": [
            {
                "uri": args.material.name,
                "digest": {"sha256": material_digest},
            }
        ],
        "claim": {
            "predicateType": "https://docs.pypi.org/attestations/publish/v1",
            "subject": args.material.name,
        },
        "bundle": bundle,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    print(str(args.out))
    return 0


def _json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return cast(dict[str, Any], data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a synthetic local PyPI attestation fixture.")
    parser.add_argument("--bundle", type=Path, required=True, help="Sigstore bundle template JSON path.")
    parser.add_argument("--material", type=Path, required=True, help="Material file to bind by sha256.")
    parser.add_argument("--out", type=Path, required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
