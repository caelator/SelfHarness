import json
import subprocess
import sys
from pathlib import Path

from self_harness.readiness_matrix import ReadinessMatrixCatalog, load_readiness_matrix_catalog
from self_harness.readiness_promotion import (
    evaluate_readiness_promotion,
    readiness_promotion_report_to_jsonable,
)
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "readiness_promotion_report.py"


def test_readiness_promotion_noop_is_clean(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry()])
    candidate = _catalog(tmp_path, [_entry()])

    report = evaluate_readiness_promotion(baseline, candidate, surface_results={})
    payload = readiness_promotion_report_to_jsonable(report)

    assert report.ok is True
    assert payload["unchanged_count"] == 1
    assert payload["admitted_transitions"] == []
    assert payload["rejected_transitions"] == []


def test_readiness_promotion_admits_evidence_backed_promotion(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="blocked")])
    candidate = _catalog(tmp_path, [_entry(status="provisioned")])

    report = evaluate_readiness_promotion(
        baseline,
        candidate,
        surface_results={"scanner_check": _scanner_surface()},
    )

    assert report.ok is True
    assert len(report.admitted_transitions) == 1
    transition = report.admitted_transitions[0]
    assert transition.dependency == "Trivy binary and scanner database"
    assert transition.transition == "promoted"
    assert transition.preflight_surface == "scanner_check"


def test_readiness_promotion_rejects_missing_evidence_for_promotion(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="blocked")])
    candidate = _catalog(tmp_path, [_entry(status="provisioned")])

    report = evaluate_readiness_promotion(baseline, candidate, surface_results={})

    assert report.ok is False
    assert report.rejected_transitions[0].transition == "promoted"
    assert "not supplied" in report.rejected_transitions[0].detail


def test_readiness_promotion_reuses_surface_specific_live_rules(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_docker_entry(status="blocked")])
    candidate = _catalog(tmp_path, [_docker_entry(status="provisioned")])

    offline = evaluate_readiness_promotion(
        baseline,
        candidate,
        surface_results={"container_preflight": _container_surface(mode="offline")},
    )
    live = evaluate_readiness_promotion(
        baseline,
        candidate,
        surface_results={"container_preflight": _container_surface(mode="live")},
    )

    assert offline.ok is False
    assert "must be a live report" in offline.rejected_transitions[0].detail
    assert live.ok is True
    assert live.admitted_transitions[0].transition == "promoted"


def test_readiness_promotion_rejects_demotions_and_removed_entries(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="provisioned"), _docker_entry(status="blocked")])
    candidate = _catalog(tmp_path, [_entry(status="blocked")])

    report = evaluate_readiness_promotion(
        baseline,
        candidate,
        surface_results={"scanner_check": _scanner_surface()},
    )

    assert report.ok is False
    assert {transition.transition for transition in report.rejected_transitions} == {"demoted", "removed"}


def test_readiness_promotion_allow_demotions_records_admission(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="provisioned")])
    candidate = _catalog(tmp_path, [_entry(status="blocked")])

    report = evaluate_readiness_promotion(
        baseline,
        candidate,
        surface_results={},
        allow_demotions=True,
    )

    assert report.ok is True
    assert report.admitted_transitions[0].transition == "metadata-or-status-edit"


def test_readiness_promotion_rejects_preflight_surface_change_on_provisioned_row(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="provisioned", preflight_surface="scanner_check")])
    candidate = _catalog(tmp_path, [_entry(status="provisioned", preflight_surface="operator_preflight")])

    report = evaluate_readiness_promotion(
        baseline,
        candidate,
        surface_results={"operator_preflight": _scanner_surface()},
    )

    assert report.ok is False
    assert report.rejected_transitions[0].transition == "preflight-surface-changed"


def test_readiness_promotion_requires_evidence_for_provisioned_row_edits(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="provisioned", remediation="Install Trivy.")])
    candidate = _catalog(tmp_path, [_entry(status="provisioned", remediation="Install Trivy and seed DB.")])

    missing = evaluate_readiness_promotion(baseline, candidate, surface_results={})
    admitted = evaluate_readiness_promotion(baseline, candidate, surface_results={"scanner_check": _scanner_surface()})

    assert missing.ok is False
    assert missing.rejected_transitions[0].transition == "provisioned-edit"
    assert admitted.ok is True
    assert admitted.admitted_transitions[0].transition == "provisioned-edit"


