import json
from hashlib import sha256
from pathlib import Path

from self_harness.reproduction_bundle import (
    ReproductionBundle,
    ReproductionBundleCheck,
    ReproductionBundleEntry,
    _cross_artifact_capture_run_id_binding,
)
from self_harness.types import stable_json_dumps
from test_reproduction_readiness import _class_shaped_payloads

PRIMARY_CLASSES = (
    "container_image_trust_report",
    "fixed_protocol_config",
    "live_harbor_audit",
    "live_harbor_preflight_report",
    "live_terminal_bench_split_manifest",
    "live_two_repeat_evaluation_report",
    "model_backend_preflight_report",
    "network_resource_controls_attestation",
)


def test_capture_run_binding_accepts_matching_primary_artifacts(tmp_path: Path) -> None:
    check = _check(tmp_path)

    assert check is not None
    assert check.status == "pass"
    assert check.metadata is not None
    assert check.metadata["unique_capture_run_ids"] == ["fixture-capture-run-p72"]
    assert check.metadata["missing_capture_run_id"] == []


def test_capture_run_binding_rejects_version_drift(tmp_path: Path) -> None:
    check = _check(tmp_path, overrides={"live_harbor_audit": "other-capture-run"})

    assert check is not None
    assert check.status == "fail"
    assert "must share one capture_run_id" in check.detail
    assert check.metadata is not None
    assert check.metadata["capture_run_ids_by_artifact"]["live_harbor_audit"] == "other-capture-run"
    assert check.metadata["unique_capture_run_ids"] == ["fixture-capture-run-p72", "other-capture-run"]


def test_capture_run_binding_rejects_missing_primary_capture_run_id(tmp_path: Path) -> None:
    check = _check(tmp_path, remove_capture_run_id_from={"model_backend_preflight_report"})

    assert check is not None
    assert check.status == "fail"
    assert "must record capture_run_id" in check.detail
    assert check.metadata is not None
    assert check.metadata["missing_capture_run_id"] == ["model_backend_preflight_report"]


def test_capture_run_binding_skips_for_derived_artifacts_only(tmp_path: Path) -> None:
    check = _check(
        tmp_path,
        include_classes=("audit_verify_report", "release_candidate_evidence"),
    )

    assert check is None


def _check(
    tmp_path: Path,
    *,
    include_classes: tuple[str, ...] = PRIMARY_CLASSES,
    overrides: dict[str, str] | None = None,
    remove_capture_run_id_from: set[str] | None = None,
) -> ReproductionBundleCheck | None:
    bundle = _bundle(
        tmp_path,
        include_classes=include_classes,
        overrides=overrides or {},
        remove_capture_run_id_from=remove_capture_run_id_from or set(),
    )
    return _cross_artifact_capture_run_id_binding(bundle)


def _bundle(
    tmp_path: Path,
    *,
    include_classes: tuple[str, ...],
    overrides: dict[str, str],
    remove_capture_run_id_from: set[str],
) -> ReproductionBundle:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bundle_path = tmp_path / "bundle.json"
    payloads = json.loads(stable_json_dumps(_class_shaped_payloads()))
    entries: list[ReproductionBundleEntry] = []
    for artifact_class in include_classes:
        payload = payloads[artifact_class]
        if artifact_class in overrides:
            payload["capture_run_id"] = overrides[artifact_class]
        if artifact_class in remove_capture_run_id_from:
            payload.pop("capture_run_id", None)
        entries.append(
            _write_entry(
                bundle_path,
                artifacts / f"{artifact_class}.json",
                artifact_class,
                payload,
            )
        )
    return ReproductionBundle(
        schema_version="1.0",
        bundle_id="test-bundle",
        created_at="2026-06-24T00:00:00Z",
        operator_label="self-harness-tests",
        entries=tuple(entries),
        path=bundle_path,
        reproduction_claimed=False,
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
