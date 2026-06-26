import json
import subprocess
import sys
from pathlib import Path

import pytest

from self_harness.readiness_matrix import (
    ReadinessMatrixError,
    evaluate_readiness_matrix,
    load_readiness_matrix_catalog,
    readiness_matrix_report_to_jsonable,
)
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "readiness_matrix_report.py"


def test_readiness_matrix_loads_catalog_and_reports_live_blocker(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)
    catalog = load_readiness_matrix_catalog(path, repo_root=tmp_path)
    report = evaluate_readiness_matrix(catalog)
    payload = readiness_matrix_report_to_jsonable(report)

    assert catalog.schema_version == "1.0"
    assert report.ok is True
    assert report.live_execution_blocked is True
    assert report.blocked_count == 1
    assert report.optional_count == 0
    assert report.provisioned_count == 0
    assert report.reproduction_claimed is False
    assert len(report.report_hash) == 64
    assert "not benchmark reproduction evidence" in report.boundary
    assert payload["rows"][0]["offline_fixture"] == "fixture.json"
    assert payload["rows"][0]["preflight_surface"] == "none"
    assert payload["rows"][0]["operator_action"] == "provision"


def test_readiness_matrix_loads_schema_1_1_preflight_metadata(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        schema_version="1.1",
        entry_overrides={"preflight_surface": "scanner_check", "operator_action": "scan"},
    )
    catalog = load_readiness_matrix_catalog(path, repo_root=tmp_path)
    entry = catalog.entries[0]

    assert catalog.schema_version == "1.1"
    assert entry.preflight_surface == "scanner_check"
    assert entry.operator_action == "scan"


def test_readiness_matrix_rejects_unknown_catalog_field(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, extra={"unexpected": True})

    with pytest.raises(ReadinessMatrixError, match="unknown readiness matrix catalog field"):
        load_readiness_matrix_catalog(path, repo_root=tmp_path)


def test_readiness_matrix_rejects_missing_fixture(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, entry_overrides={"offline_fixture": "missing.json"})

    with pytest.raises(ReadinessMatrixError, match="offline_fixture does not exist"):
        load_readiness_matrix_catalog(path, repo_root=tmp_path)


def test_readiness_matrix_rejects_unknown_affected_gate(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, entry_overrides={"affects": ["unknown-live-gate"]})

    with pytest.raises(ReadinessMatrixError, match="unknown affected gate"):
        load_readiness_matrix_catalog(path, repo_root=tmp_path)


def test_readiness_matrix_report_hash_is_deterministic(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)
    first = evaluate_readiness_matrix(load_readiness_matrix_catalog(path, repo_root=tmp_path))
    second = evaluate_readiness_matrix(load_readiness_matrix_catalog(path, repo_root=tmp_path))

    assert first.report_hash == second.report_hash


def test_readiness_matrix_rejects_reproduction_claim_field(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, entry_overrides={"reproduction_claimed": True})

    with pytest.raises(ReadinessMatrixError, match="reproduction_claimed"):
        load_readiness_matrix_catalog(path, repo_root=tmp_path)


def test_readiness_matrix_rejects_unknown_preflight_surface(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, entry_overrides={"preflight_surface": "magic"})

    with pytest.raises(ReadinessMatrixError, match="unknown preflight_surface"):
        load_readiness_matrix_catalog(path, repo_root=tmp_path)


def test_readiness_matrix_rejects_unknown_operator_action(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, entry_overrides={"operator_action": "magic"})

    with pytest.raises(ReadinessMatrixError, match="unknown operator_action"):
        load_readiness_matrix_catalog(path, repo_root=tmp_path)


def test_readiness_matrix_cli_writes_report(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)
    out = tmp_path / "report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--catalog", str(path), "--out", str(out)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(out.read_text(encoding="utf-8"))

    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["live_execution_blocked"] is True
    assert json.loads(completed.stdout) == report


def _write_catalog(
    tmp_path: Path,
    *,
    schema_version: str = "1.0",
    entry_overrides: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    (tmp_path / "fixture.json").write_text("{}\n", encoding="utf-8")
    entry = {
        "dependency": "Docker daemon",
        "domain": "docker",
        "status": "blocked",
        "affects": ["terminal-bench --mode live"],
        "offline_fixture": "fixture.json",
        "operator_remediation": "Start Docker before live Terminal-Bench execution.",
        "reproduction_relevant": True,
    }
    if entry_overrides is not None:
        entry.update(entry_overrides)
    catalog = {
        "schema_version": schema_version,
        "entries": [entry],
    }
    if extra is not None:
        catalog.update(extra)
    path = tmp_path / "readiness_matrix.json"
    path.write_text(stable_json_dumps(catalog) + "\n", encoding="utf-8")
    return path
