#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.readiness_matrix import ReadinessMatrixError, load_readiness_matrix_catalog  # noqa: E402
from self_harness.readiness_promotion import (  # noqa: E402
    READINESS_PROMOTION_BOUNDARY,
    ReadinessPromotionError,
    evaluate_readiness_promotion,
    readiness_promotion_report_to_jsonable,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        baseline = load_readiness_matrix_catalog(args.baseline_catalog)
        candidate = load_readiness_matrix_catalog(args.candidate_catalog)
        report = evaluate_readiness_promotion(
            baseline,
            candidate,
            surface_results={
                "operator_preflight": _load_optional_json(args.operator_preflight_result),
                "scanner_check": _load_optional_json(args.scanner_result),
                "harbor_discovery_check": _load_optional_json(args.harbor_discovery_result),
                "release_smoke": _load_optional_json(args.release_smoke_result),
                "model_backend_preflight": _load_optional_json(args.model_backend_preflight_result),
                "container_preflight": _load_optional_json(args.container_preflight_result),
                "attestation_check": _load_optional_json(args.attestation_result),
            },
            allow_demotions=args.allow_demotion,
        )
    except (OSError, ReadinessMatrixError, ReadinessPromotionError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "error": str(exc),
            "reproduction_claimed": False,
            "boundary": READINESS_PROMOTION_BOUNDARY,
        }
        output = stable_json_dumps(payload) + "\n"
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        print(output, end="", file=sys.stderr)
        return 3

    payload = readiness_promotion_report_to_jsonable(report)
    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    if args.expected_hash is not None:
        expected = args.expected_hash.read_text(encoding="utf-8").strip()
        if report.report_hash != expected:
            print(output, end="")
            print(
                f"readiness promotion report hash mismatch: expected {expected}, got {report.report_hash}",
                file=sys.stderr,
            )
            return 2
    print(output, end="")
    return 0 if report.ok else 2


def _load_optional_json(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReadinessPromotionError(f"missing preflight surface artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReadinessPromotionError(f"invalid preflight surface JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ReadinessPromotionError(f"preflight surface artifact must be a JSON object: {path}")
    if not all(isinstance(key, str) for key in data):
        raise ReadinessPromotionError(f"preflight surface artifact keys must be strings: {path}")
    return cast(dict[str, object], data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Admit readiness-catalog promotions against existing preflight surface artifacts."
    )
    parser.add_argument("--baseline-catalog", type=Path, default=Path("docs/operations/readiness_matrix.json"))
    parser.add_argument("--candidate-catalog", type=Path, required=True)
    parser.add_argument("--operator-preflight-result", type=Path)
    parser.add_argument("--scanner-result", type=Path)
    parser.add_argument("--harbor-discovery-result", type=Path)
    parser.add_argument("--release-smoke-result", type=Path)
    parser.add_argument("--model-backend-preflight-result", type=Path)
    parser.add_argument("--container-preflight-result", type=Path)
    parser.add_argument("--attestation-result", type=Path)
    parser.add_argument("--allow-demotion", action="store_true")
    parser.add_argument("--expected-hash", type=Path)
    parser.add_argument("--out", type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
