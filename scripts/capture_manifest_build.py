#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.capture_manifest import CAPTURE_MANIFEST_BOUNDARY  # noqa: E402
from self_harness.capture_manifest_build import (  # noqa: E402
    CaptureManifestBuildError,
    build_capture_manifest,
    capture_manifest_document_to_jsonable,
    load_planned_artifact,
    write_capture_manifest_document,
)
from self_harness.reproduction_readiness import ReproductionReadinessError, load_reproduction_requirements  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = load_reproduction_requirements(args.requirements)
        planned_artifacts = _planned_artifacts(args.planned_artifact)
        document = build_capture_manifest(
            requirements=requirements,
            manifest_id=args.manifest_id,
            bundle_id=args.bundle_id,
            operator_label=args.operator_label,
            created_at=args.created_at,
            run_id=args.run_id,
            mode=args.mode,
            benchmark_protocol=args.benchmark_protocol,
            model_backends=args.model_backend,
            evaluator=args.evaluator,
            tool_set=args.tool_set,
            tool_budget=_tool_budget_json(args.tool_budget_json),
            outbound_bandwidth_cap_bps=args.outbound_bandwidth_cap_bps,
            mirrored_resources=args.mirrored_resource,
            signing_custody={
                "provider": args.signing_provider,
                **({"key_id": args.key_id} if args.key_id is not None else {}),
                **({"fingerprint": args.fingerprint} if args.fingerprint is not None else {}),
            },
            source_defaults={
                "provider": args.source_provider,
                "captured_after": args.source_captured_after,
                "captured_before": args.source_captured_before,
                "operator_label": args.operator_label,
            },
            entry_sources=_entry_sources(args.entry_source),
            planned_artifacts=planned_artifacts,
            entry_notes=_entry_notes(args.entry_note),
            strict_shapes=args.strict_shapes,
        )
        write_capture_manifest_document(document, args.out)
    except ReproductionReadinessError as exc:
        _emit_error(exc, code="requirements-error")
        return 3
    except (OSError, CaptureManifestBuildError) as exc:
        _emit_error(exc, code="capture-manifest-build-error")
        return 2

    print(stable_json_dumps(capture_manifest_document_to_jsonable(document)))
    return 0


def _planned_artifacts(specs: list[str]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for spec in specs:
        artifact_class, raw_path = _parse_key_value(spec, flag="--planned-artifact")
        if artifact_class in artifacts:
            raise CaptureManifestBuildError(f"duplicate planned artifact class: {artifact_class}")
        artifacts[artifact_class] = load_planned_artifact(Path(raw_path), artifact_class=artifact_class)
    return artifacts


def _entry_sources(specs: list[str]) -> dict[str, dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    for spec in specs:
        artifact_class, raw_assignment = _parse_class_spec(spec, flag="--entry-source")
        key, value = _parse_key_value(raw_assignment, flag="--entry-source")
        sources.setdefault(artifact_class, {})[key] = value
    return sources


def _entry_notes(specs: list[str]) -> dict[str, str]:
    notes: dict[str, str] = {}
    for spec in specs:
        artifact_class, note = _parse_key_value(spec, flag="--entry-note")
        if artifact_class in notes:
            raise CaptureManifestBuildError(f"duplicate entry note class: {artifact_class}")
        notes[artifact_class] = note
    return notes


def _tool_budget_json(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CaptureManifestBuildError("--tool-budget-json must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise CaptureManifestBuildError("--tool-budget-json must be a JSON object")
    return cast(dict[str, object], payload)


def _parse_class_spec(spec: str, *, flag: str) -> tuple[str, str]:
    artifact_class, separator, rest = spec.partition(":")
    if not separator or not artifact_class or not rest:
        raise CaptureManifestBuildError(f"{flag} values must use CLASS:KEY=VALUE")
    return artifact_class, rest


def _parse_key_value(spec: str, *, flag: str) -> tuple[str, str]:
    key, separator, value = spec.partition("=")
    if not separator or not key or not value:
        raise CaptureManifestBuildError(f"{flag} values must use KEY=VALUE")
    return key, value


def _emit_error(exc: BaseException, *, code: str) -> None:
    payload = {
        "schema_version": "1.0",
        "ok": False,
        "error": str(exc),
        "code": code,
        "reproduction_claimed": False,
        "boundary": CAPTURE_MANIFEST_BOUNDARY,
    }
    print(stable_json_dumps(payload), file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic operator live-evidence capture manifest.")
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--operator-label", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", default="live")
    parser.add_argument("--benchmark-protocol", default="terminal-bench@2.0")
    parser.add_argument("--model-backend", action="append", required=True)
    parser.add_argument("--evaluator", required=True)
    parser.add_argument("--tool-set", required=True)
    parser.add_argument("--tool-budget-json", required=True)
    parser.add_argument("--outbound-bandwidth-cap-bps", type=int, required=True)
    parser.add_argument("--mirrored-resource", action="append", required=True)
    parser.add_argument("--source-provider", required=True)
    parser.add_argument("--source-captured-after", required=True)
    parser.add_argument("--source-captured-before", required=True)
    parser.add_argument("--signing-provider", required=True)
    parser.add_argument("--key-id", default="")
    parser.add_argument("--fingerprint")
    parser.add_argument(
        "--planned-artifact",
        action="append",
        default=[],
        help="Optional planned artifact template, CLASS=PATH. Missing classes get deterministic stubs.",
    )
    parser.add_argument(
        "--entry-source",
        action="append",
        default=[],
        help="Per-entry planned-source override, CLASS:KEY=VALUE.",
    )
    parser.add_argument("--entry-note", action="append", default=[], help="Per-entry note, CLASS=TEXT.")
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    parser.add_argument("--out", type=Path, default=Path("dist/self-harness-capture-manifest.json"))
    parser.add_argument("--strict-shapes", dest="strict_shapes", action="store_true", default=True)
    parser.add_argument("--no-strict-shapes", dest="strict_shapes", action="store_false")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
