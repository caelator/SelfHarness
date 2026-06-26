#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.capture_admit import (  # noqa: E402
    CAPTURE_ADMISSION_BOUNDARY,
    CaptureAdmissionError,
    capture_admission_report_to_jsonable,
    run_capture_admission,
)
from self_harness.reproduction_readiness import ReproductionReadinessError, load_reproduction_requirements  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = load_reproduction_requirements(args.requirements)
        result = run_capture_admission(
            admission_id=args.admission_id,
            requirements=requirements,
            artifact_dir=args.artifact_dir,
            bundle_path=args.bundle_out if args.bundle_out is not None else args.artifact_dir / "bundle.json",
            bundle_id=args.bundle_id,
            operator_label=args.operator_label,
            created_at=args.created_at,
            source_provider=args.source_provider,
            source_captured_at=args.source_captured_at,
            source_url=args.source_url,
            raw_inputs=_raw_inputs(args.raw_input),
            raw_flags=_raw_flags(args.raw_flag),
            supplied_artifacts=_artifacts(args.artifact),
            readiness_matrix_result=args.readiness_matrix_result,
            bundle_signature_path=args.bundle_signature,
            bundle_public_key=args.bundle_public_key,
            require_bundle_signature=args.require_bundle_signature,
            skip_readiness=args.skip_readiness,
        )
        payload = capture_admission_report_to_jsonable(result)
    except (OSError, CaptureAdmissionError, ReproductionReadinessError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "capture-admission-error",
            "message": str(exc),
            "reproduction_claimed": False,
            "boundary": CAPTURE_ADMISSION_BOUNDARY,
        }
        _write(args.out, payload)
        print(stable_json_dumps(payload), file=sys.stderr)
        return 2

    _write(args.out, payload)
    print(stable_json_dumps(payload))
    return 0 if result.ok else 2


def _raw_inputs(specs: list[str]) -> dict[str, dict[str, Path]]:
    result: dict[str, dict[str, Path]] = {}
    for spec in specs:
        artifact_class, raw_assignment = _parse_class_spec(spec, flag="--raw-input")
        key, raw_path = _parse_key_value(raw_assignment, flag="--raw-input")
        if key in result.setdefault(artifact_class, {}):
            raise CaptureAdmissionError(f"duplicate raw input for {artifact_class}:{key}")
        result[artifact_class][key] = Path(raw_path)
    return result


def _raw_flags(specs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in specs:
        key, value = _parse_key_value(spec, flag="--raw-flag")
        if key in result:
            raise CaptureAdmissionError(f"duplicate raw flag: {key}")
        result[key] = value
    return result


def _artifacts(specs: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for spec in specs:
        artifact_class, raw_path = _parse_key_value(spec, flag="--artifact")
        if artifact_class in result:
            raise CaptureAdmissionError(f"duplicate supplied artifact: {artifact_class}")
        result[artifact_class] = Path(raw_path)
    return result


def _parse_class_spec(spec: str, *, flag: str) -> tuple[str, str]:
    artifact_class, separator, rest = spec.partition(":")
    if not separator or not artifact_class or not rest:
        raise CaptureAdmissionError(f"{flag} values must use CLASS:KEY=VALUE")
    return artifact_class, rest


def _parse_key_value(spec: str, *, flag: str) -> tuple[str, str]:
    key, separator, value = spec.partition("=")
    if not separator or not key or not value:
        raise CaptureAdmissionError(f"{flag} values must use KEY=VALUE")
    return key, value


def _write(path: Path | None, payload: dict[str, object]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admit post-capture raw evidence into a verified bundle report.")
    parser.add_argument("--admission-id", required=True)
    parser.add_argument("--operator-label", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--source-provider", required=True)
    parser.add_argument("--source-captured-at", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--bundle-out", type=Path)
    parser.add_argument(
        "--raw-input",
        action="append",
        default=[],
        help="Raw input mapping in CLASS:KEY=PATH form.",
    )
    parser.add_argument("--raw-flag", action="append", default=[], help="Raw extractor flag in KEY=VALUE form.")
    parser.add_argument("--artifact", action="append", default=[], help="Pre-extracted artifact in CLASS=PATH form.")
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    parser.add_argument("--readiness-matrix-result", type=Path)
    parser.add_argument("--skip-readiness", action="store_true")
    parser.add_argument("--bundle-signature", type=Path)
    parser.add_argument("--bundle-public-key")
    parser.add_argument("--require-bundle-signature", action="store_true")
    parser.add_argument("--out", type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
