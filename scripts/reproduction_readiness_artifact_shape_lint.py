#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness._artifact_shapes import artifact_shape_error  # noqa: E402
from self_harness.reproduction_bundle import (  # noqa: E402
    load_reproduction_bundle,
    reproduction_bundle_artifact_index,
    verify_reproduction_bundle,
)
from self_harness.reproduction_readiness import (  # noqa: E402
    REPRODUCTION_READINESS_BOUNDARY,
    ReproductionReadinessError,
    load_reproduction_requirements,
)
from self_harness.types import stable_json_dumps  # noqa: E402


@dataclass(frozen=True)
class ShapeCheck:
    artifact_class: str
    status: str
    detail: str
    artifact_paths: tuple[str, ...]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = load_reproduction_requirements(args.requirements)
        required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
        bundle_report = None
        if args.reproduction_bundle is not None:
            if args.artifact_dir or args.artifact:
                raise ReproductionReadinessError(
                    "--reproduction-bundle cannot be combined with --artifact-dir or --artifact"
                )
            bundle_report = verify_reproduction_bundle(
                args.reproduction_bundle,
                requirements,
                signature_path=args.reproduction_bundle_signature,
                public_key=args.reproduction_bundle_public_key,
                require_signature=args.require_reproduction_bundle_signature,
            )
            if bundle_report.ok:
                artifact_index = reproduction_bundle_artifact_index(load_reproduction_bundle(args.reproduction_bundle))
                checks = tuple(
                    _shape_check(artifact_class, artifact_index.get(artifact_class, ()))
                    for artifact_class in required_classes
                )
            else:
                checks = (
                    ShapeCheck(
                        artifact_class="reproduction_bundle",
                        status="fail",
                        detail="bundle verification failed",
                        artifact_paths=(str(args.reproduction_bundle),),
                    ),
                )
        else:
            artifact_index = _artifact_index(args)
            checks = tuple(
                _shape_check(artifact_class, artifact_index.get(artifact_class, ()))
                for artifact_class in required_classes
            )
        ready = all(check.status == "pass" for check in checks)
        metadata = (
            {
                "reproduction_bundle": {
                    "ok": bundle_report.ok,
                    "bundle_id": bundle_report.bundle_id,
                    "report_hash": bundle_report.report_hash,
                }
            }
            if bundle_report is not None
            else None
        )
        payload = _report(checks=checks, ready=ready, metadata=metadata)
    except (OSError, ReproductionReadinessError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "artifact_shapes_ready": False,
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
    return 0 if ready else 2


def _artifact_index(args: argparse.Namespace) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for artifact_dir in args.artifact_dir:
        if not artifact_dir.exists():
            continue
        for path in sorted(artifact_dir.iterdir()):
            if path.is_file():
                _add_artifact(index, path.stem, path)
    for spec in args.artifact:
        artifact_class, path = _parse_artifact_spec(spec)
        _add_artifact(index, artifact_class, path)
    return index


def _add_artifact(index: dict[str, list[Path]], artifact_class: str, path: Path) -> None:
    index.setdefault(artifact_class, []).append(path)


def _parse_artifact_spec(spec: str) -> tuple[str, Path]:
    artifact_class, separator, raw_path = spec.partition("=")
    if not separator or not artifact_class or not raw_path:
        raise ReproductionReadinessError("--artifact values must use CLASS=PATH")
    return artifact_class, Path(raw_path)


def _shape_check(artifact_class: str, raw_paths: tuple[Path, ...] | list[Path]) -> ShapeCheck:
    paths = tuple(path for path in raw_paths if path.is_file() and path.stat().st_size > 0)
    if not paths:
        return ShapeCheck(
            artifact_class=artifact_class,
            status="fail",
            detail=f"missing non-empty artifact for class {artifact_class}",
            artifact_paths=(),
        )
    invalid = tuple(
        f"{path}: {error}"
        for path in paths
        if (error := artifact_shape_error(artifact_class, path)) is not None
    )
    if invalid:
        return ShapeCheck(
            artifact_class=artifact_class,
            status="fail",
            detail="invalid artifact evidence: " + ", ".join(invalid),
            artifact_paths=tuple(str(path) for path in paths),
        )
    return ShapeCheck(
        artifact_class=artifact_class,
        status="pass",
        detail="artifact class shape is valid for benchmark reproduction readiness",
        artifact_paths=tuple(str(path) for path in paths),
    )


def _report(
    *,
    checks: tuple[ShapeCheck, ...],
    ready: bool,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    report_without_hash = {
        "schema_version": "1.0",
        "ok": True,
        "artifact_shapes_ready": ready,
        "checks": [_check_to_jsonable(check) for check in checks],
        "reproduction_claimed": False,
        "boundary": REPRODUCTION_READINESS_BOUNDARY,
    }
    if metadata is not None:
        report_without_hash["metadata"] = metadata
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return {**report_without_hash, "report_hash": report_hash}


def _check_to_jsonable(check: ShapeCheck) -> dict[str, object]:
    return {
        "artifact_class": check.artifact_class,
        "status": check.status,
        "detail": check.detail,
        "artifact_paths": list(check.artifact_paths),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate supplied benchmark-reproduction artifact shapes without live contact."
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
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
    parser.add_argument("--out", type=Path, default=Path("dist/self-harness-reproduction-artifact-shapes.json"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
