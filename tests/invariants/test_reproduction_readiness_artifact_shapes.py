from pathlib import Path

from self_harness._artifact_shapes import (
    artifact_shape_error,
    supported_reproduction_artifact_classes,
)
from self_harness.reproduction_readiness import load_reproduction_requirements
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO_ROOT / "docs" / "operations" / "benchmark_reproduction_requirements.json"


def test_every_reproduction_requirement_has_shape_validator() -> None:
    requirements = load_reproduction_requirements(REQUIREMENTS)
    required_classes = {requirement.required_artifact_class for requirement in requirements}

    assert required_classes == set(supported_reproduction_artifact_classes())


def test_unknown_reproduction_artifact_class_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "unknown.json"
    artifact.write_text(stable_json_dumps({"ok": True, "reproduction_claimed": False}) + "\n", encoding="utf-8")

    assert artifact_shape_error("unknown_artifact_class", artifact) == (
        "unsupported required_artifact_class: unknown_artifact_class"
    )


def test_generic_placeholders_do_not_satisfy_registered_artifact_classes(tmp_path: Path) -> None:
    artifact = tmp_path / "placeholder.json"
    artifact.write_text(stable_json_dumps({"ok": True, "reproduction_claimed": False}) + "\n", encoding="utf-8")

    errors = {
        artifact_class: artifact_shape_error(artifact_class, artifact)
        for artifact_class in supported_reproduction_artifact_classes()
    }

    assert set(errors) == set(supported_reproduction_artifact_classes())
    assert all(error is not None for error in errors.values())