def test_readiness_promotion_admits_new_blocked_entry(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry()])
    candidate = _catalog(tmp_path, [_entry(), _docker_entry(status="blocked")])

    report = evaluate_readiness_promotion(baseline, candidate, surface_results={})

    assert report.ok is True
    assert report.admitted_transitions[0].transition == "added"
    assert report.admitted_transitions[0].candidate_status == "blocked"


def test_readiness_promotion_rejects_surface_reproduction_claim_leak(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="blocked")])
    candidate = _catalog(tmp_path, [_entry(status="provisioned")])
    surface = _scanner_surface()
    surface["metadata"] = {"reproduction_claimed": True}

    report = evaluate_readiness_promotion(baseline, candidate, surface_results={"scanner_check": surface})

    assert report.ok is False
    assert any(transition.transition == "surface-reproduction-claim" for transition in report.rejected_transitions)


def test_readiness_promotion_report_hash_is_deterministic(tmp_path: Path) -> None:
    baseline = _catalog(tmp_path, [_entry(status="blocked")])
    candidate = _catalog(tmp_path, [_entry(status="provisioned")])

    first = evaluate_readiness_promotion(baseline, candidate, surface_results={"scanner_check": _scanner_surface()})
    second = evaluate_readiness_promotion(baseline, candidate, surface_results={"scanner_check": _scanner_surface()})

    assert first.report_hash == second.report_hash
    assert readiness_promotion_report_to_jsonable(first) == readiness_promotion_report_to_jsonable(second)


def test_readiness_promotion_cli_writes_report_and_exit_codes(tmp_path: Path) -> None:
    baseline_path = _catalog_path(tmp_path, "baseline", [_entry(status="blocked")])
    candidate_path = _catalog_path(tmp_path, "candidate", [_entry(status="provisioned")])
    scanner = tmp_path / "scanner.json"
    out = tmp_path / "promotion.json"
    scanner.write_text(stable_json_dumps(_scanner_surface()) + "\n", encoding="utf-8")

    clean = _run_cli(
        "--baseline-catalog",
        str(baseline_path),
        "--candidate-catalog",
        str(candidate_path),
        "--scanner-result",
        str(scanner),
        "--out",
        str(out),
    )
    rejected = _run_cli(
        "--baseline-catalog",
        str(baseline_path),
        "--candidate-catalog",
        str(candidate_path),
        "--out",
        str(tmp_path / "rejected.json"),
    )

    assert clean.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is True
    assert rejected.returncode == 2
    assert json.loads(rejected.stdout)["ok"] is False


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _catalog(tmp_path: Path, entries: list[dict[str, object]], *, name: str = "catalog") -> ReadinessMatrixCatalog:
    return load_readiness_matrix_catalog(_catalog_path(tmp_path, name, entries), repo_root=tmp_path)


def _catalog_path(tmp_path: Path, name: str, entries: list[dict[str, object]]) -> Path:
    (tmp_path / "fixture.json").write_text("{}\n", encoding="utf-8")
    path = tmp_path / f"{name}.json"
    path.write_text(stable_json_dumps({"schema_version": "1.1", "entries": entries}) + "\n", encoding="utf-8")
    return path


def _entry(
    *,
    status: str = "blocked",
    preflight_surface: str = "scanner_check",
    remediation: str = "Install Trivy and seed a fresh database.",
) -> dict[str, object]:
    return {
        "dependency": "Trivy binary and scanner database",
        "domain": "trivy",
        "status": status,
        "affects": ["scripts/scanner_run.py live"],
        "offline_fixture": "fixture.json",
        "operator_remediation": remediation,
        "reproduction_relevant": True,
        "preflight_surface": preflight_surface,
        "operator_action": "scan",
    }


def _docker_entry(*, status: str) -> dict[str, object]:
    return {
        "dependency": "Docker daemon",
        "domain": "docker",
        "status": status,
        "affects": ["container-demo --mode live"],
        "offline_fixture": "fixture.json",
        "operator_remediation": "Start Docker.",
        "reproduction_relevant": True,
        "preflight_surface": "container_preflight",
        "operator_action": "provision",
    }


def _scanner_surface() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": True,
        "reproduction_claimed": False,
        "checks": [{"name": "scanner", "required": True, "status": "pass"}],
    }


def _container_surface(*, mode: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": True,
        "mode": mode,
        "reproduction_claimed": False,
        "checks": [
            {"name": "docker_cli_present", "required_for_live": True, "status": "pass"},
            {"name": "docker_daemon_reachable", "required_for_live": mode == "live", "status": "pass"},
        ],
    }
