#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.reproduction_bundle import REPRODUCTION_BUNDLE_BOUNDARY  # noqa: E402
from self_harness.reproduction_bundle_build import (  # noqa: E402
    ReproductionBundleBuildError,
    build_reproduction_bundle,
    reproduction_bundle_document_to_jsonable,
    write_reproduction_bundle,
)
from self_harness.reproduction_readiness import ReproductionReadinessError, load_reproduction_requirements  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = load_reproduction_requirements(args.requirements)
        required_classes = tuple(sorted({requirement.required_artifact_class for requirement in requirements}))
        artifacts = _artifact_index(args, required_classes=required_classes)
        document = build_reproduction_bundle(
            artifacts,
            bundle_path=args.out,
            requirements=requirements,
            bundle_id=args.bundle_id,
            operator_label=args.operator_label,
            created_at=args.created_at,
            source_defaults={
                "provider": args.source_provider,
                "captured_at": args.source_captured_at,
                "operator_label": args.operator_label,
                **({"url": args.source_url} if args.source_url is not None else {}),
            },
            entry_sources=_entry_sources(args.entry_source),
            entry_notes=_entry_notes(args.entry_note),
            strict_shapes=args.strict_shapes,
        )
        write_reproduction_bundle(document, args.out)
    except ReproductionReadinessError as exc:
        _emit_error(args.out, exc, code="requirements-error")
        return 3
    except (OSError, ReproductionBundleBuildError) as exc:
        _emit_error(args.out, exc, code="bundle-build-error")
        return 2

    payload = reproduction_bundle_document_to_jsonable(document)
    print(stable_json_dumps(payload))
    return 0


def _artifact_index(args: argparse.Namespace, *, required_classes: tuple[str, ...]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for artifact_dir in args.artifact_dir:
        for artifact_class in required_classes:
            candidate = artifact_dir / f"{artifact_class}.json"
            if candidate.is_file():
                _add_artifact(artifacts, artifact_class, candidate)
    for spec in args.artifact:
        artifact_class, path = _parse_key_value(spec, flag="--artifact")
        _add_artifact(artifacts, artifact_class, path)
    return artifacts


def _add_artifact(artifacts: dict[str, Path], artifact_class: str, path: Path) -> None:
    if artifact_class in artifacts:
        raise ReproductionBundleBuildError(f"duplicate artifact class: {artifact_class}")
    artifacts[artifact_class] = path


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
            raise ReproductionBundleBuildError(f"duplicate entry note class: {artifact_class}")
        notes[artifact_class] = note
    return notes


def _parse_class_spec(spec: str, *, flag: str) -> tuple[str, str]:
    artifact_class, separator, rest = spec.partition(":")
    if not separator or not artifact_class or not rest:
        raise ReproductionBundleBuildError(f"{flag} values must use CLASS:KEY=VALUE")
    return artifact_class, rest


def _parse_key_value(spec: str, *, flag: str) -> tuple[str, Path] | tuple[str, str]:
    key, separator, value = spec.partition("=")
    if not separator or not key or not value:
        raise ReproductionBundleBuildError(f"{flag} values must use KEY=VALUE")
    return key, Path(value) if flag == "--artifact" else value


def _emit_error(out: Path, exc: BaseException, *, code: str) -> None:
    payload = {
        "schema_version": "1.0",
        "ok": False,
        "error": str(exc),
        "code": code,
        "reproduction_claimed": False,
        "boundary": REPRODUCTION_BUNDLE_BOUNDARY,
    }
    text = stable_json_dumps(payload) + "\n"
    print(text, end="", file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic reproduction evidence bundle manifest.")
    parser.add_argument("--artifact-dir", type=Path, action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[], help="Explicit artifact mapping, CLASS=PATH.")
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--operator-label", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--source-provider", required=True)
    parser.add_argument("--source-captured-at", required=True)
    parser.add_argument("--source-url")
    parser.add_argument(
        "--entry-source",
        action="append",
        default=[],
        help="Per-entry source override, CLASS:KEY=VALUE.",
    )
    parser.add_argument("--entry-note", action="append", default=[], help="Per-entry note, CLASS=TEXT.")
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    parser.add_argument("--out", type=Path, default=Path("dist/reproduction-artifacts/bundle.json"))
    parser.add_argument("--strict-shapes", dest="strict_shapes", action="store_true", default=True)
    parser.add_argument("--no-strict-shapes", dest="strict_shapes", action="store_false")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
