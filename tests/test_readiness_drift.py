import json
import subprocess
import sys
from pathlib import Path

from self_harness.readiness_drift import (
    evaluate_readiness_drift,
    readiness_drift_report_to_jsonable,
)
from self_harness.readiness_matrix import ReadinessMatrixCatalog, load_readiness_matrix_catalog
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "readiness_drift_report.py"


def test_provisioned_reproduction_entry_fails_without_surface_result(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="scanner_check",
    )
    report = evaluate_readiness_drift(catalog)
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "not supplied" in check.detail


def test_provisioned_reproduction_entry_fails_when_surface_result_failed(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="scanner_check",
    )
    report = evaluate_readiness_drift(catalog, scanner_result={"schema_version": "1.0", "ok": False})
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "ok field is not true" in check.detail


def test_blocked_entry_with_missing_surface_is_advisory(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="blocked",
        reproduction_relevant=True,
        preflight_surface="scanner_check",
    )
    report = evaluate_readiness_drift(catalog)
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "advisory"


def test_non_reproduction_entry_with_missing_surface_is_advisory(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=False,
        preflight_surface="scanner_check",
    )
    report = evaluate_readiness_drift(catalog)
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "advisory"


def test_provisioned_reproduction_entry_passes_with_clean_surface(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="scanner_check",
    )
    report = evaluate_readiness_drift(
        catalog,
        scanner_result={
            "schema_version": "1.0",
            "ok": True,
            "checks": [{"name": "scanner", "required": True, "status": "pass"}],
        },
    )
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "pass"


def test_provisioned_reproduction_entry_fails_when_required_surface_check_failed(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="operator_preflight",
    )
    report = evaluate_readiness_drift(
        catalog,
        operator_preflight_result={
            "schema_version": "1.0",
            "ok": True,
            "checks": [{"name": "harbor_discovery_offline", "required": True, "status": "fail"}],
        },
    )
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["failed_required_checks"] == ["harbor_discovery_offline"]


def test_provisioned_reproduction_entry_fails_without_declared_surface(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="none",
    )
    report = evaluate_readiness_drift(catalog)
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "no preflight surface" in check.detail


def test_provisioned_pypi_release_smoke_surface_fails_when_missing(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="release_smoke",
        domain="pypi",
        dependency="PyPI trusted publishing",
        operator_action="publish",
    )
    report = evaluate_readiness_drift(catalog)
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert check.preflight_surface == "release_smoke"
    assert "not supplied" in check.detail


def test_provisioned_pypi_release_smoke_surface_passes_when_clean(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="release_smoke",
        domain="pypi",
        dependency="PyPI trusted publishing",
        operator_action="publish",
    )
    report = evaluate_readiness_drift(
        catalog,
        release_smoke_result={
            "schema_version": "1.0",
            "ok": True,
            "reproduction_claimed": False,
            "checks": [{"name": "wheel_install", "required": True, "status": "pass"}],
        },
    )
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "pass"


def test_provisioned_model_backend_surface_passes_when_clean(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="model_backend_preflight",
        domain="model",
        dependency="GLM-5.2 model API credentials",
        operator_action="configure",
    )
    report = evaluate_readiness_drift(
        catalog,
        model_backend_preflight_result={
            "schema_version": "1.0",
            "ok": True,
            "mode": "live",
            "reproduction_claimed": False,
            "checks": [{"name": "glm_backend_reachable", "required": True, "status": "pass"}],
        },
    )
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "pass"
    assert check.preflight_surface == "model_backend_preflight"


def test_provisioned_model_backend_surface_fails_for_replay_report(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="model_backend_preflight",
        domain="model",
        dependency="GLM-5.2 model API credentials",
        operator_action="configure",
    )
    report = evaluate_readiness_drift(
        catalog,
        model_backend_preflight_result={
            "schema_version": "1.0",
            "ok": True,
            "mode": "replay",
            "reproduction_claimed": False,
            "checks": [{"name": "glm_backend_reachable", "required": True, "status": "pass"}],
        },
    )
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "must be a live report" in check.detail


def test_provisioned_model_backend_surface_fails_when_missing(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="model_backend_preflight",
        domain="model",
        dependency="GLM-5.2 model API credentials",
        operator_action="configure",
    )
    report = evaluate_readiness_drift(catalog)
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "not supplied" in check.detail


def test_blocked_container_preflight_surface_is_advisory_with_offline_report(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="blocked",
        reproduction_relevant=True,
        preflight_surface="container_preflight",
        domain="docker",
        dependency="Docker daemon",
        operator_action="provision",
        affects=["container-demo --mode live"],
    )
    report = evaluate_readiness_drift(catalog, container_preflight_result=_offline_container_surface())
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "advisory"
    assert check.preflight_surface == "container_preflight"


