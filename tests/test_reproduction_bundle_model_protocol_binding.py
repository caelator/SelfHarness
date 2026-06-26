import json
from hashlib import sha256
from pathlib import Path

from self_harness.reproduction_bundle import (
    ReproductionBundle,
    ReproductionBundleCheck,
    ReproductionBundleEntry,
    _cross_artifact_model_protocol_binding,
)
from self_harness.types import stable_json_dumps
from test_reproduction_readiness import _class_shaped_payloads


def test_model_protocol_binding_accepts_matching_canonical_backends(tmp_path: Path) -> None:
    check = _check(tmp_path)

    assert check is not None
    assert check.status == "pass"
    assert check.metadata == {
        "protocol_backends": ["glm", "minimax", "qwen"],
        "preflight_backends": ["glm", "minimax", "qwen"],
        "paper_backends": ["glm", "minimax", "qwen"],
    }


def test_model_protocol_binding_accepts_alias_equivalent_backends(tmp_path: Path) -> None:
    check = _check(
        tmp_path,
        protocol_models=["minimax-m2.5", "qwen3.5-35b-a3b", "glm-5"],
        preflight_backends=["minimax", "qwen", "glm"],
    )

    assert check is not None
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["protocol_backends"] == ["glm", "minimax", "qwen"]


def test_model_protocol_binding_rejects_missing_preflight_backend(tmp_path: Path) -> None:
    check = _check(tmp_path, preflight_backends=["minimax", "qwen"])

    assert check is not None
    assert check.status == "fail"
    assert "model backend preflight report backends must cover" in check.detail
    assert "must match fixed protocol config models" in check.detail


def test_model_protocol_binding_rejects_extra_preflight_backend(tmp_path: Path) -> None:
    check = _check(tmp_path, preflight_backends=["minimax", "qwen", "glm", "sonnet"])

    assert check is not None
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["preflight_backends"] == ["glm", "minimax", "qwen", "sonnet"]


def test_model_protocol_binding_rejects_only_protocol_artifact(tmp_path: Path) -> None:
    bundle, protocol_entry, preflight_entry = _bundle(tmp_path, include_preflight=False)

    check = _cross_artifact_model_protocol_binding(bundle, protocol_entry, preflight_entry)

    assert check is not None
    assert check.status == "fail"
    assert check.detail == "model backend preflight report artifact is missing"


def test_model_protocol_binding_rejects_only_preflight_artifact(tmp_path: Path) -> None:
    bundle, protocol_entry, preflight_entry = _bundle(tmp_path, include_protocol=False)

    check = _cross_artifact_model_protocol_binding(bundle, protocol_entry, preflight_entry)

    assert check is not None
    assert check.status == "fail"
    assert check.detail == "fixed protocol config artifact is missing"


def test_model_protocol_binding_skips_when_both_artifacts_absent(tmp_path: Path) -> None:
    bundle, protocol_entry, preflight_entry = _bundle(
        tmp_path,
        include_protocol=False,
        include_preflight=False,
    )

    check = _cross_artifact_model_protocol_binding(bundle, protocol_entry, preflight_entry)

    assert check is None


def _check(
    tmp_path: Path,
    *,
    protocol_models: list[str] | None = None,
    preflight_backends: list[str] | None = None,
) -> ReproductionBundleCheck | None:
    bundle, protocol_entry, preflight_entry = _bundle(
        tmp_path,
        protocol_models=protocol_models,
        preflight_backends=preflight_backends,
    )
    return _cross_artifact_model_protocol_binding(bundle, protocol_entry, preflight_entry)


def _bundle(
    tmp_path: Path,
    *,
    include_protocol: bool = True,
    include_preflight: bool = True,
    protocol_models: list[str] | None = None,
    preflight_backends: list[str] | None = None,
) -> tuple[ReproductionBundle, ReproductionBundleEntry | None, ReproductionBundleEntry | None]:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bundle_path = tmp_path / "bundle.json"
    payloads = json.loads(stable_json_dumps(_class_shaped_payloads()))
    entries: list[ReproductionBundleEntry] = []
    protocol_entry: ReproductionBundleEntry | None = None
    preflight_entry: ReproductionBundleEntry | None = None

    if include_protocol:
        protocol = payloads["fixed_protocol_config"]
        if protocol_models is not None:
            protocol["models"] = protocol_models
        protocol_entry = _write_entry(
            bundle_path,
            artifacts / "fixed_protocol_config.json",
            "fixed_protocol_config",
            protocol,
        )
        entries.append(protocol_entry)

    if include_preflight:
        preflight = payloads["model_backend_preflight_report"]
        if preflight_backends is not None:
            preflight["backends"] = preflight_backends
        preflight_entry = _write_entry(
            bundle_path,
            artifacts / "model_backend_preflight_report.json",
            "model_backend_preflight_report",
            preflight,
        )
        entries.append(preflight_entry)

    return (
        ReproductionBundle(
            schema_version="1.0",
            bundle_id="test-bundle",
            created_at="2026-06-24T00:00:00Z",
            operator_label="self-harness-tests",
            entries=tuple(entries),
            path=bundle_path,
            reproduction_claimed=False,
        ),
        protocol_entry,
        preflight_entry,
    )


def _write_entry(
    bundle_path: Path,
    path: Path,
    artifact_class: str,
    payload: dict[str, object],
) -> ReproductionBundleEntry:
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    data = path.read_bytes()
    return ReproductionBundleEntry(
        required_artifact_class=artifact_class,
        path=str(path.relative_to(bundle_path.parent)),
        sha256=sha256(data).hexdigest(),
        byte_size=len(data),
        source={
            "provider": "fixture",
            "captured_at": "2026-06-24T00:00:00Z",
            "operator_label": "self-harness-tests",
        },
        notes=None,
    )
