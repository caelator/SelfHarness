import json
from pathlib import Path

import pytest

from self_harness.audit import load_audit_run
from self_harness.audit_migration import (
    AuditMigrationError,
    detect_audit_schema_version,
    migrate_audit_tree,
)
from self_harness.cli import main
from self_harness.types import write_jsonl, write_stable_json


def test_migrate_synthetic_schema_10_audit_to_latest_copy(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "migrated"
    _write_synthetic_schema_10_audit(source)

    report = migrate_audit_tree(source, destination)
    audit = load_audit_run(destination)
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    migrated_proposals = _read_jsonl(destination / "rounds" / "0" / "proposals.jsonl")
    migrated_evaluations = _read_jsonl(destination / "rounds" / "0" / "evaluations.jsonl")

    assert report.from_schema_version == "1.0"
    assert report.to_schema_version == "1.4"
    assert report.source_audit_hash != report.destination_audit_hash
    assert audit.manifest["schema_version"] == "1.4"
    assert audit.lineage[0]["schema_version"] == "1.4"
    assert source_manifest.get("schema_version") is None
    assert migrated_proposals[0]["schema_version"] == "1.4"
    assert migrated_proposals[0]["changed_surfaces"] == ["bootstrap"]
    assert migrated_proposals[0]["decision_reason"] == "migrated_no_reason_recorded"
    assert migrated_evaluations[0]["schema_version"] == "1.4"
    assert migrated_evaluations[0]["failure_category"] == "assertion-fail"
    assert "manifest.json" in report.changed_files
    assert "rounds/0/proposals.jsonl" in report.changed_files


def test_audit_migration_rejects_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "migrated"
    _write_synthetic_schema_10_audit(source)
    destination.mkdir()

    with pytest.raises(AuditMigrationError, match="destination already exists"):
        migrate_audit_tree(source, destination)


def test_audit_migration_rejects_current_or_downgrade_targets(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _write_synthetic_schema_10_audit(current)
    manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = "1.4"
    write_stable_json(current / "manifest.json", manifest)

    with pytest.raises(AuditMigrationError, match="already at target"):
        migrate_audit_tree(current, tmp_path / "same-target", target_schema_version="1.4")

    with pytest.raises(AuditMigrationError, match="upgrade-only"):
        migrate_audit_tree(current, tmp_path / "downgrade", target_schema_version="1.2")


def test_detect_audit_schema_version_rejects_malformed_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{", encoding="utf-8")

    with pytest.raises(AuditMigrationError, match="invalid JSON"):
        detect_audit_schema_version(tmp_path)


def test_audit_migrate_cli_outputs_structured_report(tmp_path: Path, capsys) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "migrated"
    _write_synthetic_schema_10_audit(source)

    code = main(["audit-migrate", str(source), "--out", str(destination)])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["migration"]["from_schema_version"] == "1.0"
    assert output["migration"]["to_schema_version"] == "1.4"
    assert output["migration"]["boundary"].startswith("release/operator migration copy")
    assert load_audit_run(destination).manifest["schema_version"] == "1.4"


def test_audit_migrate_cli_reports_errors_as_json(tmp_path: Path, capsys) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "migrated"
    _write_synthetic_schema_10_audit(source)
    destination.mkdir()

    code = main(["audit-migrate", str(source), "--out", str(destination)])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["ok"] is False
    assert output["reason"] == "audit-migration-error"


def _write_synthetic_schema_10_audit(root: Path) -> None:
    round_dir = root / "rounds" / "0"
    round_dir.mkdir(parents=True)
    write_stable_json(
        root / "manifest.json",
        {
            "protocol_hash": "legacy-protocol",
            "evaluation_repeats": 1,
            "seed": 0,
        },
    )
    write_stable_json(
        root / "lineage.json",
        [
            {
                "round": 0,
                "harness_before_hash": "h0",
                "harness_after_hash": "h1",
                "ops_applied": [],
                "reverse_ops": [],
                "accepted_proposal_ids": [],
            }
        ],
    )
    write_stable_json(round_dir / "harness_before.json", {})
    write_stable_json(round_dir / "harness_after.json", {})
    write_jsonl(
        round_dir / "proposals.jsonl",
        [
            {
                "id": "p0",
                "status": "rejected",
                "patch": {
                    "ops": [
                        {
                            "op": "AppendToSurface",
                            "surface": "bootstrap",
                            "payload": "legacy text",
                        }
                    ]
                },
            }
        ],
    )
    write_jsonl(
        round_dir / "evaluations.jsonl",
        [
            {
                "task_id": "held-in",
                "proposal_id": "p0",
                "arm": "candidate",
                "split": "held_in",
                "terminal_cause": "assertion-fail",
                "passed": False,
            }
        ],
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
