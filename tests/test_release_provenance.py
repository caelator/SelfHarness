import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_PROVENANCE = REPO_ROOT / "scripts" / "build_provenance.py"
VERIFY_PROVENANCE = REPO_ROOT / "scripts" / "verify_provenance.py"


def test_release_provenance_is_deterministic_and_verifies(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    _write_artifacts(repo, include_sbom=True)

    first = _run_build(repo)
    first_bytes = first.read_bytes()
    second = _run_build(repo)

    manifest = json.loads(second.read_text(encoding="utf-8"))
    artifacts = {(row["kind"], row["filename"]) for row in manifest["artifacts"]}

    assert second.read_bytes() == first_bytes
    assert manifest["schema_version"] == "1.0"
    assert manifest["package_name"] == "self-harness"
    assert manifest["package_version"] == "0.1.0"
    assert manifest["source"]["git_commit"] == "git-unavailable"
    assert artifacts == {
        ("wheel", "self_harness-0.1.0-py3-none-any.whl"),
        ("sdist", "self_harness-0.1.0.tar.gz"),
        ("sbom", "self_harness-sbom.json"),
    }
    _run_verify(repo, second)


def test_release_provenance_omits_missing_sbom(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    _write_artifacts(repo, include_sbom=False)

    manifest_path = _run_build(repo)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert [row["kind"] for row in manifest["artifacts"]] == ["sdist", "wheel"]
    _run_verify(repo, manifest_path)


def test_release_provenance_verification_rejects_tampering(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    artifacts = _write_artifacts(repo, include_sbom=True)
    manifest_path = _run_build(repo)

    artifacts["wheel"].write_bytes(b"tampered wheel")
    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_PROVENANCE),
            "--manifest",
            str(manifest_path),
            "--repo-root",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "hash mismatch" in completed.stderr or "hash mismatch" in completed.stdout


def test_release_provenance_verification_rejects_schema_mismatch(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    _write_artifacts(repo, include_sbom=False)
    manifest_path = _run_build(repo)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "9.9"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_PROVENANCE),
            "--manifest",
            str(manifest_path),
            "--repo-root",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "schema_version" in completed.stderr or "schema_version" in completed.stdout


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["hatchling>=1.25"]',
                'build-backend = "hatchling.build"',
                "",
                "[project]",
                'name = "self-harness"',
                'version = "0.1.0"',
                'requires-python = ">=3.11"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return repo


def _write_artifacts(repo: Path, *, include_sbom: bool) -> dict[str, Path]:
    dist = repo / "dist"
    dist.mkdir()
    wheel = dist / "self_harness-0.1.0-py3-none-any.whl"
    sdist = dist / "self_harness-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel bytes")
    sdist.write_bytes(b"sdist bytes")
    artifacts = {"wheel": wheel, "sdist": sdist}
    if include_sbom:
        sbom_dir = repo / "sbom"
        sbom_dir.mkdir()
        sbom = sbom_dir / "self_harness-sbom.json"
        sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
        artifacts["sbom"] = sbom
    return artifacts


def _run_build(repo: Path) -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILD_PROVENANCE),
            "--repo-root",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return Path(completed.stdout.strip())


def _run_verify(repo: Path, manifest_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(VERIFY_PROVENANCE),
            "--manifest",
            str(manifest_path),
            "--repo-root",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
