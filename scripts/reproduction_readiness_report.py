#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.reproduction_bundle import (  # noqa: E402
    load_reproduction_bundle,
    reproduction_bundle_artifact_index,
    verify_reproduction_bundle,
)
from self_harness.reproduction_readiness import (  # noqa: E402
    REPRODUCTION_READINESS_BOUNDARY,
    ReproductionReadinessError,
    ReproductionRequirement,
    evaluate_reproduction_readiness,
    load_readiness_matrix_report,
    load_reproduction_requirements,
    reproduction_readiness_report_to_jsonable,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_arg_combinations(args)
        requirements = load_reproduction_requirements(args.requirements)
        readiness_matrix = load_readiness_matrix_report(args.readiness_matrix_result)
        artifact_index, metadata = _artifact_index(args, requirements)
        report = evaluate_reproduction_readiness(requirements, readiness_matrix, artifact_index, metadata=metadata)
        payload = reproduction_readiness_report_to_jsonable(report)
    except (OSError, ReproductionReadinessError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reproduction_ready": False,
            "error": str(exc),
            "reproduction_claimed": False,
            "boundary": REPRODUCTION_READINESS_BOUNDARY,
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
    return 0 if report.reproduction_ready else 2


def _validate_arg_combinations(args: argparse.Namespace) -> None:
    if args.reproduction_bundle is not None and (args.artifact_dir or args.artifact):
        raise ReproductionReadinessError(
            "--reproduction-bundle cannot be combined with --artifact-dir or --artifact"
        )


def _artifact_index(
    args: argparse.Namespace,
    requirements: Sequence[ReproductionRequirement],
) -> tuple[dict[str, list[Path]], dict[str, object] | None]:
    if args.reproduction_bundle is not None:
        if args.artifact_dir or args.artifact:
            raise ReproductionReadinessError(
                "--reproduction-bundle cannot be combined with --artifact-dir or --artifact"
            )
        report = verify_reproduction_bundle(
            args.reproduction_bundle,
            requirements,
            signature_path=args.reproduction_bundle_signature,
            public_key=args.reproduction_bundle_public_key,
            require_signature=args.require_reproduction_bundle_signature,
        )
        metadata: dict[str, object] = {
            "reproduction_bundle": {
                "ok": report.ok,
                "bundle_id": report.bundle_id,
                "report_hash": report.report_hash,
            }
        }
        if not report.ok:
            return {}, metadata
        bundle = load_reproduction_bundle(args.reproduction_bundle)
        return reproduction_bundle_artifact_index(bundle), metadata

    index: dict[str, list[Path]] = {}
    _add_artifact(index, "audit_verify_report", args.audit_verify_result)
    for artifact_dir in args.artifact_dir:
        if not artifact_dir.exists():
            continue
        for path in sorted(artifact_dir.iterdir()):
            if path.is_file():
                _add_artifact(index, path.stem, path)
    for spec in args.artifact:
        artifact_class, path = _parse_artifact_spec(spec)
        _add_artifact(index, artifact_class, path)
    return index, None


def _add_artifact(index: dict[str, list[Path]], artifact_class: str, path: Path | None) -> None:
    if path is None:
        return
    index.setdefault(artifact_class, []).append(path)


def _parse_artifact_spec(spec: str) -> tuple[str, Path]:
    artifact_class, separator, raw_path = spec.partition("=")
    if not separator or not artifact_class or not raw_path:
        raise ReproductionReadinessError("--artifact values must use CLASS=PATH")
    return artifact_class, Path(raw_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Map paper reproduction requirements to current readiness and artifact evidence."
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    parser.add_argument(
        "--readiness-matrix-result",
        type=Path,
        default=Path("dist/self-harness-readiness-matrix.json"),
    )
    parser.add_argument("--audit-verify-result", type=Path, default=Path("dist/self-harness-audit-verify.json"))
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        action="append",
        default=[],
        help="Directory whose files are indexed by stem as reproduction artifact classes.",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Explicit reproduction artifact mapping in CLASS=PATH form.",
    )
    parser.add_argument(
        "--reproduction-bundle",
        type=Path,
        help="Operator reproduction evidence bundle manifest; cannot be combined with artifact inputs.",
    )
    parser.add_argument("--reproduction-bundle-signature", type=Path)
    parser.add_argument("--reproduction-bundle-public-key")
    parser.add_argument("--require-reproduction-bundle-signature", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("dist/self-harness-reproduction-readiness.json"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
