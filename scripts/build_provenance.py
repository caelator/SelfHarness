from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    dist_dir = (repo_root / args.dist_dir).resolve()
    sbom_dir = (repo_root / args.sbom_dir).resolve()
    pyproject = _load_pyproject(repo_root / "pyproject.toml")
    package = pyproject["project"]
    output_path = args.out
    if output_path is None:
        output_path = dist_dir / f"{package['name']}-{package['version']}-provenance.json"
    else:
        output_path = output_path.resolve()
    manifest = build_manifest(repo_root, dist_dir, sbom_dir, pyproject)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_stable_json(manifest), encoding="utf-8")
    print(output_path)
    return 0


def build_manifest(repo_root: Path, dist_dir: Path, sbom_dir: Path, pyproject: dict[str, Any]) -> dict[str, Any]:
    project = pyproject["project"]
    build_system = pyproject.get("build-system", {})
    artifacts = [
        *(_artifact_row("wheel", path) for path in sorted(dist_dir.glob("*.whl"))),
        *(_artifact_row("sdist", path) for path in sorted(dist_dir.glob("*.tar.gz"))),
    ]
    sbom_path = sbom_dir / "self_harness-sbom.json"
    if sbom_path.exists():
        artifacts.append(_artifact_row("sbom", sbom_path))
    artifacts = sorted(artifacts, key=lambda item: (str(item["kind"]), str(item["filename"])))
    if not artifacts:
        raise SystemExit(f"no release artifacts found in {dist_dir}")
    return {
        "schema_version": SCHEMA_VERSION,
        "package_name": project["name"],
        "package_version": project["version"],
        "python_requires": project.get("requires-python"),
        "builder": {
            "backend": build_system.get("build-backend", "unknown"),
            "build_module": "build",
            "build_module_version": _metadata_version("build"),
        },
        "source": _source_info(repo_root),
        "artifacts": artifacts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic Self-Harness release provenance manifest.")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repository root.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"), help="Distribution artifact directory.")
    parser.add_argument("--sbom-dir", type=Path, default=Path("sbom"), help="Optional SBOM artifact directory.")
    parser.add_argument("--out", type=Path, help="Output manifest path.")
    return parser


def _load_pyproject(path: Path) -> dict[str, Any]:
    try:
        pyproject = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"could not read pyproject.toml: {path}") from exc
    project = pyproject.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str) or not isinstance(
        project.get("version"),
        str,
    ):
        raise SystemExit("pyproject.toml must include project.name and project.version")
    return pyproject


def _metadata_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def _source_info(repo_root: Path) -> dict[str, object]:
    commit = _git_output(repo_root, ["rev-parse", "HEAD"])
    status = _git_output(repo_root, ["status", "--porcelain"])
    return {
        "git_commit": commit if commit is not None else "git-unavailable",
        "git_dirty": None if status is None else bool(status),
        "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
    }


def _git_output(repo_root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _artifact_row(kind: str, path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "kind": kind,
        "filename": path.name,
        "sha256": sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