def test_provisioned_container_preflight_surface_fails_for_offline_report(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="container_preflight",
        domain="docker",
        dependency="Docker daemon",
        operator_action="provision",
        affects=["container-demo --mode live"],
    )
    report = evaluate_readiness_drift(catalog, container_preflight_result=_offline_container_surface())
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "must be a live report" in check.detail


def test_provisioned_container_preflight_surface_passes_for_live_report(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="container_preflight",
        domain="docker",
        dependency="Docker daemon",
        operator_action="provision",
        affects=["container-demo --mode live"],
    )
    report = evaluate_readiness_drift(catalog, container_preflight_result=_live_container_surface())
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "pass"


def test_provisioned_container_preflight_surface_fails_for_skipped_required_live_check(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="container_preflight",
        domain="docker",
        dependency="Docker daemon",
        operator_action="provision",
        affects=["container-demo --mode live"],
    )
    surface = _live_container_surface()
    surface["checks"] = [
        {"name": "docker_cli_present", "required_for_live": True, "status": "pass"},
        {"name": "docker_daemon_reachable", "required_for_live": True, "status": "skipped"},
    ]
    report = evaluate_readiness_drift(catalog, container_preflight_result=surface)
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert check.metadata is not None
    assert check.metadata["failed_required_checks"] == ["docker_daemon_reachable"]


def test_blocked_attestation_surface_is_advisory_with_structural_report(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="blocked",
        reproduction_relevant=True,
        preflight_surface="attestation_check",
        domain="sigstore",
        dependency="Sigstore Fulcio/Rekor trust material",
        operator_action="configure",
        affects=["verify-attestation --backend sigstore"],
    )
    report = evaluate_readiness_drift(catalog, attestation_result=_structural_attestation_surface())
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "advisory"
    assert check.preflight_surface == "attestation_check"


def test_provisioned_attestation_surface_fails_for_structural_report(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="attestation_check",
        domain="sigstore",
        dependency="Sigstore Fulcio/Rekor trust material",
        operator_action="configure",
        affects=["verify-attestation --backend sigstore"],
    )
    report = evaluate_readiness_drift(catalog, attestation_result=_structural_attestation_surface())
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "must use the sigstore backend" in check.detail


def test_provisioned_attestation_surface_fails_for_failed_sigstore_crypto(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="attestation_check",
        domain="sigstore",
        dependency="Sigstore Fulcio/Rekor trust material",
        operator_action="configure",
        affects=["verify-attestation --backend sigstore"],
    )
    surface = _sigstore_attestation_surface(True)
    surface["cryptographic_valid"] = False
    report = evaluate_readiness_drift(catalog, attestation_result=surface)
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "must be cryptographically valid" in check.detail


def test_provisioned_attestation_surface_passes_for_valid_sigstore_crypto(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="attestation_check",
        domain="sigstore",
        dependency="Sigstore Fulcio/Rekor trust material",
        operator_action="configure",
        affects=["verify-attestation --backend sigstore"],
    )
    report = evaluate_readiness_drift(catalog, attestation_result=_sigstore_attestation_surface(True))
    check = report.checks[0]

    assert report.ok is True
    assert check.status == "pass"


def test_readiness_drift_rejects_container_preflight_reproduction_claim_leak(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="blocked",
        reproduction_relevant=True,
        preflight_surface="container_preflight",
        domain="docker",
        dependency="Docker daemon",
        operator_action="provision",
        affects=["container-demo --mode live"],
    )
    surface = _offline_container_surface()
    surface["reproduction_claimed"] = True
    report = evaluate_readiness_drift(catalog, container_preflight_result=surface)

    assert report.ok is False
    assert report.checks[0].name == "container_preflight_reproduction_claim"


def test_readiness_drift_rejects_attestation_reproduction_claim_leak(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="blocked",
        reproduction_relevant=True,
        preflight_surface="attestation_check",
        domain="sigstore",
        dependency="Sigstore Fulcio/Rekor trust material",
        operator_action="configure",
        affects=["verify-attestation --backend sigstore"],
    )
    surface = _structural_attestation_surface()
    surface["reproduction_claimed"] = True
    report = evaluate_readiness_drift(catalog, attestation_result=surface)

    assert report.ok is False
    assert report.checks[0].name == "attestation_check_reproduction_claim"


def test_provisioned_pypi_release_smoke_surface_fails_when_status_failed(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="release_smoke",
        domain="pypi",
        dependency="PyPI trusted publishing",
        operator_action="publish",
    )
    report = evaluate_readiness_drift(
        catalog,
        release_smoke_result={
            "schema_version": "1.0",
            "ok": False,
            "reproduction_claimed": False,
            "checks": [{"name": "wheel_install", "required": True, "status": "fail"}],
        },
    )
    check = report.checks[0]

    assert report.ok is False
    assert check.status == "fail"
    assert "ok field is not true" in check.detail


