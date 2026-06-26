import json
from pathlib import Path

from self_harness.readiness_matrix import evaluate_readiness_matrix, load_readiness_matrix_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "docs" / "operations" / "readiness_matrix.json"


def test_readiness_matrix_catalog_never_claims_reproduction() -> None:
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    catalog = load_readiness_matrix_catalog(CATALOG, repo_root=REPO_ROOT)
    report = evaluate_readiness_matrix(catalog)

    assert _contains_reproduction_claim(raw) is False
    assert report.reproduction_claimed is False
    assert "does not probe" in report.boundary


def test_blocked_reproduction_dependencies_mark_live_execution_blocked() -> None:
    catalog = load_readiness_matrix_catalog(CATALOG, repo_root=REPO_ROOT)
    report = evaluate_readiness_matrix(catalog)
    blocked_reproduction_dependencies = [
        entry for entry in catalog.entries if entry.status == "blocked" and entry.reproduction_relevant
    ]

    assert blocked_reproduction_dependencies
    assert report.live_execution_blocked is True


def _contains_reproduction_claim(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("reproduction_claimed") is True:
            return True
        return any(_contains_reproduction_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_reproduction_claim(item) for item in value)
    return False
