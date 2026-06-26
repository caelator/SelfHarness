import tomllib
from pathlib import Path

import self_harness


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert self_harness.__version__ == metadata["project"]["version"]