def test_readiness_drift_rejects_reproduction_claim_leak(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        status="blocked",
        reproduction_relevant=True,
        preflight_surface="scanner_check",
    )
    report = evaluate_readiness_drift(catalog, scanner_result={"ok": True, "reproduction_claimed": True})

    assert report.ok is False
    assert report.checks[0].name == "scanner_check_reproduction_claim"


def test_readiness_drift_cli_rejects_malformed_surface_json(tmp_path: Path) -> None:
    catalog_path = _catalog_path(
        tmp_path,
        status="provisioned",
        reproduction_relevant=True,
        preflight_surface="scanner_check",
    )
    malformed = tmp_path / "malformed-scanner.json"
    malformed.write_text("{", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog_path), "--scanner-result", str(malformed)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 3
    assert "invalid preflight surface JSON" in completed.stderr


def test_readiness_drift_report_hash_matches_committed_fixture() -> None:
    catalog = load_readiness_matrix_catalog(REPO_ROOT / "docs" / "operations" / "readiness_matrix.json")
    report = evaluate_readiness_drift(
        catalog,
        operator_preflight_result=_load_fixture("operator_preflight_result.json"),
        scanner_result=_load_fixture("scanner_result.json"),
        harbor_discovery_result=_load_fixture("harbor_discovery_result.json"),
        release_smoke_result=_load_fixture("release_smoke_result.json"),
        container_preflight_result=_load_fixture("container_preflight_result.json"),
        attestation_result=_load_fixture("attestation_result.json"),
    )
    committed = _load_fixture("readiness_drift_result.json")

    assert readiness_drift_report_to_jsonable(report) == committed


def _catalog(
    tmp_path: Path,
    *,
    status: str,
    reproduction_relevant: bool,
    preflight_surface: str,
    domain: str = "trivy",
    dependency: str = "Trivy binary and scanner database",
    operator_action: str = "scan",
    affects: list[str] | None = None,
) -> ReadinessMatrixCatalog:
    return load_readiness_matrix_catalog(
        _catalog_path(
            tmp_path,
            status=status,
            reproduction_relevant=reproduction_relevant,
            preflight_surface=preflight_surface,
            domain=domain,
            dependency=dependency,
            operator_action=operator_action,
            affects=affects,
        ),
        repo_root=tmp_path,
    )


def _catalog_path(
    tmp_path: Path,
    *,
    status: str,
    reproduction_relevant: bool,
    preflight_surface: str,
    domain: str = "trivy",
    dependency: str = "Trivy binary and scanner database",
    operator_action: str = "scan",
    affects: list[str] | None = None,
) -> Path:
    (tmp_path / "fixture.json").write_text("{}\n", encoding="utf-8")
    catalog = {
        "schema_version": "1.1",
        "entries": [
            {
                "dependency": dependency,
                "domain": domain,
                "status": status,
                "affects": affects or ["scripts/scanner_run.py live"],
                "offline_fixture": "fixture.json",
                "operator_remediation": "Install Trivy and seed a fresh database.",
                "reproduction_relevant": reproduction_relevant,
                "preflight_surface": preflight_surface,
                "operator_action": operator_action,
            }
        ],
    }
    path = tmp_path / "readiness_matrix.json"
    path.write_text(stable_json_dumps(catalog) + "\n", encoding="utf-8")
    return path


def _load_fixture(name: str) -> dict[str, object]:
    path = REPO_ROOT / "tests" / "fixtures" / "release_candidate" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _offline_container_surface() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": True,
        "mode": "offline",
        "reproduction_claimed": False,
        "checks": [
            {"name": "docker_cli_present", "required_for_live": True, "status": "pass"},
            {"name": "docker_daemon_reachable", "required_for_live": False, "status": "skipped"},
        ],
    }


def _live_container_surface() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": True,
        "mode": "live",
        "reproduction_claimed": False,
        "checks": [
            {"name": "docker_cli_present", "required_for_live": True, "status": "pass"},
            {"name": "docker_daemon_reachable", "required_for_live": True, "status": "pass"},
        ],
    }


def _structural_attestation_surface() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": True,
        "backend": "structural",
        "cryptographic_valid": None,
        "reproduction_claimed": False,
        "checks": [{"name": "cryptographic_verification", "status": "pass"}],
    }


def _sigstore_attestation_surface(cryptographic_valid: bool) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": cryptographic_valid,
        "backend": "sigstore",
        "cryptographic_valid": cryptographic_valid,
        "reproduction_claimed": False,
        "checks": [
            {
                "name": "cryptographic_verification",
                "required": True,
                "status": "pass" if cryptographic_valid else "fail",
            }
        ],
    }
