import json
import shutil
from pathlib import Path

import pytest

from self_harness.audit import load_audit_run
from self_harness.audit_migration import AuditMigrationError, migrate_audit_tree

FIXTURE_ROOT = Path("tests/fixtures/audit_migration")


@pytest.mark.parametrize("fixture_name", ["schema_1_0", "schema_1_1", "schema_1_2", "schema_1_3"])
def test_lossless_fixture_matrix_has_expected_hashes(tmp_path: Path, fixture_name: str) -> None:
    source = FIXTURE_ROOT / fixture_name
    destination = tmp_path / fixture_name
    expected_hashes = _expected_hashes()

    report = migrate_audit_tree(source, destination, target_major="1")
    audit = load_audit_run(destination)
    manifest = audit.manifest

    assert report.to_schema_version == "1.4"
    assert report.classification == "lossless"
    assert report.source_audit_hash == report.source_audit_hash_after
    assert report.destination_audit_hash == expected_hashes[fixture_name]
    assert report.transform_ids
    assert manifest["schema_version"] == "1.4"
    assert manifest["migration_applied"] is True
    assert manifest.get("reproduction_claimed") is not True
    assert manifest["migration_provenance"]["source_audit_hash"] == report.source_audit_hash
    assert manifest["migration_provenance"]["source_schema_version"] == report.from_schema_version
    assert manifest["migration_provenance"]["target_schema_version"] == "1.4"
    assert manifest["migration_provenance"]["classification"] == "lossless"
    assert manifest["migration_provenance"]["transform_ids"] == list(report.transform_ids)
    assert all(row.get("schema_version") == "1.4" for row in audit.lineage)
    assert all(
        row.get("schema_version") == "1.4"
        for round_ in audit.rounds
        for row in [*round_.proposals, *round_.evaluations]
    )


def test_current_schema_same_major_rejects_as_already_current(tmp_path: Path) -> None:
    with pytest.raises(AuditMigrationError, match="already at target"):
        migrate_audit_tree(FIXTURE_ROOT / "schema_1_4", tmp_path / "migrated", target_major="1")


def test_target_major_requires_explicit_source_schema(tmp_path: Path) -> None:
    source = tmp_path / "legacy-missing-schema"
    shutil.copytree(FIXTURE_ROOT / "schema_1_0", source)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("schema_version")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(AuditMigrationError, match="schema_version is required"):
        migrate_audit_tree(source, tmp_path / "migrated", target_major="1")

    default_report = migrate_audit_tree(source, tmp_path / "default-migrated")
    assert default_report.from_schema_version == "1.0"
    assert default_report.to_schema_version == "1.4"


def test_lossy_transform_requires_flag_and_records_provenance(tmp_path: Path) -> None:
    source = FIXTURE_ROOT / "schema_1_0"
    transforms_json = FIXTURE_ROOT / "transforms" / "lossy_drop_manifest_field.json"

    with pytest.raises(AuditMigrationError, match="requires --allow-lossy"):
        migrate_audit_tree(source, tmp_path / "rejected", target_major="1", transforms_json=transforms_json)

    report = migrate_audit_tree(
        source,
        tmp_path / "migrated",
        target_major="1",
        transforms_json=transforms_json,
        allow_lossy=True,
    )
    manifest = json.loads((tmp_path / "migrated" / "manifest.json").read_text(encoding="utf-8"))

    assert report.classification == "lossy"
    assert report.transform_ids == ("drop-legacy-manifest-field",)
    assert "legacy_drop" not in manifest
    assert manifest["migration_provenance"]["classification"] == "lossy"
    assert manifest["migration_provenance"]["lossy_allowed"] is True
    assert manifest["migration_provenance"]["notes"] == [
        "drops legacy manifest field that has no schema 1.4 equivalent"
    ]


def test_unsupported_transform_and_target_major_fail_closed(tmp_path: Path) -> None:
    unsupported = FIXTURE_ROOT / "transforms" / "unsupported_direct.json"

    with pytest.raises(AuditMigrationError, match="unsupported audit migration transform"):
        migrate_audit_tree(
            FIXTURE_ROOT / "schema_1_0",
            tmp_path / "unsupported",
            target_major="1",
            transforms_json=unsupported,
        )

    with pytest.raises(AuditMigrationError, match="unsupported target audit schema major"):
        migrate_audit_tree(FIXTURE_ROOT / "schema_1_0", tmp_path / "major-2", target_major="2")


def test_reproduction_claims_are_not_carried_forward(tmp_path: Path) -> None:
    source = tmp_path / "reproduction-source"
    shutil.copytree(FIXTURE_ROOT / "schema_1_3", source)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reproduction_claimed"] = True
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(AuditMigrationError, match="must not claim benchmark reproduction"):
        migrate_audit_tree(source, tmp_path / "migrated", target_major="1")


def test_migration_does_not_rotate_readiness_hash_fixture(tmp_path: Path) -> None:
    canonical_hash = Path("tests/fixtures/canonical_audit_hash.txt")
    before = canonical_hash.read_text(encoding="utf-8")

    migrate_audit_tree(FIXTURE_ROOT / "schema_1_0", tmp_path / "migrated", target_major="1")

    assert canonical_hash.read_text(encoding="utf-8") == before


def _expected_hashes() -> dict[str, str]:
    rows = [
        line.split()
        for line in (FIXTURE_ROOT / "expected_hashes.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {name: value for name, value in rows}
