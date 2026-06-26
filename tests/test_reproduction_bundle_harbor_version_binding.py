import json
from hashlib import sha256
from pathlib import Path

from self_harness.reproduction_bundle import (
    ReproductionBundle,
    ReproductionBundleCheck,
    ReproductionBundleEntry,
    _cross_artifact_harbor_version_binding,
)
from self_harness.types import stable_json_dumps
from test_reproduction_readiness import _class_shaped_payloads


def test_harbor_version_binding_accepts_matching_versions(tmp_path: Path) -> None:
    check = _check(tmp_path)

    assert check is not None
    assert check.status == "pass"
    assert check.metadata == {
        "split_harbor_version": "2.10.0",
        "preflight_harbor_version": "2.10.0",
    }


def test_harbor_version_binding_rejects_version_drift(tmp_path: Path) -> None:
    check = _check(tmp_path, split_harbor_version="2.10.0", preflight_harbor_version="2.11.0")

    assert check is not None
    assert check.status == "fail"
    assert "harbor_version must match" in check.detail
    assert check.metadata == {
        "split_harbor_version": "2.10.0",
        "preflight_harbor_version": "2.11.0",
    }


def test_harbor_version_binding_rejects_split_without_harbor_version(tmp_path: Path) -> None:
    check = _check(tmp_path, include_split_harbor_version=False)

    assert check is not None
    assert check.status == "fail"
    assert "missing non-empty string field: harbor_version" in check.detail


def test_harbor_version_binding_rejects_only_split_artifact(tmp_path: Path) -> None:
    bundle, split_entry, preflight_entry = _bundle(tmp_path, include_preflight=False)

    check = _cross_artifact_harbor_version_binding(bundle, split_entry, preflight_entry)

    assert check is not None
    assert check.status == "fail"
    assert check.detail == "live Harbor preflight report artifact is missing"


def test_harbor_version_binding_rejects_only_preflight_artifact(tmp_path: Path) -> None:
    bundle, split_entry, preflight_entry = _bundle(tmp_path, include_split=False)

    check = _cross_artifact_harbor_version_binding(bundle, split_entry, preflight_entry)

    assert check is not None
    assert check.status == "fail"
    assert check.detail == "live Terminal-Bench split manifest artifact is missing"


def test_harbor_version_binding_skips_when_both_artifacts_absent(tmp_path: Path) -> None:
    bundle, split_entry, preflight_entry = _bundle(tmp_path, include_split=False, include_preflight=False)

    check = _cross_artifact_harbor_version_binding(bundle, split_entry, preflight_entry)

    assert check is None


def _check(
    tmp_path: Path,
    *,
    split_harbor_version: str = "2.10.0",
    preflight_harbor_version: str = "2.10.0",
    include_split_harbor_version: bool = True,
) -> ReproductionBundleCheck | None:
    bundle, split_entry, preflight_entry = _bundle(
        tmp_path,
        split_harbor_version=split_harbor_version,
        preflight_harbor_version=preflight_harbor_version,
        include_split_harbor_version=include_split_harbor_version,
    )
    return _cross_artifact_harbor_version_binding(bundle, split_entry, preflight_entry)


def _bundle(
    tmp_path: Path,
    *,
    include_split: bool = True,
    include_preflight: bool = True,
    split_harbor_version: str = "2.10.0",
    preflight_harbor_version: str = "2.10.0",
    include_split_harbor_version: bool = True,
) -> tuple[ReproductionBundle, ReproductionBundleEntry | None, ReproductionBundleEntry | None]:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bundle_path = tmp_path / "bundle.json"
    payloads = json.loads(stable_json_dumps(_class_shaped_payloads()))
    entries: list[ReproductionBundleEntry] = []
    split_entry: ReproductionBundleEntry | None = None
    preflight_entry: ReproductionBundleEntry | None = None

    if include_split:
        split = payloads["live_terminal_bench_split_manifest"]
        if include_split_harbor_version:
            split["harbor_version"] = split_harbor_version
        else:
            split.pop("harbor_version", None)
        split_entry = _write_entry(
            bundle_path,
            artifacts / "live_terminal_bench_split_manifest.json",
            "live_terminal_bench_split_manifest",
            split,
        )
        entries.append(split_entry)

    if include_preflight:
        preflight = payloads["live_harbor_preflight_report"]
        preflight["harbor_version"] = preflight_harbor_version
        preflight_entry = _write_entry(
            bundle_path,
            artifacts / "live_harbor_preflight_report.json",
            "live_harbor_preflight_report",
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
        split_entry,
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
