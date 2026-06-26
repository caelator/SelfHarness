#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.capture_extract import (  # noqa: E402
    CAPTURE_EXTRACT_BOUNDARY,
    EXTRACTABLE_ARTIFACT_CLASSES,
    CaptureExtractError,
    extract_artifact_from_paths,
    parse_proposer_backend_map,
)
from self_harness.image_policy import ImagePolicyError  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = extract_artifact_from_paths(
            args.artifact_class,
            capture_run_id=args.capture_run_id,
            harbor_discovery_result=args.harbor_discovery_result,
            harbor_version=args.harbor_version,
            image_policy=args.image_policy,
            model_backend_preflight_result=args.model_backend_preflight_result,
            network_controls=args.network_controls,
            harbor_run_dir=args.harbor_run_dir,
            capture_envelope=args.capture_envelope,
            attempts_jsonl=args.attempts_jsonl,
            split_manifest_result=args.split_manifest_result,
            fixed_protocol_declaration=args.fixed_protocol_declaration,
            fixed_protocol_result=args.fixed_protocol_result,
            fixed_protocol_sha256=args.fixed_protocol_sha256,
            proposer_request_log=args.proposer_request_log,
            proposer_request_log_artifact=args.proposer_request_log_artifact,
            proposer_context_log=args.proposer_context_log,
            audit_run_dir=args.audit_run_dir,
            proposer_backend_map=parse_proposer_backend_map(args.proposer_backend_map)
            if args.proposer_backend_map
            else {},
        )
    except (OSError, CaptureExtractError, ImagePolicyError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "artifact_class": args.artifact_class,
            "reason": "capture-extract-error",
            "message": str(exc),
            "reproduction_claimed": False,
            "boundary": CAPTURE_EXTRACT_BOUNDARY,
        }
        output = stable_json_dumps(payload) + "\n"
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        print(output, end="", file=sys.stderr)
        return 2

    output = stable_json_dumps(payload) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    if args.json:
        print(output, end="")
    else:
        print(args.out if args.out is not None else output, end="" if args.out is None else "\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract required live evidence artifacts from captured raw files.")
    parser.add_argument("--class", dest="artifact_class", choices=sorted(EXTRACTABLE_ARTIFACT_CLASSES), required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--capture-run-id")
    parser.add_argument("--harbor-discovery-result", type=Path)
    parser.add_argument("--harbor-version")
    parser.add_argument("--image-policy", type=Path)
    parser.add_argument("--model-backend-preflight-result", type=Path)
    parser.add_argument("--network-controls", type=Path)
    parser.add_argument("--harbor-run-dir", type=Path)
    parser.add_argument("--capture-envelope", type=Path)
    parser.add_argument("--attempts-jsonl", type=Path)
    parser.add_argument("--split-manifest-result", type=Path)
    parser.add_argument("--fixed-protocol-declaration", type=Path)
    parser.add_argument("--fixed-protocol-result", type=Path)
    parser.add_argument("--fixed-protocol-sha256")
    parser.add_argument("--proposer-request-log", type=Path)
    parser.add_argument("--proposer-request-log-artifact", type=Path)
    parser.add_argument("--proposer-context-log", type=Path)
    parser.add_argument("--audit-run-dir", type=Path)
    parser.add_argument("--proposer-backend-map", action="append", default=[])
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
