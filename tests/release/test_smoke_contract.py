from pathlib import Path


def test_release_smoke_script_documents_artifact_parity_contract() -> None:
    script = Path("scripts/release_smoke.py").read_text(encoding="utf-8")

    assert "--wheel" in script
    assert "--repo-root" in script
    assert "TemporaryDirectory" in script
    assert "_create_venv" in script
    assert '"-m", "venv"' in script
    assert "pip\", \"install\", str(wheel)" in script
    assert "self-harness" in script
    assert "audit-trajectory" in script
    assert "inspect-harness" in script
    assert "canonical_audit_hash.txt" in script
    assert "audit_tree_hash" in script
    assert "PYTHONPATH" in script
