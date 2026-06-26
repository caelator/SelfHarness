from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
ARTIFACT_KINDS = {"wheel", "sdist", "sbom"}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest.resolve()
    dist_dir = (repo_root / args.dist_dir).resolve()
    sbom_dir = (repo_root / args.sbom_dir).resolve()
    verify_manifest(manifest_path, dist_dir=dist_dir, sbom_dir=sbom_dir)
    print(f"provenance verified: {manifest_path}")
    return 0


def verify_manifest(manifest_path: Path, *, dist_dir: Path, sbom_dir: Path) -> None:
    manifest = _load_manifest(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"unsupported provenance schema_version: {manifest.get('schema_version')}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SystemExit("provenance manifest must include artifacts")
    for artifact in artifacts:
        _verify_artifact(artifact, dist_dir=dist_dir, sbom_dir=sbom_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a Self-Harness release provenance manifest.")
    parser.add_argument("--manifest", type=Path, required=True, help="Provenance manifest path.")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repository root.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"), help="Distribution artifact directory.")
    parser.add_argument("--sbom-dir", type=Path, default=Path("sbom"), help="Optional SBOM artifact directory.")
    return parser


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing provenance manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid provenance manifest JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit("provenance manifest must be a JSON object")
    return value


def _verify_artifact(artifact: object, *, dist_dir: Path, sbom_dir: Path) -> None:
    if not isinstance(artifact, dict):
        raise SystemExit("provenance artifact entries must be objects")
    kind = artifact.get("kind")
    filename = artifact.get("filename")
    expected_hash = artifact.get("sha256")
    expected_size = artifact.get("size_bytes")
    if kind not in ARTIFACT_KINDS:
        raise SystemExit(f"unsupported provenance artifact kind: {kind}")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise SystemExit("provenance artifact filename must be a basename")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise SystemExit(f"provenance artifact has invalid sha256: {filename}")
    if not all(character in "0123456789abcdef" for character in expected_hash):
        raise SystemExit(f"provenance artifact has invalid sha256: {filename}")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise SystemExit(f"provenance artifact has invalid size_bytes: {filename}")
    path = (sbom_dir if kind == "sbom" else dist_dir) / filename
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise SystemExit(f"provenance artifact is missing: {path}") from exc
    actual_hash = sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit(f"provenance hash mismatch for {filename}: expected {expected_hash}, got {actual_hash}")
    if len(data) != expected_size:
        raise SystemExit(f"provenance size mismatch for {filename}: expected {expected_size}, got {len(data)}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
